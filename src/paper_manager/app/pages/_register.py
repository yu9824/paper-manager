import io
from logging import DEBUG
from typing import Optional, Union

import streamlit as st
from crossref.restful import Works  # type: ignore[import-untyped]
from streamlit.runtime.uploaded_file_manager import UploadedFile

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


def _create_entry_from_doi_metadata(metadata: dict) -> Entry:
    """DOIメタデータからEntryオブジェクトを作成する。

    Parameters
    ----------
    metadata : dict
        CrossRef APIから取得したメタデータ

    Returns
    -------
    Entry
        作成された論文エントリ
    """
    return Entry(
        {
            "ENTRYTYPE": entrytype4doi(metadata["type"]),
            "title": metadata["title"][0],
            "author": " and ".join(
                [
                    author["given"] + " " + author["family"]
                    for author in metadata["author"]
                ]
            ),
            "journal": metadata["container-title"][0],
            "year": str(metadata["published"]["date-parts"][0][0]),
            "volume": metadata.get("volume", ""),
            "number": metadata.get("issue", ""),
            "pages": metadata.get("page", ""),
            "DOI": metadata["DOI"],
        }
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

        entries = load_bib(bibtexfile_or_buffer)

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
            works = Works()
            metadata: Optional[dict] = works.doi(doi)

        if not metadata:
            st.error("❌ 無効なDOI、またはメタデータの取得に失敗しました。")
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
    _logger.setLevel(DEBUG)

    main()
