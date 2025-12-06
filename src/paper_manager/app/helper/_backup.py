"""バックアップ・復元機能の共通モジュール。

このモジュールは、paper-managerのデータをバックアップ・復元するための
機能を提供します。

主な機能:
    - バックアップzipファイルの作成
    - バックアップzipファイルからの復元
    - バックアップ情報の取得

バックアップに含まれる内容:
    - paper-managerのバージョン情報
    - 論文リスト（list.json）
    - 設定ファイル（config.json）
    - すべてのPDFファイル

例:
    >>> from paper_manager.app.helper._backup import create_backup_zip
    >>> from pathlib import Path
    >>> backup_path = create_backup_zip("backup.zip")
    >>> print(backup_path)
    backup.zip

"""

import json
import shutil
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Optional, Union

from paper_manager import __version__
from paper_manager._constants import (
    DIRPATH_DATA,
    DIRPATH_PDF,
    ENCODING,
    FILEPATH_CONFIG,
    FILEPATH_LIST,
)
from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)


def create_backup_zip(output_path: Optional[Union[Path, str]] = None) -> Union[BytesIO, Path]:
    """バックアップ用のzipファイルを作成する。

    Parameters
    ----------
    output_path : Optional[Union[Path, str]], optional
        出力先のファイルパス。Noneの場合はBytesIOを返す。デフォルトはNone。

    Returns
    -------
    Union[BytesIO, Path]
        出力先が指定された場合はPath、そうでない場合はBytesIO
    """
    if output_path is None:
        zip_buffer = BytesIO()
        zip_file_obj = zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED)
        should_close = True
    else:
        output_path = Path(output_path)
        zip_file_obj = zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED)
        should_close = True

    try:
        # バージョン情報を保存
        version_info = {
            "version": __version__,
            "backup_date": datetime.now().isoformat(),
        }
        zip_file_obj.writestr(
            "version.json",
            json.dumps(version_info, ensure_ascii=False, indent=2),
        )

        # list.jsonを保存
        if FILEPATH_LIST.is_file():
            zip_file_obj.write(FILEPATH_LIST, "list.json")
            _logger.debug(f"Added list.json to backup: {FILEPATH_LIST}")

        # config.jsonを保存
        if FILEPATH_CONFIG.is_file():
            zip_file_obj.write(FILEPATH_CONFIG, "config.json")
            _logger.debug(f"Added config.json to backup: {FILEPATH_CONFIG}")

        # PDFディレクトリを保存
        if DIRPATH_PDF.is_dir():
            pdf_files = list(DIRPATH_PDF.rglob("*.pdf"))
            for pdf_file in pdf_files:
                # 相対パスを保持（pdf/ から始まるパス）
                arcname = pdf_file.relative_to(DIRPATH_DATA)
                zip_file_obj.write(pdf_file, str(arcname))
                _logger.debug(f"Added PDF to backup: {pdf_file} -> {arcname}")

    finally:
        if should_close:
            zip_file_obj.close()

    if output_path is None:
        zip_buffer.seek(0)
        return zip_buffer
    else:
        return output_path


