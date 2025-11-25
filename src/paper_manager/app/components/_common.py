import shutil
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
    accept_multiple: bool = True,
) -> list[UploadedFile]:
    """PDFファイルアップロードウィジェットを表示する。

    Parameters
    ----------
    accept_multiple : bool, optional
        複数ファイルのアップロードを許可するかどうか。デフォルトはTrue。

    Returns
    -------
    list[UploadedFile]
        アップロードされたファイルオブジェクトのリスト。
    """
    uploaded_files = st.file_uploader(  # type: ignore[call-overload]
        "PDF file(s) (.pdf)",
        type="pdf",
        accept_multiple_files=accept_multiple,
        help="PDF file(s) (.pdf), optional",
    )

    if uploaded_files is None:
        return []

    if isinstance(uploaded_files, list):
        return uploaded_files
    else:
        return [uploaded_files]


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


def save_pdfs(
    uploaded_files: list[UploadedFile],
    entry: Entry,
    base_dir: Union[Path, None] = None,
) -> int:
    """複数のPDFファイルを保存する。

    Parameters
    ----------
    uploaded_files : list[UploadedFile]
        アップロードされたPDFファイルのリスト
    entry : Entry
        対象の論文エントリ
    base_dir : Union[Path, None], optional
        PDFの基本ディレクトリ。Noneの場合はDIRPATH_PDFを使用。

    Returns
    -------
    int
        保存したファイル数
    """
    if not uploaded_files:
        return 0

    pdf_dir = entry.get_pdf_dir(base_dir)
    pdf_dir.mkdir(parents=True, exist_ok=True)

    saved_count = 0
    for uploaded_file in uploaded_files:
        filepath = pdf_dir / uploaded_file.name
        with open(filepath, mode="wb") as f:
            f.write(uploaded_file.getvalue())
        _logger.debug(f"PDF saved: {filepath}")
        saved_count += 1

    return saved_count


def delete_pdfs(
    entry: Entry,
    base_dir: Union[Path, None] = None,
    specific_files: Optional[list[Path]] = None,
) -> int:
    """PDFファイルを削除する。

    Parameters
    ----------
    entry : Entry
        対象の論文エントリ
    base_dir : Union[Path, None], optional
        PDFの基本ディレクトリ。Noneの場合はDIRPATH_PDFを使用。
    specific_files : Optional[list[Path]], optional
        削除する特定のファイル。Noneの場合は全PDFを削除。

    Returns
    -------
    int
        削除したファイル数
    """
    if base_dir is None:
        base_dir = DIRPATH_PDF

    if specific_files is not None:
        # 特定のファイルのみ削除
        deleted_count = 0
        for filepath in specific_files:
            if filepath.is_file():
                filepath.unlink()
                _logger.debug(f"PDF deleted: {filepath}")
                deleted_count += 1

        # ディレクトリが空になったら削除
        pdf_dir = entry.get_pdf_dir(base_dir)
        if pdf_dir.is_dir() and not any(pdf_dir.iterdir()):
            pdf_dir.rmdir()
            _logger.debug(f"Empty PDF directory deleted: {pdf_dir}")

        return deleted_count

    # 全PDFを削除（ディレクトリごと削除）
    pdf_dir = entry.get_pdf_dir(base_dir)
    if pdf_dir.is_dir():
        file_count = len(list(pdf_dir.glob("*.pdf")))
        shutil.rmtree(pdf_dir)
        _logger.debug(f"PDF directory deleted: {pdf_dir}")
        return file_count

    # 後方互換性: 旧形式の単一PDFファイルを削除
    legacy_pdf = base_dir / entry.pdf_filename
    if legacy_pdf.is_file():
        legacy_pdf.unlink()
        _logger.debug(f"Legacy PDF deleted: {legacy_pdf}")
        return 1

    return 0


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
    uploaded_files: Optional[list[UploadedFile]] = None,
) -> bool:
    """論文エントリをリストに登録する。

    重複チェックを行い、PDFファイルがあれば保存する。

    Parameters
    ----------
    paper_list : PaperList
        論文リスト
    entry : Entry
        登録する論文エントリ
    uploaded_files : Optional[list[UploadedFile]]
        アップロードされたPDFファイルのリスト

    Returns
    -------
    bool
        登録成功した場合True
    """
    _logger.debug(f"Registering entry: {entry}")

    # IDを設定
    entry["ID"] = entry.get_key(paper_list.keys())

    # PDFディレクトリ名で重複を確認する
    existing_pdf_dirs = {_entry.pdf_dir_name for _entry in paper_list.values()}
    if entry.pdf_dir_name in existing_pdf_dirs:
        st.error("FAIL: Duplicated")
        return False

    # ラインナップとして追加
    paper_list[entry["ID"]] = entry

    # PDFファイルを保存
    if uploaded_files:
        save_pdfs(uploaded_files, entry)

    # PaperListを保存
    save_paper_list(paper_list)

    st.success("SUCCESS: Registered")
    return True


def update_entry_in_list(
    paper_list: PaperList,
    key_original: str,
    entry_updated: Entry,
    uploaded_files: Optional[list[UploadedFile]] = None,
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
    uploaded_files : Optional[list[UploadedFile]]
        アップロードされたPDFファイルのリスト

    Returns
    -------
    bool
        更新成功した場合True
    """
    _logger.debug(f"Updating entry: {key_original}")

    original_entry = paper_list[key_original]

    # PDFディレクトリ名が変わらない場合は単純に更新
    if original_entry.pdf_dir_name == entry_updated.pdf_dir_name:
        paper_list[key_original] = entry_updated
    else:
        # PDFディレクトリ名が変わる場合
        # 1. 古いPDFディレクトリを新しい名前にリネーム
        old_pdf_dir = original_entry.get_pdf_dir()
        new_pdf_dir = entry_updated.get_pdf_dir()
        if old_pdf_dir.is_dir() and not new_pdf_dir.exists():
            old_pdf_dir.rename(new_pdf_dir)
            _logger.debug(
                f"PDF directory renamed: {old_pdf_dir} -> {new_pdf_dir}"
            )

        # 2. 新しいキーで登録
        new_id = entry_updated.get_key(paper_list.keys())
        del paper_list[key_original]
        entry_updated["ID"] = new_id
        paper_list[new_id] = entry_updated

    # 新しいPDFファイルを保存
    if uploaded_files:
        save_pdfs(uploaded_files, entry_updated)

    # PaperListを保存
    save_paper_list(paper_list)

    _logger.debug(f"Entry updated: {paper_list=}")
    return True


# 後方互換性のためのエイリアス（非推奨）
def save_pdf(
    uploaded_file_pdf: Optional[UploadedFile],
    filepath_pdf: Path,
) -> bool:
    """PDFファイルを保存する。

    .. deprecated::
        Use `save_pdfs()` instead for multiple PDF support.

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

    filepath_pdf.parent.mkdir(parents=True, exist_ok=True)

    with open(filepath_pdf, mode="wb") as f:
        f.write(uploaded_file_pdf.getvalue())

    _logger.debug(f"PDF saved: {filepath_pdf}")
    return True
