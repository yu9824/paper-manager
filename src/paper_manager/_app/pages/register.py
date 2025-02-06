import io
from datetime import date
from logging import DEBUG
from pathlib import Path
from types import MappingProxyType
from typing import Optional, Union

import streamlit as st
from crossref.restful import Works  # type: ignore[import-untyped]
from streamlit.runtime.uploaded_file_manager import UploadedFile

from paper_manager._app._utils import config_page, load_fields, pdf_upload_form
from paper_manager.bib import load_bib
from paper_manager.entry import Entry, PaperList
from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)

DIRPATH_ROOT = Path(__file__).parent.parent
DIRPATH_DATA = DIRPATH_ROOT / "data"
DIRPATH_PDF = DIRPATH_DATA / "pdf"

FILEPATH_LIST = DIRPATH_DATA / "list.json"

ENCODING = "utf-8"
MAP_ENTRYTYPE4DOI = MappingProxyType(
    {
        "journal-article": "article",
        "proceedings-article": "inproceedings",
        "book": "book",
    }
)
MAP_FIELDS = MappingProxyType(load_fields())

MAP_REQUIRED_FIELDS = {
    entry_type: set(
        field
        for field in MAP_FIELDS[entry_type]
        if MAP_FIELDS[entry_type][field]["required"]
    )
    for entry_type in MAP_FIELDS
}


def entrytype4doi(entrytype: str) -> str:
    if entrytype in MAP_ENTRYTYPE4DOI:
        return MAP_ENTRYTYPE4DOI[entrytype]
    else:
        _logger.warning(f"Unknown entrytype: {entrytype}. Use 'misc' instead.")
        return "misc"


def custom_entry(entry: Entry) -> Entry:
    entry_type = entry["ENTRYTYPE"]

    for field in MAP_FIELDS[entry_type]:
        if field == "year":
            _year_default = int(entry[field]) if field in entry else None
            if _year := st.number_input(
                field,
                value=_year_default,
                format="%4i",
                placeholder="YYYY, Required",
                step=1,
                min_value=1000,
                max_value=date.today().year + 1,
                # key=field,
            ):
                entry[field] = str(_year)

        elif field == "author":
            if _text_input_temp := st.text_input(
                field,
                value=entry.get(field, None),
                placeholder="e.g., 'Taro Yamada and Jiro Yamada', Required",
                # key=field,
            ):
                entry[field] = _text_input_temp

        else:
            if _text_input_temp := st.text_input(
                field,
                value=entry.get(field, None),
                placeholder="Required"
                if field in MAP_REQUIRED_FIELDS[entry_type]
                else "",
                # key=field,
            ):
                entry[field] = _text_input_temp
    return entry


@config_page
def main():
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

            uploaded_file_pdf = (
                pdf_upload_form()
                if uploaded_file_pdf is None
                else uploaded_file_pdf
            )

            submitted_bib = st.form_submit_button(type="primary")
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
                    submitted_bib = False
                elif len(entries) == 0:
                    st.error("No entry")
                    submitted_bib = False
                entry = Entry(dict(entries[tuple(entries.keys())[0]]))
            elif submitted_bib:
                st.error("FAIL: Empty BIB")
                submitted_bib = False

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

            submitted_doi = st.form_submit_button(type="primary")
            if submitted_doi and doi:
                works = Works()
                metadata: Optional[dict[str, Union[str, dict]]] = (  # type: ignore[annotation-unchecked]
                    works.doi(doi)
                )

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
            with st.form("custom_form", clear_on_submit=False):
                entry = custom_entry(Entry(dict(ENTRYTYPE=entry_type)))

                # 共通
                uploaded_file_pdf = (
                    pdf_upload_form()
                    if uploaded_file_pdf is None
                    else uploaded_file_pdf
                )

                submitted_custom = st.form_submit_button(type="primary")
                if submitted_custom:
                    if MAP_REQUIRED_FIELDS[entry_type] <= set(entry.keys()):
                        for _key in MAP_FIELDS[entry_type]:
                            _ = st.session_state.pop(_key, None)
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

        # 前後の空白削除
        entry = Entry({_key: _value.strip() for _key, _value in entry.items()})

        filename_pdf = entry.pdf_filename
        # pdfのファイル名で重複を確認する (DOIがないものも対応するため)
        st_pdf = {_entry.pdf_filename for _entry in paper_list.values()}
        if filename_pdf in st_pdf:
            st.error("FAIL: Duplicated")
        else:
            # ラインナップとして追加して
            paper_list[entry.get_key(paper_list.keys())] = entry
            paper_list.to_session_state()

            # pdfをdataディレクトリ内に保存する
            if uploaded_file_pdf:
                with open(DIRPATH_PDF / filename_pdf, mode="wb") as f:
                    f.write(uploaded_file_pdf.getvalue())

            st.success("SUCCESS: Registered")

    _logger.debug("Register page End")


if __name__ == "__main__":
    _logger.setLevel(DEBUG)

    main()
