import re
import xml.dom.minidom
import zipfile
from collections.abc import Sequence
from copy import deepcopy
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Optional, Union

import pandas as pd
import streamlit as st
from bib2xml import bib2xml
from bibtexparser.bibdatabase import (
    BibDatabase,
)
from bibtexparser.bwriter import BibTexWriter
from pybtex.database.input import bibtex
from pybtex.style.formatting.plain import Style
from streamlit_pdf_viewer import pdf_viewer

from paper_manager._constants import (
    AUTHOR_SEPARATOR,
    COLNAME_AUTHOR,
    COLNAME_HAS_PDF,
    COLNAME_JOURNAL,
    COLNAME_TAGS,
    COLNAME_TITLE,
    COLNAME_YEAR,
    COLNAMES_DISPLAY,
    ENCODING,
    TAG_SEPARATOR,
)
from paper_manager.app.components import (
    custom_entry,
    delete_pdfs,
    pdf_upload_form,
    save_paper_list,
    update_entry_in_list,
)
from paper_manager.app.components._common import _report_failure
from paper_manager.app.helper import (
    KEY_NOTICES,
    MAP_REQUIRED_FIELDS,
    config_page,
)
from paper_manager.app.helper._orphan_pdf import (
    delete_orphaned_pdfs,
    find_closest_entry_for_orphaned_pdf,
    find_orphaned_pdfs,
)
from paper_manager.entry import Entry, PaperList, unique_citation_keys
from paper_manager.helper import split, to_display_path, to_fs_path
from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)


def _render_paper_table(paper_list: PaperList) -> list[str]:
    """論文一覧テーブルを表示し、選択された論文のキーを返す。

    Parameters
    ----------
    paper_list : PaperList
        表示する論文リスト

    Returns
    -------
    list[str]
        選択された論文のキーのリスト。選択されていない場合は空リスト。
    """
    _df_paper_list = pd.DataFrame.from_dict(dict(paper_list), orient="index")
    # fill missing columns
    for _col in set(COLNAMES_DISPLAY) - set(_df_paper_list.columns):
        _df_paper_list.loc[:, _col] = ""
    _df_paper_list.fillna("", inplace=True)

    _df_paper_list.loc[:, COLNAME_HAS_PDF] = pd.Series(
        {
            _key: "o" if _entry.get_pdf_count() else "x"
            for _key, _entry in paper_list.items()
        },
        dtype=str,
    )
    _df_paper_list.loc[:, COLNAME_TAGS].apply(split, args=(TAG_SEPARATOR,))
    _df_paper_list.loc[:, COLNAME_AUTHOR] = _df_paper_list.loc[
        :, COLNAME_AUTHOR
    ].apply(split, args=(AUTHOR_SEPARATOR,))
    column_config = {
        COLNAME_HAS_PDF: st.column_config.MultiselectColumn(
            options=("o", "x"), color=("red", "blue")
        ),
        COLNAME_TAGS: st.column_config.MultiselectColumn(color="auto"),
        COLNAME_AUTHOR: st.column_config.MultiselectColumn(color="auto"),
    }

    paper_selected = st.dataframe(
        _df_paper_list,
        column_order=COLNAMES_DISPLAY,
        column_config=column_config,
        hide_index=True,
        selection_mode="multi-row",
        on_select="rerun",
    )

    # 行が選択されていたら
    if index_list_selected := paper_selected["selection"]["rows"]:
        keys = tuple(paper_list.keys())
        return [keys[i] for i in index_list_selected]
    return []


def _render_link(entry: Entry) -> None:
    """論文のURL/DOIリンクを表示する。

    Parameters
    ----------
    entry : Entry
        表示する論文エントリ
    """
    if "url" in entry:
        st.markdown("Link: [{0}]({0})".format(entry["url"]))
    elif "DOI" in entry and (
        result_doi := re.match(
            r"(https?://.*doi\.org/)?(.+)",
            entry["DOI"],
        )
    ):
        st.markdown(
            "Link: [{0}](https://doi.org/{0})".format(result_doi.group(2))
        )


