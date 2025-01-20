import io
import json
import os
import re
import xml.dom.minidom
from datetime import date
from logging import DEBUG
from pathlib import Path

import pandas as pd
import streamlit as st
from bib2xml.core import bib2xml
from bibtexparser.bibdatabase import BibDatabase
from bibtexparser.bwriter import BibTexWriter
from crossref.restful import Works
from pybtex.database.input import bibtex
from streamlit_pdf_viewer import pdf_viewer

from paper_manager.bib import load_bib
from paper_manager.entry import get_filename_pdf, get_key
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


def main():
    st.title("PAPER MANAGER")
    st.header("List")

    os.makedirs(DIRPATH_PDF, exist_ok=True)

    if FILEPATH_LIST.exists():
        try:
            with open(FILEPATH_LIST, mode="r", encoding="utf-8") as f:
                dict_paper_list: dict[str, ENTRY] = json.load(f)
        except json.JSONDecodeError:
            dict_paper_list = dict()
    else:
        dict_paper_list = dict()

    if dict_paper_list:
        _df_paper_list = pd.DataFrame.from_dict(
            dict_paper_list, orient="index", dtype=str
        )
        _df_paper_list.loc[
            :, list(set(COLS_TABLE) - set(_df_paper_list.columns))
        ] = ""

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
                    pd.DataFrame.from_dict(
                        dict_paper_list, orient="index", dtype=str
                    )
                    .loc[:, list(COLS_TABLE)]
                    .fillna(""),
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
                with open(FILEPATH_LIST, mode="w", encoding="utf-8") as f:
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
                    data=xml_str.encode("utf-8"),
                    mime="application/xml",
                    file_name=filepath_pdf_selected.with_suffix(".xml").name,
                )
                st.code(
                    xml.dom.minidom.parseString(xml_str).toprettyxml(),
                    language="xml",
                )

    st.header("Register")

    tab_from_bib, tab_from_doi, tab_custom_form = st.tabs(
        ("BIB", "DOI", "CUSTOM")
    )
    # BIB登録
    with tab_from_bib:
        st.subheader("BIB")
        with st.form("bib_form", clear_on_submit=True):
            tab_from_bib_with_text, tab_from_bib_with_file = st.tabs(
                ("TEXT", "FILE Upload")
            )
            with tab_from_bib_with_text:
                bib_text_input = st.text_area("bibtex file")
            with tab_from_bib_with_file:
                uploaded_file_bib = st.file_uploader(
                    "bibtex file (.bib)",
                    type="bib",
                    accept_multiple_files=False,
                    help="bibtex file (.bib), optional",
                )
            uploaded_file_pdf = pdf_upload_form()

            submitted = st.form_submit_button()
            if submitted and (uploaded_file_bib or bib_text_input):
                bibtexfile_or_buffer = (
                    uploaded_file_bib
                    if uploaded_file_bib
                    else io.StringIO(bib_text_input)
                )
                entries = load_bib(bibtexfile_or_buffer)
                if len(entries) > 2:
                    st.error(
                        f"Must be only one entry. (contains {len(entries)} entries)"
                    )
                    st.stop()
                elif len(entries) == 0:
                    st.error("No entry")
                    st.stop()
                entry = dict(entries[tuple(entries.keys())[0]])

    # DOI登録
    with tab_from_doi:
        st.subheader("DOI")
        with st.form("doi_form", clear_on_submit=True):
            doi = st.text_input(
                "DOI",
                key="DOI_DOI",
                help="like 'doi.org/10.1107/S0567739476001551'",
            )

            uploaded_file_pdf = pdf_upload_form()

            submitted = st.form_submit_button()
            if submitted and doi:
                works = Works()
                metadata: dict = works.doi(doi)

                if metadata:
                    entry = {
                        "ENTRYTYPE": "article",
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
                else:
                    st.error("FAIL: Invalid DOI")

    # カスタム登録
    with tab_custom_form:
        st.subheader("CUSTOM")

        entry_type = st.selectbox(
            "Select entry type",
            options=(
                "article",
                "proceedings",
                "thesis",
                "patent",
                "report",
            ),
        )
        st.write(entry_type)

        with st.form("custom_form", clear_on_submit=True):
            if entry_type == "article":
                entry = dict(
                    ENTRYTYPE="article",
                    title=st.text_input("Title", placeholder="Required"),
                    author=st.text_input(
                        "Author",
                        placeholder="e.g., 'Taro Yamada and Jiro Yamada', Required",
                    ),
                    journal=st.text_input("Journal"),
                    year=str(
                        st.number_input(
                            "Year",
                            format="%4i",
                            placeholder="YYYY, Required",
                            step=1,
                            value=None,
                            min_value=1000,
                            max_value=date.today().year + 1,
                        )
                    ),
                    volume=st.text_input("Volume"),
                    number=st.text_input("Number"),
                    pages=st.text_input("Pages"),
                    url=st.text_input("URL"),
                    DOI=st.text_input("DOI"),
                )

            elif entry_type == "proceedings":
                entry = dict(
                    ENTRYTYPE="proceedings",
                    title=st.text_input("Title", placeholder="Required"),
                    editor=st.text_input("Editor"),
                    booktitle=st.text_input("Book title"),
                    year=str(
                        st.number_input(
                            "Year",
                            format="%4i",
                            placeholder="YYYY, Required",
                            step=1,
                            value=None,
                            min_value=1000,
                            max_value=date.today().year + 1,
                        )
                    ),
                    organization=st.text_input("Organization"),
                    publisher=st.text_input("Publisher"),
                    address=st.text_input("Address"),
                    pages=st.text_input("Pages"),
                    url=st.text_input("URL"),
                    DOI=st.text_input("DOI"),
                )

            elif entry_type == "thesis":
                entry = dict(
                    ENTRYTYPE="thesis",
                    title=st.text_input("Title", placeholder="Required"),
                    author=st.text_input("Author", placeholder="Required"),
                    school=st.text_input("School"),
                    year=str(
                        st.number_input(
                            "Year",
                            format="%4i",
                            placeholder="YYYY, Required",
                            step=1,
                            value=None,
                            min_value=1000,
                            max_value=date.today().year + 1,
                        )
                    ),
                    type=st.selectbox(
                        "Type", options=["PhD Thesis", "Master's Thesis"]
                    ),
                    url=st.text_input("URL"),
                )

            elif entry_type == "patent":
                entry = dict(
                    ENTRYTYPE="patent",
                    title=st.text_input("Title", placeholder="Required"),
                    inventor=st.text_input("Inventor", placeholder="Required"),
                    holder=st.text_input("Patent Holder"),
                    number=st.text_input("Patent Number"),
                    year=str(
                        st.number_input(
                            "Year",
                            format="%4i",
                            placeholder="YYYY, Required",
                            step=1,
                            value=None,
                            min_value=1000,
                            max_value=date.today().year + 1,
                        )
                    ),
                    country=st.text_input("Country"),
                    url=st.text_input("URL"),
                )

            elif entry_type == "report":
                entry = dict(
                    ENTRYTYPE="report",
                    title=st.text_input("Title", placeholder="Required"),
                    author=st.text_input("Author"),
                    institution=st.text_input("Institution"),
                    year=str(
                        st.number_input(
                            "Year",
                            format="%4i",
                            placeholder="YYYY, Required",
                            step=1,
                            value=None,
                            min_value=1000,
                            max_value=date.today().year + 1,
                        )
                    ),
                    number=st.text_input("Report Number"),
                    url=st.text_input("URL"),
                )
            elif entry_type == "book":
                entry = dict(
                    ENTRYTYPE="book",
                    title=st.text_input("Title", placeholder="Required"),
                    author=st.text_input(
                        "Author", placeholder="e.g., 'Taro Yamada', Required"
                    ),
                    publisher=st.text_input(
                        "Publisher", placeholder="Required"
                    ),
                    year=str(
                        st.number_input(
                            "Year",
                            format="%4i",
                            placeholder="YYYY, Required",
                            step=1,
                            value=None,
                            min_value=1000,
                            max_value=date.today().year + 1,
                        )
                    ),
                    edition=st.text_input("Edition"),
                    volume=st.text_input("Volume"),
                    series=st.text_input("Series"),
                    address=st.text_input("Publisher Address"),
                    url=st.text_input("URL"),
                    ISBN=st.text_input("ISBN"),
                )
            # 共通
            uploaded_file_pdf = pdf_upload_form()

            submitted = st.form_submit_button()
            if submitted and not (
                entry["author"] and entry["year"] and entry["title"]
            ):
                st.error(
                    "'.bib file', 'DOI' or ('author', 'year' and 'title') is necessary."
                )
                st.stop()

        if submitted:
            ## ここから共通
            entry["ID"] = get_key(entry, keys=dict_paper_list.keys())

            # 前後の空白削除
            entry = {_key: _value.strip() for _key, _value in entry.items()}

            filename_pdf = get_filename_pdf(entry)
            # pdfのファイル名で重複を確認する (DOIがないものも対応するため)
            st_doi: set[str] = {
                get_filename_pdf(_entry) for _entry in dict_paper_list.values()
            }
            if filename_pdf in st_doi:
                st.error("FAIL: Duplicated")
            else:
                # ラインナップとして追加して
                dict_paper_list[get_key(entry, dict_paper_list.keys())] = entry
                with open(FILEPATH_LIST, mode="w", encoding="utf-8") as f:
                    json.dump(dict_paper_list, f, indent=4, ensure_ascii=True)

                # pdfをdataディレクトリ内に保存する
                if uploaded_file_pdf:
                    with open(DIRPATH_PDF / filename_pdf, mode="wb") as f:
                        f.write(uploaded_file_pdf.getvalue())

                # reload
                st.rerun()


def pdf_upload_form():
    return st.file_uploader(
        "PDF file (.pdf)",
        type="pdf",
        accept_multiple_files=False,
        help="PDF file (.pdf), optional",
    )


if __name__ == "__main__":
    _logger.setLevel(DEBUG)

    main()
