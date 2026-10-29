import io
from collections.abc import Mapping
from typing import Any, Optional, Union

import streamlit as st
from crossref.restful import Works
from pybtex.exceptions import PybtexError
from requests.exceptions import RequestException
from streamlit.runtime.uploaded_file_manager import UploadedFile

from paper_manager._constants import (
    AUTHOR_SEPARATOR,
    COLNAME_AUTHOR,
    COLNAME_BOOKTITLE,
    COLNAME_DOI,
    COLNAME_JOURNAL,
    COLNAME_NUMBER,
    COLNAME_PAGES,
    COLNAME_PUBLISHER,
    COLNAME_TITLE,
    COLNAME_VOLUME,
    COLNAME_YEAR,
)
from paper_manager.app.components import (
    custom_entry,
    pdf_upload_form,
    register_entry_to_list,
)
from paper_manager.app.helper import (
    MAP_FIELDS,
    MAP_REQUIRED_FIELDS,
    config_page,
    entrytype4doi,
)
from paper_manager.app.helper._proxy import apply_proxy_to_environment
from paper_manager.bib import load_bib
from paper_manager.entry import Entry, PaperList
from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)


# Crossref の出版日の候補。年が数値のものを、この順に探して使う。
_PUBLICATION_DATE_FIELDS = (
    "issued",
    "published-print",
    "published-online",
    "published",
)

_DOI_NOT_FOUND_MESSAGE = "❌ 無効なDOI、またはメタデータの取得に失敗しました。"


