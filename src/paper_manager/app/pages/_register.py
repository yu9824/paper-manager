import io
from logging import DEBUG
from typing import Optional, Union

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


def _render_bib_form(
    uploaded_file_pdf: Optional[UploadedFile],
) -> tuple[bool, Optional[Entry], Optional[UploadedFile]]:
    """BIB登録フォームを表示する。

    Parameters
    ----------
    uploaded_file_pdf : Optional[UploadedFile]
        アップロードされたPDFファイル

    Returns
    -------
    tuple[bool, Optional[Entry], Optional[UploadedFile]]
        (送信成功フラグ, 作成されたEntry, PDFファイル)
    """
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

        submitted = st.form_submit_button(type="primary")

        if not submitted:
            return False, None, uploaded_file_pdf

        if not (uploaded_file_bib or bib_text_input):
            st.error("FAIL: Empty BIB")
            return False, None, uploaded_file_pdf

        bibtexfile_or_buffer: Union[io.StringIO, io.BytesIO] = (
            uploaded_file_bib
            if uploaded_file_bib
            else io.StringIO(bib_text_input)
        )
        assert isinstance(bibtexfile_or_buffer, (io.StringIO, io.BytesIO))

        entries = load_bib(bibtexfile_or_buffer)

        if len(entries) > 2:
            st.error(
                f"Must be only one entry. (contains {len(entries)} entries)"
            )
            return False, None, uploaded_file_pdf

        if len(entries) == 0:
            st.error("No entry")
            return False, None, uploaded_file_pdf

        entry = Entry(entries[tuple(entries.keys())[0]])
        return True, entry, uploaded_file_pdf


def _render_doi_form(
    uploaded_file_pdf: Optional[UploadedFile],
) -> tuple[bool, Optional[Entry], Optional[UploadedFile]]:
    """DOI登録フォームを表示する。

    Parameters
    ----------
    uploaded_file_pdf : Optional[UploadedFile]
        アップロードされたPDFファイル

    Returns
    -------
    tuple[bool, Optional[Entry], Optional[UploadedFile]]
        (送信成功フラグ, 作成されたEntry, PDFファイル)
    """
    st.subheader("DOI")

    with st.form("doi_form", clear_on_submit=True):
        doi = st.text_input(
            "DOI",
            help="like 'doi.org/10.1107/S0567739476001551'",
        )

        uploaded_file_pdf = pdf_upload_form(uploaded_file_pdf)

        submitted = st.form_submit_button(type="primary")

        if not submitted:
            return False, None, uploaded_file_pdf

        if not doi:
            st.error("FAIL: Empty DOI")
            return False, None, uploaded_file_pdf

        works = Works()
        metadata: Optional[dict] = works.doi(doi)

        if not metadata:
            st.error("FAIL: Invalid DOI")
            return False, None, uploaded_file_pdf

        entry = _create_entry_from_doi_metadata(metadata)
        return True, entry, uploaded_file_pdf


def _render_custom_form(
    uploaded_file_pdf: Optional[UploadedFile],
    already_submitted: bool,
) -> tuple[bool, Optional[Entry], Optional[UploadedFile]]:
    """カスタム登録フォームを表示する。

    Parameters
    ----------
    uploaded_file_pdf : Optional[UploadedFile]
        アップロードされたPDFファイル
    already_submitted : bool
        他のフォームで既に送信されたかどうか

    Returns
    -------
    tuple[bool, Optional[Entry], Optional[UploadedFile]]
        (送信成功フラグ, 作成されたEntry, PDFファイル)
    """
    st.subheader("CUSTOM")

    entry_type = st.selectbox(
        "Select entry type",
        options=tuple(MAP_FIELDS.keys()),
    )

    if already_submitted:
        return False, None, uploaded_file_pdf

    assert entry_type is not None

    with st.form("custom_form", clear_on_submit=True):
        entry = custom_entry(Entry(dict(ENTRYTYPE=entry_type)))

        uploaded_file_pdf = pdf_upload_form(uploaded_file_pdf)

        submitted = st.form_submit_button(type="primary")

        if not submitted:
            return False, None, uploaded_file_pdf

        # 必須フィールドのチェック
        if not (MAP_REQUIRED_FIELDS[entry_type] <= set(entry.keys())):
            missing_fields = MAP_REQUIRED_FIELDS[entry_type] - set(
                entry.keys()
            )
            st.error(
                "('{}') is/are necessary.".format("', '".join(missing_fields))
            )
            return False, None, uploaded_file_pdf

        # セッションステートのクリア
        for field in MAP_FIELDS[entry_type]:
            _ = st.session_state.pop(field, None)

        return True, entry, uploaded_file_pdf


def _register_entry(
    paper_list: PaperList,
    entry: Entry,
    uploaded_file_pdf: Optional[UploadedFile],
) -> bool:
    """論文エントリを登録する。

    Parameters
    ----------
    paper_list : PaperList
        論文リスト
    entry : Entry
        登録する論文エントリ
    uploaded_file_pdf : Optional[UploadedFile]
        アップロードされたPDFファイル

    Returns
    -------
    bool
        登録成功した場合True
    """
    _logger.debug(f"submitted_entry={entry}")

    entry["ID"] = entry.get_key(paper_list.keys())

    # pdfのファイル名で重複を確認する (DOIがないものも対応するため)
    st_pdf = {_entry.pdf_filename for _entry in paper_list.values()}
    if entry.pdf_filename in st_pdf:
        st.error("FAIL: Duplicated")
        return False

    # ラインナップとして追加
    paper_list[entry.get_key(paper_list.keys())] = entry
    paper_list.to_session_state()

    # pdfをdataディレクトリ内に保存する
    if uploaded_file_pdf:
        with open(DIRPATH_PDF / entry.pdf_filename, mode="wb") as f:
            f.write(uploaded_file_pdf.getvalue())

    st.success("SUCCESS: Registered")

    # to reload
    st.button("Clear")

    return True


@config_page
def main() -> None:
    """論文登録ページのメインスクリプト。

    BIB、DOI、カスタムの3つの方法で論文を登録できる。
    """
    st.header("Register")
    _logger.debug("Register page Start")

    paper_list = PaperList.from_session_state()
    uploaded_file_pdf: Optional[UploadedFile] = None  # type: ignore[annotation-unchecked]

    # タブの作成
    tab_from_bib, tab_from_doi, tab_custom_form = st.tabs(
        ("BIB", "DOI", "CUSTOM")
    )

    # BIB登録
    with tab_from_bib:
        submitted_bib, entry_bib, uploaded_file_pdf = _render_bib_form(
            uploaded_file_pdf
        )

    # DOI登録
    with tab_from_doi:
        submitted_doi, entry_doi, uploaded_file_pdf = _render_doi_form(
            uploaded_file_pdf
        )

    # カスタム登録
    with tab_custom_form:
        submitted_custom, entry_custom, uploaded_file_pdf = (
            _render_custom_form(
                uploaded_file_pdf,
                already_submitted=(submitted_bib or submitted_doi),
            )
        )

    # 登録処理
    if submitted_bib and entry_bib is not None:
        _register_entry(paper_list, entry_bib, uploaded_file_pdf)
    elif submitted_doi and entry_doi is not None:
        _register_entry(paper_list, entry_doi, uploaded_file_pdf)
    elif submitted_custom and entry_custom is not None:
        _register_entry(paper_list, entry_custom, uploaded_file_pdf)

    _logger.debug("Register page End")


if __name__ == "__main__":
    _logger.setLevel(DEBUG)

    main()
