import os
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

from paper_manager._constants import COLS_TABLE, DIRPATH_PDF, ENCODING
from paper_manager.app.components import (
    custom_entry,
    pdf_upload_form,
    save_paper_list,
    save_pdf,
    update_entry_in_list,
)
from paper_manager.app.utils import config_page
from paper_manager.entry import Entry, PaperList
from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)


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
    _df_paper_list = pd.DataFrame.from_dict(
        dict(paper_list), orient="index", dtype=str
    )
    for _col in set(COLS_TABLE) - set(_df_paper_list.columns):
        _df_paper_list.loc[:, _col] = ""

    paper_selected = st.dataframe(
        pd.concat(
            (
                pd.Series(
                    {
                        _key: "o"
                        if (DIRPATH_PDF / _entry.pdf_filename).is_file()
                        else "x"
                        for _key, _entry in paper_list.items()
                    },
                    name="PDF",
                ),
                _df_paper_list.loc[:, list(COLS_TABLE)].fillna(""),
            ),
            axis=1,
        ),
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


def _render_export(
    bib_text: str,
    bibdata: "bibtex.BibliographyData",
    filepath_pdf: Path,
    options_file_ext: tuple[str, ...],
) -> None:
    """エクスポート機能を表示する。

    Parameters
    ----------
    bib_text : str
        BibTeX形式のテキスト
    bibdata : bibtex.BibliographyData
        パースされたBibliographyData
    filepath_pdf : Path
        PDFファイルのパス
    options_file_ext : tuple[str, ...]
        選択可能なファイル拡張子
    """
    st.subheader("Export")

    ext = st.radio(
        "ext",
        options=options_file_ext,
        horizontal=True,
        label_visibility="hidden",
    )

    if ext == "pdf":
        with open(filepath_pdf, mode="rb") as f:
            pdf_contents = f.read()

        st.download_button(
            "Download",
            data=pdf_contents,
            file_name=filepath_pdf.name,
        )
        pdf_viewer(pdf_contents, width=700, height=1000)

    elif ext == "bib":
        st.download_button(
            "Download",
            bib_text,
            file_name=filepath_pdf.with_suffix(".bib").name,
        )

        st.code(bib_text, language="latex")

    elif ext == "xml":
        xml_str = bib2xml(bibdata)
        st.download_button(
            "Download",
            data=xml_str.encode(ENCODING),
            mime="application/xml",
            file_name=filepath_pdf.with_suffix(".xml").name,
        )

        st.code(
            xml.dom.minidom.parseString(xml_str).toprettyxml(indent="  "),
            language="xml",
        )


def _delete_entry(
    paper_list: PaperList,
    key_selected: str,
    filepath_pdf: Path,
    flag_delete_pdf: bool,
) -> None:
    """論文エントリを削除する。

    Parameters
    ----------
    paper_list : PaperList
        論文リスト
    key_selected : str
        削除する論文のキー
    filepath_pdf : Path
        PDFファイルのパス
    flag_delete_pdf : bool
        PDFファイルも削除するかどうか
    """
    _logger.debug("push delete button")

    if filepath_pdf.is_file() and flag_delete_pdf:
        os.remove(filepath_pdf)

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
    filepath_pdf_selected = DIRPATH_PDF / entry.pdf_filename

    # リンクの表示
    _render_link(entry)

    # ファイル拡張子オプションの設定
    options_file_ext: tuple[str, ...] = ("bib", "xml")
    flag_delete_pdf = False

    if filepath_pdf_selected.is_file():
        options_file_ext = ("pdf",) + options_file_ext
        flag_delete_pdf = st.checkbox("Delete the pdf file")

    # 編集・削除ボタン
    _col_edit, _col_delete, *_ = st.columns(8)

    if _col_edit.button("Edit", type="primary"):
        edit_entry(key_selected)

    elif _col_delete.button("Delete"):
        _delete_entry(
            paper_list, key_selected, filepath_pdf_selected, flag_delete_pdf
        )

    # 削除されていない場合のみ引用・エクスポートを表示
    elif key_selected in set(paper_list.keys()):
        bib_text, bibdata = _render_citation(entry)
        _render_export(
            bib_text, bibdata, filepath_pdf_selected, options_file_ext
        )

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

    with st.form("Edit", clear_on_submit=False):
        entry_edited = custom_entry(deepcopy(original_entry))

        # PDFファイルがない場合のみアップロードフォームを表示
        uploaded_file_pdf = None
        if not (DIRPATH_PDF / original_entry.pdf_filename).is_file():
            uploaded_file_pdf = pdf_upload_form()

        _col_done_edit, _col_cancel_edit, *_ = st.columns(6)
        flag_done_edit = _col_done_edit.form_submit_button(
            "Done", type="primary"
        )
        flag_cancel_edit = _col_cancel_edit.form_submit_button("Cancel")

    if flag_done_edit:
        # PDFファイルの保存（フォーム内でアップロードされた場合）
        if uploaded_file_pdf:
            save_pdf(
                uploaded_file_pdf,
                DIRPATH_PDF / original_entry.pdf_filename,
            )

        # 共通関数を使用してエントリを更新
        update_entry_in_list(
            paper_list, key_selected, entry_edited, uploaded_file_pdf
        )
        st.rerun()

    elif flag_cancel_edit:
        st.rerun()


if __name__ == "__main__":
    _logger.setLevel(DEBUG)

    main()
