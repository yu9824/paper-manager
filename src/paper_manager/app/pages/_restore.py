import json
import shutil
import zipfile
from io import BytesIO
from pathlib import Path

import streamlit as st

from paper_manager import __version__
from paper_manager._constants import (
    DIRPATH_DATA,
    DIRPATH_PDF,
    ENCODING,
    FILEPATH_CONFIG,
    FILEPATH_LIST,
)
from paper_manager.app.helper import config_page
from paper_manager.entry import PaperList
from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)


def restore_from_zip(uploaded_file: BytesIO) -> tuple[bool, str]:
    """zipファイルからデータを復元する。

    Parameters
    ----------
    uploaded_file : BytesIO
        アップロードされたzipファイルのバイトストリーム

    Returns
    -------
    tuple[bool, str]
        (成功フラグ, メッセージ)
    """
    try:
        with zipfile.ZipFile(uploaded_file, "r") as zip_file:
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
                _logger.info(f"Restored list.json from backup")

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

            # セッションステートを更新
            PaperList.from_file().to_session_state()

            message = f"復元が完了しました。\n"
            if version_info:
                message += f"- バックアップ時のバージョン: {version_info.get('version', 'N/A')}\n"
                message += f"- バックアップ日時: {version_info.get('backup_date', 'N/A')}\n"
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


@config_page
def main() -> None:
    """復元ページのメインスクリプト。

    zipファイルをアップロードしてデータを復元できる。
    """
    st.header("復元")
    _logger.debug("Restore page Start")

    st.markdown(
        """
        このページでは、バックアップファイルからデータを復元できます。

        **注意事項:**
        - 復元を行うと、現在のデータが上書きされます
        - 既存のlist.jsonは自動的にバックアップされます（.json.backup）
        - 復元後はページを再読み込みしてください
        """
    )

    st.warning(
        "⚠️ 復元を実行すると、現在のデータが上書きされます。"
        "重要なデータがある場合は、事前にバックアップを取ってください。"
    )

    # ファイルアップロード
    st.subheader("バックアップファイルのアップロード")
    uploaded_file = st.file_uploader(
        "バックアップファイル（.zip）を選択してください",
        type="zip",
        help="paper-manager-backup_*.zip ファイルを選択",
    )

    if uploaded_file is not None:
        # ファイル情報を表示
        st.info(f"アップロードされたファイル: {uploaded_file.name} ({uploaded_file.size:,} bytes)")

        # プレビュー（zipファイルの内容を確認）
        try:
            with zipfile.ZipFile(uploaded_file, "r") as zip_file:
                file_list = zip_file.namelist()
                st.subheader("バックアップファイルの内容")
                st.code("\n".join(file_list), language="text")

                # バージョン情報を表示
                if "version.json" in file_list:
                    version_data = zip_file.read("version.json")
                    version_info = json.loads(version_data.decode(ENCODING))
                    st.json(version_info)

        except zipfile.BadZipFile:
            st.error("無効なzipファイルです。")
            return
        except Exception as e:
            st.error(f"ファイルの読み込み中にエラーが発生しました: {str(e)}")
            return

        # 復元ボタン
        st.subheader("復元の実行")
        if st.button("復元を実行", type="primary", icon=":material/restore:"):
            # ファイルポインタをリセット
            uploaded_file.seek(0)
            success, message = restore_from_zip(BytesIO(uploaded_file.getvalue()))

            if success:
                st.success(message)
                st.info("ページを再読み込みして、復元されたデータを確認してください。")
                st.rerun()
            else:
                st.error(message)

    _logger.debug("Restore page End")


if __name__ == "__main__":
    from logging import DEBUG

    _logger.setLevel(DEBUG)
    main()