def _with_citation_keys(entries: Sequence[Entry]) -> list[Entry]:
    """引用キーを整えた論文エントリの複製を返す。

    BibTeXの引用キーとして読めない文字（空白・``,``・``{``・``}``）を含む
    既存の論文でも書き出せるよう、複製の ``ID`` を ``unique_citation_keys``
    で整えたキー（大文字小文字を区別せずに重複しない）に置き換える。
    論文リストのエントリと ``list.json`` の ``ID`` は変えない。

    Parameters
    ----------
    entries : Sequence[Entry]
        書き出す論文エントリ。``ID`` を持つ

    Returns
    -------
    list[Entry]
        ``entries`` と同じ長さ・順序の複製。``ID`` だけが整えたキーに
        なっている
    """
    keys = unique_citation_keys([entry["ID"] for entry in entries])
    copies = []
    for entry, key in zip(entries, keys):
        copied = Entry(entry)
        copied["ID"] = key
        copies.append(copied)
    return copies


def _to_bibtex(
    entries: Sequence[Entry],
) -> tuple[str, "bibtex.BibliographyData"]:
    """論文エントリをBibTeXのテキストと、パースしたデータにする。

    書き出す前に、引用キーを ``_with_citation_keys`` で整える。

    Parameters
    ----------
    entries : Sequence[Entry]
        書き出す論文エントリ

    Returns
    -------
    tuple[str, bibtex.BibliographyData]
        BibTeXテキストとパースされたBibliographyDataのタプル
    """
    bib_database = BibDatabase()
    bib_database.entries = _with_citation_keys(entries)

    bib_writer = BibTexWriter()
    bib_text = bib_writer.write(bib_database)

    bib_parser = bibtex.Parser()
    return bib_text, bib_parser.parse_string(bib_text)


def _render_citation(entry: Entry) -> tuple[str, "bibtex.BibliographyData"]:
    """論文の引用情報を表示する。

    引用キーは ``_to_bibtex`` で整えて書き出す（論文リストの ``ID`` は
    変えない）。

    Parameters
    ----------
    entry : Entry
        表示する論文エントリ

    Returns
    -------
    tuple[str, bibtex.BibliographyData]
        BibTeXテキストとパースされたBibliographyDataのタプル
    """
    st.subheader("📝 引用情報")

    # bibtex
    bib_text, bibdata = _to_bibtex([entry])

    _citation_key = tuple(bibdata.entries.keys())[0]
    formatted_entry = Style().format_entry(
        _citation_key, bibdata.entries[_citation_key]
    )

    with st.expander("🌐 HTML形式の引用", expanded=True):
        with st.container(border=True):
            st.html(formatted_entry.text.render_as("html"))

    with st.expander("📄 プレーンテキスト形式の引用"):
        st.code(
            formatted_entry.text.render_as("text"),
            language="plaintext",
            wrap_lines=True,
        )

    return bib_text, bibdata


def _read_pdf(pdf_file: Path) -> Optional[bytes]:
    """PDFファイルを読み込む。失敗したら、画面とログにエラーを出す。

    Parameters
    ----------
    pdf_file : Path
        読み込むPDFファイル。変換後のパスでも変換前のパスでもよい
        （ファイル操作は変換後のパスで行う）

    Returns
    -------
    Optional[bytes]
        PDFの内容。読み込めなかった場合はNone。画面とログには、何をしようと
        して失敗したかと、表示用のパス（``to_display_path``）、理由を出す
    """
    try:
        return to_fs_path(pdf_file).read_bytes()
    except OSError as e:
        _report_failure("Failed to read PDF file", pdf_file, e)
        return None


def _download_filename(entry: Entry, pdf_file: Path, pdf_count: int) -> str:
    """PDFをダウンロードするときのファイル名を決める。

    PDFが1件だけで、名前が番号（``NN.pdf``）なら、``<フォルダ名>_NN.pdf``
    にする。複数ある場合と、1件だけでも名前が番号でない場合（フォルダへ
    移せなかった旧形式のPDFなど）は、PDFの名前のままにする。

    Parameters
    ----------
    entry : Entry
        論文エントリ
    pdf_file : Path
        ダウンロードするPDFファイル
    pdf_count : int
        論文のPDFの件数

    Returns
    -------
    str
        ダウンロードするファイル名
    """
    if pdf_count == 1 and re.fullmatch(r"[0-9]+", pdf_file.stem):
        # ファイル名からインデックスを抽出（例: "00.pdf" -> 0）
        return f"{entry.pdf_dir_name}_{int(pdf_file.stem):02d}.pdf"
    return pdf_file.name


