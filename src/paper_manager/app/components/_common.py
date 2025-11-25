from datetime import date
from pathlib import Path
from typing import Optional, Union

import streamlit as st
from streamlit.runtime.uploaded_file_manager import UploadedFile

from paper_manager._constants import DIRPATH_PDF
from paper_manager.app.utils import MAP_FIELDS, MAP_REQUIRED_FIELDS
from paper_manager.entry import Entry, PaperList
from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)


def pdf_upload_form(
    uploaded_file_pdf: Optional[UploadedFile] = None,
) -> Union[UploadedFile, None]:
    """PDFファイルアップロードウィジェットを表示する。

    Parameters
    ----------
    uploaded_file_pdf : UploadedFile or None
        既存のアップロードされたPDFファイル

    Returns
    -------
    Union[UploadedFile, None]
        アップロードされたファイルオブジェクト。アップロードされていない場合はNone。
    """
    _uploaded_file_pdf_temp = st.file_uploader(
        "PDF file (.pdf)",
        type="pdf",
        accept_multiple_files=False,
        help="PDF file (.pdf), optional",
    )
    return (
        _uploaded_file_pdf_temp
        if _uploaded_file_pdf_temp
        else uploaded_file_pdf
    )


def custom_entry(entry: Entry) -> Entry:
    """Entryオブジェクトをカスタマイズするためのフォームを表示する。

    エントリタイプに基づいて入力フィールドを動的に生成し、
    ユーザーが入力した値でEntryオブジェクトを更新する。

    Parameters
    ----------
    entry : Entry
        カスタマイズするEntryオブジェクト

    Returns
    -------
    Entry
        更新されたEntryオブジェクト
    """
    entry_type = entry["ENTRYTYPE"]

    for field in MAP_FIELDS[entry_type]:
        if field == "year":
            _year_default = (
                int(entry[field])
                if field in entry
                else st.session_state.get(field)
            )
            if _year := st.number_input(
                field,
                value=_year_default,
                format="%4i",
                placeholder="YYYY, Required",
                step=1,
                min_value=1000,
                max_value=date.today().year + 1,
                key=field,
            ):
                entry[field] = str(_year)

        elif field == "author":
            if _text_input_temp := st.text_input(
                field,
                value=entry.get(field, st.session_state.get(field)),
                placeholder="e.g., 'Taro Yamada and Jiro Yamada', Required",
                key=field,
            ):
                entry[field] = _text_input_temp

        else:
            if _text_input_temp := st.text_input(
                field,
                value=entry.get(field, st.session_state.get(field)),
                placeholder="Required"
                if field in MAP_REQUIRED_FIELDS[entry_type]
                else "",
                key=field,
            ):
                entry[field] = _text_input_temp

    return entry


def save_pdf(
    uploaded_file_pdf: Optional[UploadedFile],
    filepath_pdf: Path,
) -> bool:
    """PDFファイルを保存する。

    Parameters
    ----------
    uploaded_file_pdf : Optional[UploadedFile]
        アップロードされたPDFファイル
    filepath_pdf : Path
        保存先のファイルパス

    Returns
    -------
    bool
        保存に成功した場合True
    """
    if uploaded_file_pdf is None:
        return False

    with open(filepath_pdf, mode="wb") as f:
        f.write(uploaded_file_pdf.getvalue())

    _logger.debug(f"PDF saved: {filepath_pdf}")
    return True


def save_paper_list(paper_list: PaperList) -> None:
    """PaperListをセッションステートとファイルに保存する。

    Parameters
    ----------
    paper_list : PaperList
        保存する論文リスト
    """
    paper_list.to_session_state()
    paper_list.to_file()
    _logger.debug("PaperList saved")


def register_entry_to_list(
    paper_list: PaperList,
    entry: Entry,
    uploaded_file_pdf: Optional[UploadedFile] = None,
) -> bool:
    """論文エントリをリストに登録する。

    重複チェックを行い、PDFファイルがあれば保存する。

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
    _logger.debug(f"Registering entry: {entry}")

    # IDを設定
    entry["ID"] = entry.get_key(paper_list.keys())

    # pdfのファイル名で重複を確認する (DOIがないものも対応するため)
    existing_pdf_filenames = {
        _entry.pdf_filename for _entry in paper_list.values()
    }
    if entry.pdf_filename in existing_pdf_filenames:
        st.error("FAIL: Duplicated")
        return False

    # ラインナップとして追加
    paper_list[entry["ID"]] = entry

    # PDFファイルを保存
    if uploaded_file_pdf:
        save_pdf(uploaded_file_pdf, DIRPATH_PDF / entry.pdf_filename)

    # PaperListを保存
    save_paper_list(paper_list)

    st.success("SUCCESS: Registered")
    return True


def update_entry_in_list(
    paper_list: PaperList,
    key_original: str,
    entry_updated: Entry,
    uploaded_file_pdf: Optional[UploadedFile] = None,
) -> bool:
    """論文エントリを更新する。

    Parameters
    ----------
    paper_list : PaperList
        論文リスト
    key_original : str
        更新する論文の元のキー
    entry_updated : Entry
        更新後の論文エントリ
    uploaded_file_pdf : Optional[UploadedFile]
        アップロードされたPDFファイル

    Returns
    -------
    bool
        更新成功した場合True
    """
    _logger.debug(f"Updating entry: {key_original}")

    original_entry = paper_list[key_original]

    # PDFファイル名が変わらない場合は単純に更新
    if original_entry.pdf_filename == entry_updated.pdf_filename:
        paper_list[key_original] = entry_updated
    else:
        # PDFファイル名が変わる場合は新しいキーで登録
        new_id = entry_updated.get_key(paper_list.keys())
        del paper_list[key_original]
        entry_updated["ID"] = new_id
        paper_list[new_id] = entry_updated

    # PDFファイルを保存（既存のファイルがない場合のみ）
    if uploaded_file_pdf:
        filepath_pdf = DIRPATH_PDF / entry_updated.pdf_filename
        if not filepath_pdf.is_file():
            save_pdf(uploaded_file_pdf, filepath_pdf)

    # PaperListを保存
    save_paper_list(paper_list)

    _logger.debug(f"Entry updated: {paper_list=}")
    return True
