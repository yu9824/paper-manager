import json
import zipfile
from io import BytesIO

import streamlit as st

from paper_manager._constants import ENCODING
from paper_manager.app.helper import config_page
from paper_manager.app.helper._backup import restore_from_zip
from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)


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
        st.info(
            f"アップロードされたファイル: {uploaded_file.name} ({uploaded_file.size:,} bytes)"
        )

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
            success, message = restore_from_zip(
                BytesIO(uploaded_file.getvalue()), update_session_state=True
            )

            if success:
                st.success(message)
                st.info(
                    "ページを再読み込みして、復元されたデータを確認してください。"
                )
                st.rerun()
            else:
                st.error(message)

    _logger.debug("Restore page End")


if __name__ == "__main__":
    main()
