import io
import json
from datetime import date
from logging import DEBUG
from pathlib import Path
from types import MappingProxyType
from typing import Optional, Union

import streamlit as st
from crossref.restful import Works  # type: ignore[import-untyped]
from streamlit.runtime.uploaded_file_manager import UploadedFile

from paper_manager._app._utils import config_page, pdf_upload_form
from paper_manager.bib import load_bib
from paper_manager.entry import get_filename_pdf, get_key
from paper_manager.entry.typing import ENTRY
from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)

DIRPATH_ROOT = Path(__file__).parent.parent
DIRPATH_DATA = DIRPATH_ROOT / "data"
DIRPATH_PDF = DIRPATH_DATA / "pdf"

FILEPATH_LIST = DIRPATH_DATA / "list.json"

ENCODING = "utf-8"
MAP_ENTRYTYPE4DOI = MappingProxyType(
    {
        "jornal-article": "article",
        "proceedings-article": "inproceedings",
        "book": "book",
    }
)


def entrytype4doi(entrytype: str) -> str:
    if entrytype in MAP_ENTRYTYPE4DOI:
        return MAP_ENTRYTYPE4DOI[entrytype]
    else:
        _logger.warning(f"Unknown entrytype: {entrytype}. Use 'misc' instead.")
        return "misc"


@config_page
def main():
    st.header("Register")
    dict_paper_list: dict[str, ENTRY] = st.session_state["paper_list"]  # type: ignore[annotation-unchecked]

    uploaded_file_pdf: Optional[UploadedFile] = None  # type: ignore[annotation-unchecked]

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

            uploaded_file_pdf = (
                pdf_upload_form()
                if uploaded_file_pdf is None
                else uploaded_file_pdf
            )

            submitted_bib = st.form_submit_button()
            if submitted_bib and (uploaded_file_bib or bib_text_input):
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
                print(entry)

    # DOI登録
    with tab_from_doi:
        st.subheader("DOI")
        with st.form("doi_form", clear_on_submit=True):
            doi = st.text_input(
                "DOI",
                key="DOI_DOI",
                help="like 'doi.org/10.1107/S0567739476001551'",
            )

            uploaded_file_pdf = (
                pdf_upload_form()
                if uploaded_file_pdf is None
                else uploaded_file_pdf
            )

            submitted_doi = st.form_submit_button()
            if submitted_doi and doi:
                works = Works()
                metadata: Optional[dict[str, Union[str, dict]]] = (  # type: ignore[annotation-unchecked]
                    works.doi(doi)
                )

                if metadata:
                    entry = {
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
                _entry_custom = dict(
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
                _entry_custom = dict(
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
                _entry_custom = dict(
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
                _entry_custom = dict(
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
                _entry_custom = dict(
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
                _entry_custom = dict(
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
            uploaded_file_pdf = (
                pdf_upload_form()
                if uploaded_file_pdf is None
                else uploaded_file_pdf
            )

            submitted_custom = st.form_submit_button()
            if submitted_custom:
                if (
                    _entry_custom["author"]
                    and _entry_custom["year"]
                    and _entry_custom["title"]
                ) and not entry:
                    entry = _entry_custom
                else:
                    st.error("('author', 'year' and 'title') is necessary.")
                    st.stop()

    if submitted_bib or submitted_doi or submitted_custom:
        ## ここから共通
        entry["ID"] = get_key(entry, keys=dict_paper_list.keys())

        # 前後の空白削除
        entry = {_key: _value.strip() for _key, _value in entry.items()}

        filename_pdf = get_filename_pdf(entry)
        # pdfのファイル名で重複を確認する (DOIがないものも対応するため)
        st_doi = {
            get_filename_pdf(_entry) for _entry in dict_paper_list.values()
        }
        if filename_pdf in st_doi:
            st.error("FAIL: Duplicated")
        else:
            # ラインナップとして追加して
            dict_paper_list[get_key(entry, dict_paper_list.keys())] = entry
            with open(FILEPATH_LIST, mode="w", encoding=ENCODING) as f:
                json.dump(dict_paper_list, f, indent=4, ensure_ascii=False)

            # pdfをdataディレクトリ内に保存する
            if uploaded_file_pdf:
                with open(DIRPATH_PDF / filename_pdf, mode="wb") as f:
                    f.write(uploaded_file_pdf.getvalue())

            st.success("SUCCESS: Registered")


if __name__ == "__main__":
    _logger.setLevel(DEBUG)

    main()
