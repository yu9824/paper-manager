from datetime import date
from typing import Union

import streamlit as st
from streamlit.runtime.uploaded_file_manager import UploadedFile

from paper_manager.app.utils import MAP_FIELDS, MAP_REQUIRED_FIELDS
from paper_manager.entry import Entry


def pdf_upload_form() -> Union[UploadedFile, None]:
    """
    Displays a file uploader widget for uploading a single PDF file.

    Returns
    -------
    Union[UploadedFile, None]
        The uploaded file object if a file is uploaded, otherwise None.
    """
    return st.file_uploader(
        "PDF file (.pdf)",
        type="pdf",
        accept_multiple_files=False,
        help="PDF file (.pdf), optional",
    )


def custom_entry(entry: Entry) -> Entry:
    """
    Displays a form for customizing an `Entry` object using Streamlit widgets.

    This function generates input fields dynamically based on the entry type
    and modifies the `Entry` object with user-provided values.

    Parameters
    ----------
    entry : Entry
        The `Entry` object to be customized.

    Returns
    -------
    Entry
        The updated `Entry` object with user-input values.
    """
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