def _render_pdf_viewer(pdf_files: list[Path], entry: Entry) -> None:
    """複数のPDFファイルを表示する。

    選択したPDFを読み込めなかったときは、失敗したことと表示用のパスを
    画面に出し、ビューアとダウンロードボタンは表示しない。

    Parameters
    ----------
    pdf_files : list[Path]
        表示するPDFファイルのリスト
    entry : Entry
        論文エントリ
    """
    if not pdf_files:
        st.info("No PDF files available.")
        return

    st.subheader(f"PDF Files ({len(pdf_files)})")

    # PDFファイルの選択
    pdf_options = {f.name: f for f in pdf_files}
    selected_pdf_name = st.selectbox(
        "Select PDF to view",
        options=list(pdf_options.keys()),
        key="pdf_selector",
    )

    if selected_pdf_name:
        selected_pdf = pdf_options[selected_pdf_name]

        pdf_contents = _read_pdf(selected_pdf)
        if pdf_contents is None:
            return

        # ファイル名の決定
        download_filename = _download_filename(
            entry, selected_pdf, len(pdf_files)
        )

        # ダウンロードボタン
        st.download_button(
            "",
            icon=":material/download:",
            data=pdf_contents,
            file_name=download_filename,
            mime="application/pdf",
            key=f"download_{selected_pdf.name}",
            help="Download",
            width=100,
        )

        # PDFビューア
        pdf_viewer(pdf_contents, width=700, height=1000)


def _add_pdf_to_zip(
    zip_file: zipfile.ZipFile, pdf_file: Path, arcname: str
) -> bool:
    """PDFファイルをzipに追加する。読み込めなければ、zipに入れない。

    zipへ書き込む前にファイルの情報と内容を読み込むので、読み込みに失敗した
    ファイルは、途中までの内容も含めて、zipに残らない。

    Parameters
    ----------
    zip_file : zipfile.ZipFile
        追加先のzip
    pdf_file : Path
        追加するPDFファイル。変換後のパスでも変換前のパスでもよい
    arcname : str
        zipの中での名前

    Returns
    -------
    bool
        追加した場合True。読み込めなかった場合はFalse（画面とログに、
        何をしようとして失敗したかと表示用のパス、理由を出す）
    """
    source = to_fs_path(pdf_file)
    try:
        info = zipfile.ZipInfo.from_file(source, arcname)
        contents = source.read_bytes()
    except OSError as e:
        _report_failure("Failed to add PDF file to ZIP file", pdf_file, e)
        return False
    zip_file.writestr(info, contents, compress_type=zipfile.ZIP_DEFLATED)
    return True


def _zip_pdfs(entries: Sequence[Entry]) -> tuple[bytes, int]:
    """複数の論文のPDFを、階層構造を保ってzipにまとめる。

    各論文のPDFは ``<pdf_dir_name>/<PDFの名前>`` に入れる。読み込めない
    PDFは、エラーを出してzipに入れず、残りのPDFを続ける。

    Parameters
    ----------
    entries : Sequence[Entry]
        論文エントリのシーケンス

    Returns
    -------
    tuple[bytes, int]
        zipの内容と、zipに入れたPDFの件数
    """
    zip_buffer = BytesIO()
    added = 0
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for entry in entries:
            # 各エントリのpdf_dir_nameをディレクトリ名として使用
            dir_name = entry.pdf_dir_name
            for pdf_file in entry.get_pdf_files():
                # 階層構造を保つ: dir_name/pdf_filename
                if _add_pdf_to_zip(
                    zip_file, pdf_file, f"{dir_name}/{pdf_file.name}"
                ):
                    added += 1
    return zip_buffer.getvalue(), added