def restore_from_zip(
    zip_path: Union[Path, str, BytesIO],
    update_session_state: bool = False,
) -> tuple[bool, str]:
    """zipファイルからデータを復元する。

    Parameters
    ----------
    zip_path : Union[Path, str, BytesIO]
        復元するzipファイルのパスまたはBytesIO
    update_session_state : bool, optional
        Streamlitのセッションステートを更新するかどうか。デフォルトはFalse。

    Returns
    -------
    tuple[bool, str]
        (成功フラグ, メッセージ)
    """
    try:
        if isinstance(zip_path, (Path, str)):
            zip_file = zipfile.ZipFile(zip_path, "r")
        else:
            zip_file = zipfile.ZipFile(zip_path, "r")

        with zip_file:
            # バージョン情報を確認
            version_info = None
            if "version.json" in zip_file.namelist():
                version_data = zip_file.read("version.json")
                version_info = json.loads(version_data.decode(ENCODING))
                _logger.debug(f"Backup version info: {version_info}")

            # list.jsonを復元
            if "list.json" in zip_file.namelist():
                # 既存のlist.jsonをバックアップ（存在する場合）
                if FILEPATH_LIST.is_file():
                    backup_path = FILEPATH_LIST.with_suffix(".json.backup")
                    shutil.copy2(FILEPATH_LIST, backup_path)
                    _logger.debug(f"Backed up existing list.json to {backup_path}")

                # 新しいlist.jsonを書き込み
                FILEPATH_LIST.parent.mkdir(parents=True, exist_ok=True)
                with open(FILEPATH_LIST, "wb") as f:
                    f.write(zip_file.read("list.json"))
                _logger.info("Restored list.json from backup")

            # config.jsonを復元（存在する場合のみ）
            config_restored = False
            if "config.json" in zip_file.namelist():
                try:
                    # 既存のconfig.jsonをバックアップ（存在する場合）
                    if FILEPATH_CONFIG.is_file():
                        backup_path = FILEPATH_CONFIG.with_suffix(".json.backup")
                        shutil.copy2(FILEPATH_CONFIG, backup_path)
                        _logger.debug(f"Backed up existing config.json to {backup_path}")

                    # 新しいconfig.jsonを書き込み
                    FILEPATH_CONFIG.parent.mkdir(parents=True, exist_ok=True)
                    with open(FILEPATH_CONFIG, "wb") as f:
                        f.write(zip_file.read("config.json"))
                    _logger.info("Restored config.json from backup")
                    config_restored = True
                except Exception as e:
                    _logger.warning(f"Failed to restore config.json: {e}")
            else:
                _logger.debug("config.json not found in backup, skipping")

            # PDFファイルを復元
            pdf_restored_count = 0
            for file_info in zip_file.filelist:
                file_path = Path(file_info.filename)

                # pdf/ で始まるファイルのみ処理
                if file_path.parts[0] == "pdf" and file_path.suffix == ".pdf":
                    # 完全なパスを構築
                    target_path = DIRPATH_DATA / file_path

                    # ディレクトリを作成
                    target_path.parent.mkdir(parents=True, exist_ok=True)

                    # ファイルを書き込み
                    with open(target_path, "wb") as f:
                        f.write(zip_file.read(file_info.filename))
                    pdf_restored_count += 1
                    _logger.debug(f"Restored PDF: {target_path}")

            # セッションステートを更新（Streamlitアプリの場合のみ）
            if update_session_state:
                try:
                    from paper_manager.entry import PaperList

                    PaperList.from_file().to_session_state()
                except ImportError:
                    _logger.warning(
                        "Streamlit is not available, skipping session state update"
                    )

            message = "復元が完了しました。\n"
            if version_info:
                message += (
                    f"- バックアップ時のバージョン: {version_info.get('version', 'N/A')}\n"
                )
                message += (
                    f"- バックアップ日時: {version_info.get('backup_date', 'N/A')}\n"
                )
            message += f"- 復元したPDFファイル数: {pdf_restored_count}\n"
            if config_restored:
                message += "- config.jsonを復元しました\n"
            message += f"- 現在のバージョン: {__version__}"

            return True, message

    except zipfile.BadZipFile:
        return False, "無効なzipファイルです。"
    except Exception as e:
        _logger.exception("Failed to restore from backup")
        return False, f"復元中にエラーが発生しました: {str(e)}"


def get_backup_info(zip_path: Union[Path, str, BytesIO]) -> Optional[dict]:
    """バックアップzipファイルの情報を取得する。

    Parameters
    ----------
    zip_path : Union[Path, str, BytesIO]
        バックアップzipファイルのパスまたはBytesIO

    Returns
    -------
    Optional[dict]
        バージョン情報を含む辞書。取得できない場合はNone。
    """
    try:
        if isinstance(zip_path, (Path, str)):
            zip_file = zipfile.ZipFile(zip_path, "r")
        else:
            zip_file = zipfile.ZipFile(zip_path, "r")

        with zip_file:
            if "version.json" in zip_file.namelist():
                version_data = zip_file.read("version.json")
                return json.loads(version_data.decode(ENCODING))
    except Exception as e:
        _logger.exception(f"Failed to read backup info: {e}")
    return None

