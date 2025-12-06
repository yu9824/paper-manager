import json
import zipfile
from datetime import datetime
from io import BytesIO

import streamlit as st

from paper_manager import __version__
from paper_manager._constants import (
    DIRPATH_DATA,
    DIRPATH_PDF,
    FILEPATH_CONFIG,
    FILEPATH_LIST,
)
from paper_manager.app.helper import config_page
from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)


def create_backup_zip() -> BytesIO:
    """バックアップ用のzipファイルを作成する。

    Returns
    -------
    BytesIO
        バックアップデータを含むzipファイルのバイトストリーム
    """
    zip_buffer = BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        # バージョン情報を保存
        version_info = {
            "version": __version__,
            "backup_date": datetime.now().isoformat(),
        }
        zip_file.writestr(
            "version.json",
            json.dumps(version_info, ensure_ascii=False, indent=2),
        )

        # list.jsonを保存
        if FILEPATH_LIST.is_file():
            zip_file.write(FILEPATH_LIST, "list.json")
            _logger.debug(f"Added list.json to backup: {FILEPATH_LIST}")

        # config.jsonを保存
        if FILEPATH_CONFIG.is_file():
            zip_file.write(FILEPATH_CONFIG, "config.json")
            _logger.debug(f"Added config.json to backup: {FILEPATH_CONFIG}")

        # PDFディレクトリを保存
        if DIRPATH_PDF.is_dir():
            pdf_files = list(DIRPATH_PDF.rglob("*.pdf"))
            for pdf_file in pdf_files:
                # 相対パスを保持（pdf/ から始まるパス）
                arcname = pdf_file.relative_to(DIRPATH_DATA)
                zip_file.write(pdf_file, str(arcname))
                _logger.debug(f"Added PDF to backup: {pdf_file} -> {arcname}")

    zip_buffer.seek(0)
    return zip_buffer


@config_page
def main() -> None:
    """バックアップページのメインスクリプト。

    バージョン情報、list.json、PDFファイルをzipにまとめてダウンロードできる。
    """
    st.header("バックアップ")
    _logger.debug("Backup page Start")

    st.markdown(
        """
        このページでは、paper-managerのデータをバックアップできます。

        **バックアップに含まれる内容:**
        - paper-managerのバージョン情報
        - 論文リスト（list.json）
        - 設定ファイル（config.json、プロキシ設定など）
        - すべてのPDFファイル

        バックアップファイルはzip形式でダウンロードされます。
        """
    )

    # 現在のデータ状況を表示
    st.subheader("現在のデータ状況")
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        if FILEPATH_LIST.is_file():
            file_size = FILEPATH_LIST.stat().st_size
            st.metric("list.json", f"{file_size:,} bytes")
        else:
            st.metric("list.json", "存在しません")

    with col2:
        if FILEPATH_CONFIG.is_file():
            file_size = FILEPATH_CONFIG.stat().st_size
            st.metric("config.json", f"{file_size:,} bytes")
        else:
            st.metric("config.json", "存在しません")

    with col3:
        if DIRPATH_PDF.is_dir():
            pdf_count = len(list(DIRPATH_PDF.rglob("*.pdf")))
            st.metric("PDFファイル数", pdf_count)
        else:
            st.metric("PDFファイル数", 0)

    with col4:
        st.metric("paper-manager バージョン", __version__)

    # バックアップ作成
    st.subheader("バックアップの作成")
    st.markdown(
        "以下のボタンをクリックしてバックアップファイルをダウンロードしてください。"
    )

    if st.button(
        "バックアップを作成", type="primary", icon=":material/download:"
    ):
        try:
            zip_buffer = create_backup_zip()

            # ファイル名に日時を含める
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"paper-manager-backup_{timestamp}.zip"

            st.download_button(
                label="バックアップをダウンロード",
                data=zip_buffer.getvalue(),
                file_name=filename,
                mime="application/zip",
                icon=":material/file_download:",
            )

            st.success("バックアップファイルの準備が完了しました。")
            _logger.info(f"Backup created: {filename}")

        except Exception as e:
            st.error(f"バックアップの作成中にエラーが発生しました: {str(e)}")
            _logger.exception("Failed to create backup")

    _logger.debug("Backup page End")


if __name__ == "__main__":
    from logging import DEBUG

    _logger.setLevel(DEBUG)
    main()