def _render_export(
    entries: Sequence[Entry],
    pdf_files: Union[Sequence[Path], None] = None,
) -> None:
    """エクスポート機能を表示する。

    Parameters
    ----------
    entries : Sequence[Entry]
        論文エントリのシーケンス
    pdf_files : Sequence[Path] or None, optional
        PDFファイルのシーケンス（単一エントリの場合のみ使用）。デフォルトはNone。
    """
    if not entries:
        st.info("No entries selected.")
        return

    is_multi = len(entries) > 1

    # サブヘッダーの設定
    st.subheader("💾 エクスポート")

    # BibTeX テキストを生成（引用キーを整えた複製を書き出す）
    bib_text, bibdata = _to_bibtex(entries)

    # エクスポートオプション
    options_file_ext: list[str] = ["bib", "xml"]
    # 複数選択時はPDFをzipでダウンロード、単一選択時はPDFビューアを表示
    has_pdf_files = (not is_multi and pdf_files) or (
        is_multi and any(entry.get_pdf_count() > 0 for entry in entries)
    )
    if has_pdf_files:
        options_file_ext.insert(0, "pdf")

    radio_key = "ext_multi" if is_multi else "ext"
    ext = st.radio(
        "エクスポート形式を選択",
        options=options_file_ext,
        horizontal=True,
        key=radio_key,
    )

    if is_multi:
        now_str = datetime.now().strftime("%y%m%d_%H%M")
        filepath_base_export = Path(f"selected_{len(entries)}_{now_str}")
    else:
        filepath_base_export = Path(f"{entries[0].pdf_dir_name}")

    if ext == "pdf":
        if is_multi:
            # 複数選択時: 階層構造を保ってzipファイルを作成
            zip_bytes, added = _zip_pdfs(entries)
            if added:
                st.download_button(
                    "📦 ZIPファイルをダウンロード",
                    data=zip_bytes,
                    mime="application/zip",
                    icon=":material/download:",
                    file_name=str(filepath_base_export.with_suffix(".zip")),
                    use_container_width=True,
                )
            else:
                st.info("No PDF files available.")
        else:
            # 単一選択時: 既存のPDFビューアを表示
            if pdf_files is None:
                return
            _render_pdf_viewer(list(pdf_files), entries[0])

    elif ext == "bib":
        st.download_button(
            "📄 BibTeXファイルをダウンロード",
            bib_text,
            file_name=str(filepath_base_export.with_suffix(".bib")),
            icon=":material/download:",
            use_container_width=True,
        )
        with st.expander("📋 BibTeXプレビュー"):
            st.code(bib_text, language="latex")

    elif ext == "xml":
        xml_str = bib2xml(bibdata)
        st.download_button(
            "📄 XMLファイルをダウンロード",
            data=xml_str.encode(ENCODING),
            mime="application/xml",
            file_name=str(filepath_base_export.with_suffix(".xml")),
            icon=":material/download:",
            use_container_width=True,
        )
        with st.expander("📋 XMLプレビュー"):
            st.code(
                xml.dom.minidom.parseString(xml_str).toprettyxml(indent="  "),
                language="xml",
            )


def _report_orphan_failures(
    orphaned_pdfs: Sequence[Path],
    failures: Sequence[tuple[Path, str]],
    deleted: int,
) -> None:
    """孤立PDFの削除に失敗したパスと理由を、画面にエラーで出す。

    ``failures`` には、削除を依頼したパスそのもの（PDFの削除に失敗した）
    と、PDFは削除できたが空になった親フォルダを削除できなかったときの
    そのフォルダのパスの、どちらも入る。後者を「PDFの削除に失敗した」と
    表示しないよう、依頼したパスかどうかで分け、文言を分けて出す。
    パスは ``to_display_path`` で変換後のprefixを外して出す（ログには
    ``delete_orphaned_pdfs`` が失敗のたびに出している）。

    Parameters
    ----------
    orphaned_pdfs : Sequence[Path]
        削除を依頼した孤立PDFのパス
    failures : Sequence[tuple[Path, str]]
        ``delete_orphaned_pdfs`` が返した、失敗したパスと理由
    deleted : int
        削除に成功したPDFの件数
    """
    requested = set(orphaned_pdfs)
    pdf_failures = [f for f in failures if f[0] in requested]
    folder_failures = [f for f in failures if f[0] not in requested]

    def lines(items: Sequence[tuple[Path, str]]) -> str:
        return "\n".join(
            f"- `{to_display_path(path)}`: {reason}" for path, reason in items
        )

    if pdf_failures:
        st.error(
            f"❌ {len(pdf_failures)}個の孤立PDFファイルを削除できませんでした"
            f"（{deleted}個は削除しました）。\n\n{lines(pdf_failures)}"
        )
    if folder_failures:
        st.error(
            "❌ 孤立PDFファイルは削除しましたが、空になったフォルダを"
            f"{len(folder_failures)}個削除できませんでした"
            f"（削除したPDF: {deleted}個）。\n\n{lines(folder_failures)}"
        )


