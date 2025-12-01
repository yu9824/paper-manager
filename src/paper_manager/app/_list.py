import re
import xml.dom.minidom
from copy import deepcopy
from logging import DEBUG
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st
from bib2xml import bib2xml  # type: ignore[import-untyped]
from bibtexparser.bibdatabase import (  # type: ignore[import-untyped]
    BibDatabase,
)
from bibtexparser.bwriter import BibTexWriter  # type: ignore[import-untyped]
from pybtex.database.input import bibtex  # type: ignore[import-untyped]
from pybtex.style.formatting.plain import Style  # type: ignore[import-untyped]
from streamlit_pdf_viewer import pdf_viewer  # type: ignore[import-untyped]

from paper_manager._constants import (
    AUTHOR_SEPARATOR,
    COLNAME_AUTHOR,
    COLNAME_HAS_PDF,
    COLNAME_TAGS,
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
from paper_manager.app.helper import config_page
from paper_manager.entry import Entry, PaperList
from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)


def split(s: str, sep: str) -> tuple[str, ...]:
    """文字列をセパレータで分割し、タプルとして返す。

    Parameters
    ----------
    s : str
        分割する文字列
    sep : str
        セパレータ

    Returns
    -------
    tuple[str, ...]
        分割された文字列のタプル
    """
    if not s:
        return ()
    return tuple(map(str.strip, s.split(sep)))


def _render_paper_table(paper_list: PaperList) -> Optional[str]:
    """論文一覧テーブルを表示し、選択された論文のキーを返す。

    Parameters
    ----------
    paper_list : PaperList
        表示する論文リスト

    Returns
    -------
    Optional[str]
        選択された論文のキー。選択されていない場合は None。
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
        selection_mode="single-row",
        on_select="rerun",
    )

    # 行が選択されていたら
    if index_list_selected := paper_selected["selection"]["rows"]:
        return tuple(paper_list.keys())[index_list_selected[0]]
    return None


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


def _render_citation(entry: Entry) -> tuple[str, "bibtex.BibliographyData"]:
    """論文の引用情報を表示する。

    Parameters
    ----------
    entry : Entry
        表示する論文エントリ

    Returns
    -------
    tuple[str, bibtex.BibliographyData]
        BibTeXテキストとパースされたBibliographyDataのタプル
    """
    st.subheader("Citation")

    # bibtex
    bib_database = BibDatabase()
    bib_database.entries = [entry]

    bib_writer = BibTexWriter()
    bib_text = bib_writer.write(bib_database)

    bib_parser = bibtex.Parser()
    bibdata = bib_parser.parse_string(bib_text)

    _citation_key = tuple(bibdata.entries.keys())[0]
    formatted_entry = Style().format_entry(
        _citation_key, bibdata.entries[_citation_key]
    )

    st.text("HTML")
    with st.container(border=True):
        st.html(formatted_entry.text.render_as("html"))

    st.text("Plain text")
    st.code(
        formatted_entry.text.render_as("text"),
        language="plaintext",
        wrap_lines=True,
    )

    st.divider()

    return bib_text, bibdata


def _render_pdf_viewer(pdf_files: list[Path]) -> None:
    """複数のPDFファイルを表示する。

    Parameters
    ----------
    pdf_files : list[Path]
        表示するPDFファイルのリスト
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

        with open(selected_pdf, mode="rb") as f:
            pdf_contents = f.read()

        # ダウンロードボタン
        st.download_button(
            "Download",
            data=pdf_contents,
            file_name=selected_pdf.name,
            key=f"download_{selected_pdf.name}",
        )

        # PDFビューア
        pdf_viewer(pdf_contents, width=700, height=1000)


def _render_export(
    bib_text: str,
    bibdata: "bibtex.BibliographyData",
    entry: Entry,
    pdf_files: list[Path],
) -> None:
    """エクスポート機能を表示する。

    Parameters
    ----------
    bib_text : str
        BibTeX形式のテキスト
    bibdata : bibtex.BibliographyData
        パースされたBibliographyData
    entry : Entry
        論文エントリ
    pdf_files : list[Path]
        PDFファイルのリスト
    """
    st.subheader("Export")

    # エクスポートオプション
    options_file_ext: list[str] = ["bib", "xml"]
    if pdf_files:
        options_file_ext.insert(0, "pdf")

    ext = st.radio(
        "ext",
        options=options_file_ext,
        horizontal=True,
        label_visibility="hidden",
    )

    if ext == "pdf":
        _render_pdf_viewer(pdf_files)

    elif ext == "bib":
        st.download_button(
            "Download",
            bib_text,
            file_name=f"{entry.pdf_dir_name}.bib",
        )
        st.code(bib_text, language="latex")

    elif ext == "xml":
        xml_str = bib2xml(bibdata)
        st.download_button(
            "Download",
            data=xml_str.encode(ENCODING),
            mime="application/xml",
            file_name=f"{entry.pdf_dir_name}.xml",
        )
        st.code(
            xml.dom.minidom.parseString(xml_str).toprettyxml(indent="  "),
            language="xml",
        )


def _delete_entry(
    paper_list: PaperList,
    key_selected: str,
    entry: Entry,
    flag_delete_pdf: bool,
) -> None:
    """論文エントリを削除する。

    Parameters
    ----------
    paper_list : PaperList
        論文リスト
    key_selected : str
        削除する論文のキー
    entry : Entry
        削除する論文エントリ
    flag_delete_pdf : bool
        PDFファイルも削除するかどうか
    """
    _logger.debug("push delete button")

    if flag_delete_pdf:
        delete_pdfs(entry)

    del paper_list[key_selected]
    save_paper_list(paper_list)

    st.rerun()


@config_page
def main() -> None:
    """論文リストページのメインスクリプト。

    論文一覧の表示、選択した論文の詳細表示、編集・削除機能を提供する。
    """
    st.header("List")
    _logger.debug("List page Start")

    paper_list = PaperList.from_session_state()

    if not paper_list:
        _logger.debug("List page End")
        return

    # 論文一覧テーブルの表示
    key_selected = _render_paper_table(paper_list)

    if key_selected is None:
        _logger.debug("List page End")
        return

    entry = paper_list[key_selected]
    pdf_files = entry.get_pdf_files()

    # リンクの表示
    _render_link(entry)

    # PDF削除オプション
    flag_delete_pdf = False
    if pdf_files:
        flag_delete_pdf = st.checkbox(
            f"Delete PDF files ({len(pdf_files)} file(s))"
        )

    # 編集・削除ボタン
    _col_edit, _col_delete, *_ = st.columns(8)

    if _col_edit.button("Edit", type="primary"):
        edit_entry(key_selected)

    elif _col_delete.button("Delete"):
        _delete_entry(paper_list, key_selected, entry, flag_delete_pdf)

    # 削除されていない場合のみ引用・エクスポートを表示
    elif key_selected in set(paper_list.keys()):
        bib_text, bibdata = _render_citation(entry)
        _render_export(bib_text, bibdata, entry, pdf_files)

    _logger.debug("List page End")


@st.fragment
def edit_entry(key_selected: str) -> None:
    """論文エントリの編集フォームを表示する。

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
            "Done", type="primary"
        )
        flag_cancel_edit = _col_cancel_edit.form_submit_button("Cancel")

    if flag_done_edit:
        # 共通関数を使用してエントリを更新
        update_entry_in_list(
            paper_list, key_selected, entry_edited, uploaded_files
        )
        st.rerun()

    elif flag_cancel_edit:
        st.rerun()


if __name__ == "__main__":
    _logger.setLevel(DEBUG)

    main()
