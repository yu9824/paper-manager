import io
from logging import DEBUG
from typing import Optional

import streamlit as st
from crossref.restful import Works  # type: ignore[import-untyped]
from streamlit.runtime.uploaded_file_manager import UploadedFile

from paper_manager._constants import DIRPATH_PDF
from paper_manager.app.components import custom_entry, pdf_upload_form
from paper_manager.app.utils import (
    MAP_FIELDS,
    MAP_REQUIRED_FIELDS,
    config_page,
    entrytype4doi,
)
from paper_manager.bib import load_bib
from paper_manager.entry import Entry, PaperList
from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)


@config_page
def main() -> None:
    """main script"""
    st.header("Register")

    _logger.debug("Register page Start")

    paper_list = PaperList.from_session_state()

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

            uploaded_file_pdf = pdf_upload_form(uploaded_file_pdf)

            submitted_bib = st.form_submit_button(type="primary")
            if submitted_bib and (uploaded_file_bib or bib_text_input):
                bibtexfile_or_buffer = (
                    uploaded_file_bib
                    if uploaded_file_bib
                    else io.StringIO(bib_text_input)
                )
                assert isinstance(
                    bibtexfile_or_buffer, (io.StringIO, io.BytesIO)
                )
                entries = load_bib(bibtexfile_or_buffer)
                if len(entries) > 2:
                    st.error(
                        f"Must be only one entry. (contains {len(entries)} entries)"
                    )
                    submitted_bib = False
                elif len(entries) == 0:
                    st.error("No entry")
                    submitted_bib = False
                entry = Entry(entries[tuple(entries.keys())[0]])
            elif submitted_bib:
                st.error("FAIL: Empty BIB")
                submitted_bib = False

    # DOI登録
    with tab_from_doi:
        st.subheader("DOI")
        with st.form("doi_form", clear_on_submit=True):
            doi = st.text_input(
                "DOI",
                help="like 'doi.org/10.1107/S0567739476001551'",
            )

            uploaded_file_pdf = pdf_upload_form(uploaded_file_pdf)

            submitted_doi = st.form_submit_button(type="primary")
            if submitted_doi and doi:
                works = Works()
                metadata: Optional[dict] = works.doi(doi)

                if metadata:
                    entry = Entry(
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
                            "year": str(
                                metadata["published"]["date-parts"][0][0]
                            ),
                            "volume": metadata.get("volume", ""),
                            "number": metadata.get("issue", ""),
                            "pages": metadata.get("page", ""),
                            "DOI": metadata["DOI"],
                        }
                    )
                else:
                    st.error("FAIL: Invalid DOI")
                    submitted_doi = False
            elif submitted_doi:
                st.error("FAIL: Empty DOI")
                submitted_doi = False

    # カスタム登録
    with tab_custom_form:
        st.subheader("CUSTOM")

        entry_type = st.selectbox(
            "Select entry type",
            options=tuple(MAP_FIELDS.keys()),
        )

        if not (submitted_bib or submitted_doi):
            assert entry_type is not None
            with st.form("custom_form", clear_on_submit=True):
                entry = custom_entry(Entry(dict(ENTRYTYPE=entry_type)))

                # 共通
                uploaded_file_pdf = pdf_upload_form(uploaded_file_pdf)

                submitted_custom = st.form_submit_button(type="primary")
                if submitted_custom:
                    if MAP_REQUIRED_FIELDS[entry_type] <= set(entry.keys()):
                        for field in MAP_FIELDS[entry_type]:
                            _ = st.session_state.pop(field, None)
                    else:
                        st.error(
                            "('{}') is/are necessary.".format(
                                "', '".join(
                                    MAP_REQUIRED_FIELDS[entry_type]
                                    - set(entry.keys())
                                )
                            )
                        )
                        submitted_custom = False

    if submitted_bib or submitted_doi or submitted_custom:
        _logger.debug(f"submitted_entry={entry}")

        ## ここから共通
        entry["ID"] = entry.get_key(paper_list.keys())

        # pdfのファイル名で重複を確認する (DOIがないものも対応するため)
        st_pdf = {_entry.pdf_filename for _entry in paper_list.values()}
        if entry.pdf_filename in st_pdf:
            st.error("FAIL: Duplicated")
        else:
            # ラインナップとして追加して
            paper_list[entry.get_key(paper_list.keys())] = entry
            paper_list.to_session_state()

            # pdfをdataディレクトリ内に保存する
            if uploaded_file_pdf:
                with open(DIRPATH_PDF / entry.pdf_filename, mode="wb") as f:
                    f.write(uploaded_file_pdf.getvalue())

            st.success("SUCCESS: Registered")

            # to reload
            st.button("Clear")

    _logger.debug("Register page End")


if __name__ == "__main__":
    _logger.setLevel(DEBUG)

    main()