def _delete_entry(
    paper_list: PaperList,
    keys_selected: Sequence[str],
    flag_delete_pdf: bool,
) -> None:
    """論文エントリを削除する（単一または複数）。

    Parameters
    ----------
    paper_list : PaperList
        論文リスト
    keys_selected : Sequence[str]
        削除する論文のキーのシーケンス
    flag_delete_pdf : bool
        PDFファイルも削除するかどうか
    """
    is_multi = len(keys_selected) > 1

    if is_multi:
        _logger.debug(
            "Delete selected entries: %s (delete_pdf=%s)",
            list(keys_selected),
            flag_delete_pdf,
        )
    else:
        _logger.debug("push delete button")

    for key in keys_selected:
        if key not in paper_list:
            continue
        entry = paper_list[key]
        if flag_delete_pdf:
            delete_pdfs(entry)
        del paper_list[key]

    # 退避できずに保存しなかった場合は、再描画しない (エラーを画面に残す)
    if save_paper_list(paper_list):
        st.rerun()


@config_page
def main() -> None:
    """論文リストページのメインスクリプト。

    論文一覧の表示、選択した論文の詳細表示、編集・削除機能を提供する。
    """
    st.header("📚 論文リスト")
    _logger.debug("List page Start")

    paper_list = PaperList.from_session_state()

    if not paper_list:
        st.info(
            "📝 論文が登録されていません。「登録」ページから論文を追加してください。"
        )
        _logger.debug("List page End")
        return

    # 統計情報を表示
    total_papers = len(paper_list)
    total_pdfs = sum(entry.get_pdf_count() for entry in paper_list.values())
    papers_with_pdf = sum(
        1 for entry in paper_list.values() if entry.get_pdf_count() > 0
    )
    orphaned_pdfs = find_orphaned_pdfs(paper_list)
    orphaned_count = len(orphaned_pdfs)

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("📄 総論文数", total_papers)
    with col2:
        st.metric("📎 PDFファイル数", total_pdfs)
    with col3:
        st.metric("✅ PDFあり", papers_with_pdf)
    with col4:
        st.metric("❌ PDFなし", total_papers - papers_with_pdf)
    with col5:
        st.metric("⚠️ 孤立PDF", orphaned_count)

    # 孤立したPDFの警告と削除機能
    if orphaned_count > 0:
        st.warning(
            f"⚠️ **{orphaned_count}個の孤立したPDFファイルが見つかりました。**\n\n"
            "これらのPDFファイルは論文リストに紐づいていません。"
        )

        with st.expander("🔍 孤立したPDFファイルの詳細", expanded=False):
            for i, pdf_path in enumerate(orphaned_pdfs, 1):
                # 最も近いエントリを検索
                closest_match = find_closest_entry_for_orphaned_pdf(
                    pdf_path, paper_list
                )

                # PDFファイルのパスを表示（変換後のprefixを外したパスで計算）
                pdf_display = Path(to_display_path(pdf_path))
                pdf_display_path = pdf_display.relative_to(
                    pdf_display.parent.parent
                )
                if closest_match:
                    entry, similarity = closest_match
                    entry_title = entry.get(COLNAME_TITLE, "N/A")
                    entry_author = entry.get(COLNAME_AUTHOR, "N/A")
                    entry_year = entry.get(COLNAME_YEAR, "N/A")
                    similarity_percent = int(similarity * 100)

                    st.markdown(
                        f"**{i}. {pdf_display_path}**\n\n"
                        f"📌 最も近いエントリ（類似度: {similarity_percent}%）:\n"
                        f"- **タイトル**: {entry_title}\n"
                        f"- **著者**: {entry_author}\n"
                        f"- **年**: {entry_year}\n"
                        f"- **pdf_dir_name**: `{entry.pdf_dir_name}`"
                    )
                else:
                    st.text(
                        f"{i}. {pdf_display_path} (類似するエントリが見つかりませんでした)"
                    )

            if st.button(
                "🗑️ すべての孤立PDFを削除",
                type="primary",
                key="delete_orphaned",
                use_container_width=True,
            ):
                failures: list[tuple[Path, str]] = []
                deleted = delete_orphaned_pdfs(orphaned_pdfs, failures)
                if failures:
                    # 再描画するとエラーが消えるので、再描画せずに残す
                    _report_orphan_failures(orphaned_pdfs, failures, deleted)
                else:
                    st.success(
                        f"✅ {deleted}個の孤立PDFファイルを削除しました。"
                    )
                    st.rerun()

    st.divider()

    # 論文一覧テーブルの表示
    st.subheader("論文一覧")
    keys_selected = _render_paper_table(paper_list)

    if not keys_selected:
        _logger.debug("List page End")
        return

    # 単一行選択時: 既存の挙動（編集・削除・引用・エクスポート）を維持
    if len(keys_selected) == 1:
        key_selected = keys_selected[0]
        entry = paper_list[key_selected]
        pdf_files = entry.get_pdf_files()

        st.divider()
        st.subheader("📖 論文詳細")

        # 論文情報をカード形式で表示
        with st.container(border=True):
            col_title, col_year = st.columns([3, 1])
            with col_title:
                st.markdown(f"### {entry.get(COLNAME_TITLE, 'N/A')}")
            with col_year:
                st.markdown(f"**Year:** {entry.get(COLNAME_YEAR, 'N/A')}")

            if entry.get(COLNAME_AUTHOR):
                st.markdown(f"**著者:** {entry[COLNAME_AUTHOR]}")

            if entry.get(COLNAME_JOURNAL):
                st.markdown(f"**ジャーナル:** {entry[COLNAME_JOURNAL]}")

            if entry.get(COLNAME_TAGS):
                tags = split(entry[COLNAME_TAGS], TAG_SEPARATOR)
                if tags:
                    st.markdown(
                        "**タグ:** " + ", ".join(f"`{tag}`" for tag in tags)
                    )

        # リンクの表示
        _render_link(entry)

        # アクションボタン
        st.markdown("#### アクション")
        col_edit, col_delete, col_export = st.columns(3)

        # PDF削除オプション
        flag_delete_pdf = False
        if pdf_files:
            with st.expander(
                f"⚠️ PDFファイル削除オプション ({len(pdf_files)} ファイル)"
            ):
                flag_delete_pdf = st.checkbox(
                    f"PDFファイルを削除する ({len(pdf_files)} ファイル)",
                    help="チェックを入れると、エントリ削除時にPDFファイルも削除されます",
                )

        if col_edit.button(
            "編集",
            key="edit",
            type="primary",
            icon=":material/edit:",
            use_container_width=True,
        ):
            edit_entry(key_selected)

        if col_delete.button(
            "削除",
            key="delete",
            icon=":material/delete:",
            use_container_width=True,
        ):
            _delete_entry(paper_list, [key_selected], flag_delete_pdf)

        # 削除されていない場合のみ引用・エクスポートを表示
        if key_selected in set(paper_list.keys()):
            st.divider()
            bib_text, bibdata = _render_citation(entry)
            _render_export([entry], pdf_files)

    # 複数行選択時: Edit など他機能は無効化し、エクスポートと一括削除のみ許可
    else:
        st.divider()
        st.subheader(f"📋 選択された論文 ({len(keys_selected)} 件)")

        with st.container(border=True):
            st.info(
                f"**{len(keys_selected)} 件の論文が選択されています。**\n\n"
                "複数選択時はエクスポート（BibTeX/XML）と一括削除のみ実行できます。"
            )

        entries = [paper_list[key] for key in keys_selected]

        # 一括削除オプション（PDF の削除有無を選択）
        total_pdf_count = sum(entry.get_pdf_count() for entry in entries)
        flag_delete_pdf_multi = False
        if total_pdf_count:
            with st.expander(
                f"⚠️ PDFファイル削除オプション ({total_pdf_count} ファイル)"
            ):
                flag_delete_pdf_multi = st.checkbox(
                    f"選択されたエントリのPDFファイルを削除する ({total_pdf_count} ファイル)",
                    help="チェックを入れると、エントリ削除時にPDFファイルも削除されます",
                )

        col_delete, col_export = st.columns(2)
        if col_delete.button(
            "🗑️ 一括削除",
            icon=":material/delete:",
            use_container_width=True,
            type="primary",
        ):
            _delete_entry(paper_list, keys_selected, flag_delete_pdf_multi)

        st.divider()
        _render_export(entries)

    _logger.debug("List page End")


