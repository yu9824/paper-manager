import os
import re
import xml.dom.minidom
from copy import deepcopy
from logging import DEBUG

import pandas as pd
import streamlit as st
from bib2xml.core import bib2xml  # type: ignore[import-untyped]
from bibtexparser.bibdatabase import (  # type: ignore[import-untyped]
    BibDatabase,
)
from bibtexparser.bwriter import BibTexWriter  # type: ignore[import-untyped]
from pybtex.database.input import bibtex  # type: ignore[import-untyped]
from streamlit_pdf_viewer import pdf_viewer  # type: ignore[import-untyped]

from paper_manager._constants import COLS_TABLE, DIRPATH_PDF, ENCODING
from paper_manager.app._utils import config_page
from paper_manager.app.pages.register import custom_entry, pdf_upload_form
from paper_manager.entry import PaperList
from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)


@config_page
def main():
    st.header("List")

    _logger.debug("List page Start")

    paper_list = PaperList.from_session_state()

    if paper_list:
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
            key_selected = tuple(paper_list.keys())[index_list_selected[0]]

            if "url" in paper_list[key_selected]:
                st.markdown(
                    "Link: [{0}]({0})".format(paper_list[key_selected]["url"])
                )
            elif "DOI" in paper_list[key_selected] and (
                result_doi := re.match(
                    r"(https?://.*doi\.org/)?(.+)",
                    paper_list[key_selected]["DOI"],
                )
            ):
                st.markdown(
                    "Link: [{0}](https://doi.org/{0})".format(
                        result_doi.group(2)
                    )
                )

            filepath_pdf_selected = (
                DIRPATH_PDF / paper_list[key_selected].pdf_filename
            )

            options_file_ext = ("bib", "xml")

            if filepath_pdf_selected.is_file():
                options_file_ext = ("pdf",) + options_file_ext

                flag_delete_pdf = st.checkbox("Delete the pdf file")

            _col_edit, _col_delete, *_ = st.columns(8)
            if _col_edit.button("Edit", type="primary"):
                edit_entry(key_selected)

            elif _col_delete.button("Delete"):
                _logger.debug("push delete button")

                if filepath_pdf_selected.is_file() and flag_delete_pdf:
                    os.remove(filepath_pdf_selected)

                del paper_list[key_selected]
                paper_list.to_session_state()
                paper_list.to_file()

                st.rerun()

            # if not deleted
            elif key_selected in set(paper_list.keys()):
                st.subheader("Information")

                ext = st.radio(
                    "ext",
                    options=options_file_ext,
                    horizontal=True,
                    label_visibility="hidden",
                )

                if ext == "pdf":
                    with open(filepath_pdf_selected, mode="rb") as f:
                        pdf_contents = f.read()

                    st.download_button(
                        "Download",
                        data=pdf_contents,
                        file_name=filepath_pdf_selected.name,
                    )
                    pdf_viewer(pdf_contents, width=700, height=1000)
                elif ext == "bib":
                    bib_database = BibDatabase()
                    bib_database.entries = [paper_list[key_selected]]

                    bib_writer = BibTexWriter()
                    bib_text = bib_writer.write(bib_database)

                    st.download_button(
                        "Download",
                        bib_text,
                        file_name=filepath_pdf_selected.with_suffix(
                            ".bib"
                        ).name,
                    )

                    st.code(bib_text, language="latex")

                elif ext == "xml":
                    bib_database = BibDatabase()
                    bib_database.entries = [paper_list[key_selected]]

                    bib_writer = BibTexWriter()

                    bib_parser = bibtex.Parser()
                    bibdata = bib_parser.parse_string(
                        bib_writer.write(bib_database)
                    )

                    xml_str = bib2xml(bibdata)
                    st.download_button(
                        "Download",
                        data=xml_str.encode(ENCODING),
                        mime="application/xml",
                        file_name=filepath_pdf_selected.with_suffix(
                            ".xml"
                        ).name,
                    )

                    st.code(
                        xml.dom.minidom.parseString(xml_str).toprettyxml(
                            indent="  "
                        ),
                        language="xml",
                    )

    _logger.debug("List page End")


@st.fragment
def edit_entry(key_selected):
    paper_list = PaperList.from_session_state()
    with st.form("Edit", clear_on_submit=False):
        _entry_editted = custom_entry(deepcopy(paper_list[key_selected]))
        if not (DIRPATH_PDF / paper_list[key_selected].pdf_filename).is_file():
            uploaded_file_pdf = pdf_upload_form()
            # pdfをdataディレクトリ内に保存する
            if uploaded_file_pdf:
                with open(
                    DIRPATH_PDF / paper_list[key_selected].pdf_filename,
                    mode="wb",
                ) as f:
                    f.write(uploaded_file_pdf.getvalue())

        _col_done_edit, _col_cancel_edit, *_ = st.columns(6)
        _flag_done_edit = _col_done_edit.form_submit_button(
            "Done", type="primary"
        )
        _flag_cancel_edit = _col_cancel_edit.form_submit_button("Cancel")

    if _flag_done_edit:
        if (
            paper_list[key_selected].pdf_filename
            == _entry_editted.pdf_filename
        ):
            paper_list[key_selected] = _entry_editted
        else:
            new_id = _entry_editted.get_key(paper_list.keys())
            del paper_list[key_selected]
            _entry_editted["ID"] = new_id
            paper_list[new_id] = _entry_editted

        _logger.debug(f"{paper_list=}")

        paper_list.to_session_state()
        paper_list.to_file()

        st.rerun()
    elif _flag_cancel_edit:
        st.rerun()


if __name__ == "__main__":
    _logger.setLevel(DEBUG)

    main()
