from typing import Union

import streamlit as st
from streamlit.runtime.uploaded_file_manager import UploadedFile


def pdf_upload_form() -> Union[UploadedFile, None]:
    return st.file_uploader(
        "PDF file (.pdf)",
        type="pdf",
        accept_multiple_files=False,
        help="PDF file (.pdf), optional",
    )
