import json
import os
import re
import xml.dom.minidom
from logging import DEBUG
from pathlib import Path

import pandas as pd
import streamlit as st
from bib2xml.core import bib2xml  # type: ignore[import-untyped]
from bibtexparser.bibdatabase import (  # type: ignore[import-untyped]
    BibDatabase,
)
from bibtexparser.bwriter import BibTexWriter  # type: ignore[import-untyped]
from pybtex.database.input import bibtex  # type: ignore[import-untyped]
from streamlit_pdf_viewer import pdf_viewer  # type: ignore[import-untyped]

from paper_manager._app._utils import config_page
from paper_manager.entry import get_filename_pdf
from paper_manager.entry.typing import ENTRY
from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)

DIRPATH_ROOT = Path(__file__).parent
DIRPATH_DATA = DIRPATH_ROOT / "data"
DIRPATH_PDF = DIRPATH_DATA / "pdf"

FILEPATH_LIST = DIRPATH_DATA / "list.json"

COLS_TABLE = (
    "year",
    "author",
    "title",
    "journal",
    "volume",
    "number",
    "pages",
    "DOI",
)

ENCODING = "utf-8"


@config_page
def main():
    st.header("List")
    dict_paper_list: dict[str, ENTRY] = st.session_state["paper_list"]  # type: ignore[annotation-unchecked]

    os.makedirs(DIRPATH_PDF, exist_ok=True)

    if dict_paper_list:
        _df_paper_list = pd.DataFrame.from_dict(
            dict_paper_list, orient="index", dtype=str
        )
        for _col in set(COLS_TABLE) - set(_df_paper_list.columns):
            _df_paper_list.loc[:, _col] = ""

        paper_selected = st.dataframe(
            pd.concat(
                (
                    pd.Series(
                        {
                            _key: "o"
                            if (
                                DIRPATH_PDF / get_filename_pdf(_entry)
                            ).is_file()
                            else "x"
                            for _key, _entry in dict_paper_list.items()
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
            key_selected = tuple(dict_paper_list.keys())[
                index_list_selected[0]
            ]

            if "url" in dict_paper_list[key_selected]:
                st.markdown(
                    "Link: [{0}]({0})".format(
                        dict_paper_list[key_selected]["url"]
                    )
                )
            elif "DOI" in dict_paper_list[key_selected] and (
                result_doi := re.match(
                    r"(https?://.*doi\.org/)?(.+)",
                    dict_paper_list[key_selected]["DOI"],
                )
            ):
                st.markdown(
                    "Link: [{0}](https://doi.org/{0})".format(
                        result_doi.group(2)
                    )
                )

            filepath_pdf_selected = DIRPATH_PDF / get_filename_pdf(
                dict_paper_list[key_selected]
            )

            options_file_ext = ("bib", "xml")

            if filepath_pdf_selected.is_file():
                options_file_ext = ("pdf",) + options_file_ext

                flag_delete_pdf = st.checkbox("Delete the pdf file")
            if st.button("Delete"):
                if filepath_pdf_selected.is_file() and flag_delete_pdf:
                    os.remove(filepath_pdf_selected)

                del dict_paper_list[key_selected]
                with open(FILEPATH_LIST, mode="w", encoding=ENCODING) as f:
                    json.dump(dict_paper_list, f, indent=4, ensure_ascii=False)

                st.rerun()

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
                bib_database.entries = [dict_paper_list[key_selected]]

                bib_writer = BibTexWriter()
                bib_text = bib_writer.write(bib_database)

                st.download_button(
                    "Download",
                    bib_text,
                    file_name=filepath_pdf_selected.with_suffix(".bib").name,
                )
                st.code(bib_text, language="bibtex")
            elif ext == "xml":
                bib_database = BibDatabase()
                bib_database.entries = [dict_paper_list[key_selected]]

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
                    file_name=filepath_pdf_selected.with_suffix(".xml").name,
                )
                st.code(
                    xml.dom.minidom.parseString(xml_str).toprettyxml(
                        indent="  "
                    ),
                    language="xml",
                )


if __name__ == "__main__":
    _logger.setLevel(DEBUG)

    main()