def _as_text(value: Any) -> Optional[str]:
    """文字列または整数を、前後の空白を除いた文字列にする。

    Parameters
    ----------
    value : Any
        Crossref の応答の値。

    Returns
    -------
    Optional[str]
        空でない文字列。文字列と整数以外、または空（空白のみを含む）の
        ときは ``None``。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value.strip() or None
    return None


def _first_text(value: Any) -> Optional[str]:
    """文字列、または文字列のリストから、先頭の空でない文字列を得る。

    Crossref の ``title`` や ``container-title`` はリストで、空のことも
    ある。

    Parameters
    ----------
    value : Any
        Crossref の応答の値。

    Returns
    -------
    Optional[str]
        先頭の空でない文字列。なければ ``None``。
    """
    candidates = value if isinstance(value, (list, tuple)) else [value]
    return next(
        (text for text in map(_as_text, candidates) if text is not None),
        None,
    )


def _author_name(author: Any) -> Optional[str]:
    """Crossref の著者の要素から、著者名を作る。

    ``given family`` → ``family`` → ``name``（団体著者）→ ``given`` の順に
    作れるものを使う。

    Parameters
    ----------
    author : Any
        ``author`` の 1 要素。

    Returns
    -------
    Optional[str]
        著者名。名前を作れない要素は ``None``。
    """
    if not isinstance(author, Mapping):
        return None
    given = _as_text(author.get("given"))
    family = _as_text(author.get("family"))
    if given and family:
        return f"{given} {family}"
    return family or _as_text(author.get("name")) or given


def _author_names(authors: Any) -> Optional[str]:
    """Crossref の ``author`` から、`` and `` でつないだ著者名を作る。

    Parameters
    ----------
    authors : Any
        ``author``（著者の要素のリスト）。

    Returns
    -------
    Optional[str]
        著者名を `` and `` でつないだ文字列。名前を作れる著者が 1 人も
        いなければ ``None``。
    """
    if not isinstance(authors, (list, tuple)):
        return None
    names = [name for name in map(_author_name, authors) if name is not None]
    return AUTHOR_SEPARATOR.join(names) or None


def _publication_year(metadata: Mapping[str, Any]) -> Optional[str]:
    """出版日の候補を順に探して、最初に見つかった年を得る。

    Parameters
    ----------
    metadata : Mapping[str, Any]
        Crossref から取得したメタデータ。

    Returns
    -------
    Optional[str]
        年。どの候補にも年がなければ ``None``（``[[null]]`` は年がない
        ものとして扱う）。
    """
    for field in _PUBLICATION_DATE_FIELDS:
        date = metadata.get(field)
        parts = date.get("date-parts") if isinstance(date, Mapping) else None
        first = (
            parts[0] if isinstance(parts, (list, tuple)) and parts else None
        )
        year = first[0] if isinstance(first, (list, tuple)) and first else None
        if isinstance(year, int) and not isinstance(year, bool):
            return str(year)
    return None


def _create_entry_from_doi_metadata(metadata: Mapping[str, Any]) -> Entry:
    """DOIメタデータからEntryオブジェクトを作成する。

    応答の項目が欠けていても、空のリストや空の文字列でも、例外にしない。
    値のない項目は Entry に入れない（空の値は必須項目の判定を通って
    しまうため）。必須項目が足りるかどうかは、呼び出し側の検証が判定する。

    エントリタイプごとに、``container-title`` は次のように使う。

    - 書籍（``book``）: 出版社（``publisher``）を取り込む。``container-title``
      はシリーズ名なので、掲載誌名にしない
    - 会議録の論文（``inproceedings``）: ``container-title`` を会議録名
      （``booktitle``）にする（なければ ``event.name``）。出版社も取り込む
    - それ以外（``article``、``misc``）: ``container-title`` を掲載誌名
      （``journal``）にする

    Parameters
    ----------
    metadata : Mapping[str, Any]
        CrossRef APIから取得したメタデータ

    Returns
    -------
    Entry
        作成された論文エントリ
    """
    crossref_type = metadata.get("type")
    entry_type = entrytype4doi(
        crossref_type if isinstance(crossref_type, str) else ""
    )
    container_title = _first_text(metadata.get("container-title"))
    event = metadata.get("event")
    publisher = _as_text(metadata.get("publisher"))

    fields: dict[str, Optional[str]] = {
        COLNAME_TITLE: _first_text(metadata.get("title")),
        COLNAME_AUTHOR: _author_names(metadata.get("author")),
        COLNAME_YEAR: _publication_year(metadata),
        COLNAME_VOLUME: _as_text(metadata.get("volume")),
        COLNAME_NUMBER: _as_text(metadata.get("issue")),
        COLNAME_PAGES: _as_text(metadata.get("page")),
        COLNAME_DOI: _as_text(metadata.get("DOI")),
    }
    if entry_type == "book":
        fields[COLNAME_PUBLISHER] = publisher
    elif entry_type == "inproceedings":
        fields[COLNAME_BOOKTITLE] = container_title or _as_text(
            event.get("name") if isinstance(event, Mapping) else None
        )
        fields[COLNAME_PUBLISHER] = publisher
    else:
        fields[COLNAME_JOURNAL] = container_title

    return Entry(
        {
            "ENTRYTYPE": entry_type,
            **{name: value for name, value in fields.items() if value},
        }
    )


def _fetch_doi_metadata(doi: str) -> tuple[Optional[dict], Optional[str]]:
    """DOIのメタデータを Crossref から取得する。

    通信エラー（接続できない、タイムアウト、JSON でない応答など。
    ``requests.exceptions.RequestException`` の派生）と、応答に
    ``message`` がない（``KeyError``）ときは、例外にせず、メッセージを返す。
    ほかの例外は想定外のバグなので、握りつぶさずそのまま送出する。

    Parameters
    ----------
    doi : str
        問い合わせるDOI

    Returns
    -------
    tuple[Optional[dict], Optional[str]]
        成功したときは ``(メタデータ, None)``、失敗したときは
        ``(None, 画面に出すメッセージ)``。失敗はログにも ERROR で出す。
        通信エラーのメッセージは、例外の型名だけを含む（例外の文面は
        入力やプロキシの URL を含みうるので、画面に出さない）。
    """
    try:
        metadata = Works().doi(doi)
    except (RequestException, KeyError) as e:
        reason = type(e).__name__
        _logger.error("Failed to fetch DOI metadata (%s): %s", reason, e)
        return (
            None,
            "❌ DOIのメタデータを取得できませんでした"
            f"（通信エラー: {reason}）。"
            "ネットワークの接続を確認して、もう一度お試しください。",
        )

    if not isinstance(metadata, dict) or _as_text(metadata.get("DOI")) is None:
        _logger.error("No usable Crossref metadata for the DOI: %r", doi)
        return None, _DOI_NOT_FOUND_MESSAGE
    return metadata, None


def _load_bib_entries(
    bibtexfile_or_buffer: Union[io.StringIO, io.BytesIO],
) -> tuple[Optional[Mapping[str, Mapping[str, str]]], Optional[str]]:
    """BibTeX を読み込み、入力の誤りは画面に出すメッセージとして返す。

    構文として正しくない BibTeX（pybtex の ``PybtexError``。構文エラー、
    同じ引用キー・同じ項目の重複、読めない人名など）と、UTF-8 として読めない
    ファイル（``UnicodeDecodeError``）は、例外にせず、メッセージを返す。
    ほかの例外は想定外のバグなので、握りつぶさずそのまま送出する。

    Parameters
    ----------
    bibtexfile_or_buffer : Union[io.StringIO, io.BytesIO]
        BibTeX のテキストまたはアップロードされたファイル

    Returns
    -------
    tuple[Optional[Mapping[str, Mapping[str, str]]], Optional[str]]
        成功したときは ``(エントリの辞書, None)``、入力の誤りのときは
        ``(None, 画面に出すメッセージ)``。失敗はログにも ERROR で出す。
    """
    try:
        return load_bib(bibtexfile_or_buffer), None
    except PybtexError as e:
        reason = str(e) or type(e).__name__
        _logger.error("Invalid BibTeX: %s", reason)
        return None, f"❌ BibTeX の構文が正しくありません: {reason}"
    except UnicodeDecodeError as e:
        _logger.error("Undecodable BibTeX file: %s", e)
        return (
            None,
            "❌ BibTeX ファイルの文字コードを読めません"
            "（UTF-8 で保存してください）。",
        )


def _render_bib_form() -> tuple[bool, Optional[Entry], list[UploadedFile]]:
    """BIB登録フォームを表示する。

    Returns
    -------
    tuple[bool, Optional[Entry], list[UploadedFile]]
        (送信成功フラグ, 作成されたEntry, PDFファイルリスト)
    """
    st.markdown("#### 📄 BibTeX形式から登録")

    with st.form("bib_form", clear_on_submit=True):
        tab_from_bib_with_text, tab_from_bib_with_file = st.tabs(
            ("📝 テキスト入力", "📎 ファイルアップロード")
        )
        with tab_from_bib_with_text:
            bib_text_input = st.text_area(
                "BibTeX形式のテキストを入力",
                height=200,
                help="BibTeX形式のエントリを貼り付けてください",
                placeholder="@article{key,\n  title={Example Title},\n  author={Author Name},\n  ...\n}",
            )
        with tab_from_bib_with_file:
            uploaded_file_bib = st.file_uploader(
                "BibTeXファイルをアップロード",
                type="bib",
                accept_multiple_files=False,
                help=".bib形式のファイルを選択してください",
            )

        st.divider()
        st.markdown("**PDFファイル（オプション）**")
        uploaded_files = pdf_upload_form(accept_multiple=True)

        submitted = st.form_submit_button(
            "📤 登録",
            type="primary",
            use_container_width=True,
        )

        if not submitted:
            return False, None, []

        if not (uploaded_file_bib or bib_text_input):
            st.error("❌ BibTeXデータが入力されていません。")
            return False, None, []

        bibtexfile_or_buffer: Union[io.StringIO, io.BytesIO] = (
            uploaded_file_bib
            if uploaded_file_bib
            else io.StringIO(bib_text_input)
        )
        assert isinstance(bibtexfile_or_buffer, (io.StringIO, io.BytesIO))

        entries, error_message = _load_bib_entries(bibtexfile_or_buffer)
        if entries is None:
            st.error(error_message)
            return False, None, []

        if len(entries) > 1:
            st.error(
                f"❌ エントリは1つだけ登録できます。（{len(entries)} 個のエントリが含まれています）"
            )
            return False, None, []

        if len(entries) == 0:
            st.error("❌ 有効なエントリが見つかりませんでした。")
            return False, None, []

        entry = Entry(entries[tuple(entries.keys())[0]])
        return True, entry, uploaded_files


def _render_doi_form() -> tuple[bool, Optional[Entry], list[UploadedFile]]:
    """DOI登録フォームを表示する。

    Returns
    -------
    tuple[bool, Optional[Entry], list[UploadedFile]]
        (送信成功フラグ, 作成されたEntry, PDFファイルリスト)
    """
    st.markdown("#### 🔗 DOIから自動取得")

    with st.form("doi_form", clear_on_submit=True):
        doi = st.text_input(
            "DOI",
            help="DOIを入力してください（例: 10.1107/S0567739476001551 または doi.org/10.1107/S0567739476001551）",
            placeholder="10.1107/S0567739476001551",
        )

        st.divider()
        st.markdown("**PDFファイル（オプション）**")
        uploaded_files = pdf_upload_form(accept_multiple=True)

        submitted = st.form_submit_button(
            "📤 登録",
            type="primary",
            use_container_width=True,
        )

        if not submitted:
            return False, None, []

        if not doi:
            st.error("❌ DOIが入力されていません。")
            return False, None, []

        # プロキシ設定を適用
        apply_proxy_to_environment()

        with st.spinner("🔍 DOIからメタデータを取得中..."):
            metadata, error_message = _fetch_doi_metadata(doi)

        if metadata is None:
            st.error(error_message)
            return False, None, []

        entry = _create_entry_from_doi_metadata(metadata)
        return True, entry, uploaded_files


def _render_custom_form(
    already_submitted: bool,
) -> tuple[bool, Optional[Entry], list[UploadedFile]]:
    """カスタム登録フォームを表示する。

    Parameters
    ----------
    already_submitted : bool
        他のフォームで既に送信されたかどうか

    Returns
    -------
    tuple[bool, Optional[Entry], list[UploadedFile]]
        (送信成功フラグ, 作成されたEntry, PDFファイルリスト)
    """
    st.markdown("#### ✏️ 手動入力")

    entry_type = st.selectbox(
        "エントリタイプを選択",
        options=tuple(MAP_FIELDS.keys()),
        help="論文の種類を選択してください",
    )

    if already_submitted:
        return False, None, []

    assert entry_type is not None

    with st.form("custom_form", clear_on_submit=True):
        st.markdown("**論文情報を入力**")
        entry = custom_entry(Entry(dict(ENTRYTYPE=entry_type)))

        st.divider()
        st.markdown("**PDFファイル（オプション）**")
        uploaded_files = pdf_upload_form(accept_multiple=True)

        submitted = st.form_submit_button(
            "📤 登録",
            type="primary",
            use_container_width=True,
        )

        if not submitted:
            return False, None, []

        # 必須フィールドのチェック
        if not (MAP_REQUIRED_FIELDS[entry_type] <= set(entry.keys())):
            missing_fields = MAP_REQUIRED_FIELDS[entry_type] - set(
                entry.keys()
            )
            st.error(
                f"❌ 必須フィールドが不足しています: {', '.join(missing_fields)}"
            )
            return False, None, []

        # セッションステートのクリア
        for field in MAP_FIELDS[entry_type]:
            _ = st.session_state.pop(field, None)

        return True, entry, uploaded_files


def _validate_required_fields(entry: Entry) -> bool:
    """必須フィールドをチェックし、不足していればエラーを表示する。"""
    entry_type = entry["ENTRYTYPE"]
    missing_fields = MAP_REQUIRED_FIELDS[entry_type] - set(entry.keys())

    if missing_fields:
        st.error(
            "('{}') is/are necessary.".format("', '".join(missing_fields))
        )
        return False

    return True


@config_page
def main() -> None:
    """論文登録ページのメインスクリプト。

    BIB、DOI、カスタムの3つの方法で論文を登録できる。
    """
    st.header("📝 論文登録")
    _logger.debug("Register page Start")

    paper_list = PaperList.from_session_state()

    st.markdown(
        """
        以下の3つの方法で論文を登録できます：

        - **BIB**: BibTeX形式のテキストまたはファイルから登録
        - **DOI**: DOIから自動的にメタデータを取得して登録
        - **CUSTOM**: 手動で情報を入力して登録
        """
    )

    st.divider()

    # タブの作成
    tab_from_bib, tab_from_doi, tab_custom_form = st.tabs(
        ("📄 BIB", "🔗 DOI", "✏️ CUSTOM")
    )

    # BIB登録
    with tab_from_bib:
        submitted_bib, entry_bib, uploaded_files_bib = _render_bib_form()

    # DOI登録
    with tab_from_doi:
        submitted_doi, entry_doi, uploaded_files_doi = _render_doi_form()

    # カスタム登録
    with tab_custom_form:
        submitted_custom, entry_custom, uploaded_files_custom = (
            _render_custom_form(
                already_submitted=(submitted_bib or submitted_doi),
            )
        )

    # 登録処理（共通関数を使用）
    if submitted_bib and entry_bib is not None:
        if _validate_required_fields(entry_bib) and register_entry_to_list(
            paper_list, entry_bib, uploaded_files_bib
        ):
            st.balloons()
            if st.button("🔄 クリア", use_container_width=True):
                st.rerun()
    elif submitted_doi and entry_doi is not None:
        if _validate_required_fields(entry_doi) and register_entry_to_list(
            paper_list, entry_doi, uploaded_files_doi
        ):
            st.balloons()
            if st.button("🔄 クリア", use_container_width=True):
                st.rerun()
    elif submitted_custom and entry_custom is not None:
        if _validate_required_fields(entry_custom) and register_entry_to_list(
            paper_list, entry_custom, uploaded_files_custom
        ):
            st.balloons()
            if st.button("🔄 クリア", use_container_width=True):
                st.rerun()

    _logger.debug("Register page End")


if __name__ == "__main__":
    main()