@st.fragment
def edit_entry(key_selected: str) -> None:
    """論文エントリの編集フォームを表示する。

    更新に成功したときだけ再描画して閉じる。再描画すると画面のエラーは
    消えるので、追加した PDF の保存に失敗していたら、その文 (画面のエラーと
    同じ文) を保留中の通知 (``KEY_NOTICES``) に追加する。通知は、次の描画で
    ``config_page`` が警告として表示して消す。更新を拒否・中止したときは、
    再描画も通知の追加もせず、エラーを画面に残す。

    Parameters
    ----------
    key_selected : str
        編集する論文のキー
    """
    paper_list = PaperList.from_session_state()
    original_entry = paper_list[key_selected]
    existing_pdf_files = original_entry.get_pdf_files()

    with st.form("Edit", clear_on_submit=False):
        entry_edited = custom_entry(deepcopy(original_entry))

        # 既存のPDFファイルを表示
        if existing_pdf_files:
            st.write(f"Existing PDF files: {len(existing_pdf_files)}")
            for pdf_file in existing_pdf_files:
                st.text(f"  - {pdf_file.name}")

        # 追加のPDFファイルアップロード
        st.write("Add more PDF files:")
        uploaded_files = pdf_upload_form(accept_multiple=True)

        _col_done_edit, _col_cancel_edit, *_ = st.columns(6)
        flag_done_edit = _col_done_edit.form_submit_button(
            "",
            type="primary",
            key="done",
            icon=":material/check:",
            help="Done",
            use_container_width=True,
        )
        flag_cancel_edit = _col_cancel_edit.form_submit_button(
            "",
            key="cancel",
            icon=":material/cancel:",
            help="Cancel",
            use_container_width=True,
        )

    if flag_done_edit:
        # 追加したPDFの保存に失敗した文 (画面のエラーと同じ文)
        failures: list[str] = []

        # 必須フィールドのチェック（ENTRYTYPE ごと）
        entry_type = entry_edited["ENTRYTYPE"]
        missing_fields = MAP_REQUIRED_FIELDS[entry_type] - set(
            entry_edited.keys()
        )

        if missing_fields:
            st.error(
                "('{}') is/are necessary.".format("', '".join(missing_fields))
            )
        # 共通関数を使用してエントリを更新
        elif update_entry_in_list(
            paper_list, key_selected, entry_edited, uploaded_files, failures
        ):
            # 成功したときだけ再描画して閉じる。失敗したときは、
            # update_entry_in_list が出したエラーを画面に残す。
            # 再描画で消える PDF の保存の失敗は、通知に積んでおき、
            # 次の描画で config_page が表示する
            if failures:
                st.session_state.setdefault(KEY_NOTICES, []).extend(failures)
            st.rerun()

    elif flag_cancel_edit:
        st.rerun()


if __name__ == "__main__":
    main()
