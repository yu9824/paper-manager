"""設定ページ。"""

import streamlit as st

from paper_manager.app.helper import config_page
from paper_manager.app.helper._proxy import (
    get_proxy_config,
    save_proxy_config,
)
from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)


@config_page
def main() -> None:
    """設定ページのメインスクリプト。

    プロキシ設定などを管理できる。
    """
    st.header("⚙️ 設定")
    _logger.debug("Settings page Start")

    # プロキシ設定セクション
    st.subheader("🌐 プロキシ設定")

    st.markdown(
        """
        プロキシサーバーを使用してDOIからメタデータを取得する場合、以下の設定を行ってください。

        **形式:** `http://proxy.example.com:8080` または `http://username:password@proxy.example.com:8080`
        """
    )

    # 現在の設定を取得
    current_config = get_proxy_config()

    with st.form("proxy_settings", clear_on_submit=False):
        http_proxy = st.text_input(
            "HTTPプロキシ",
            value=current_config.get("http", ""),
            help="HTTPプロキシのURL（例: http://proxy.example.com:8080）",
            placeholder="http://proxy.example.com:8080",
        )

        https_proxy = st.text_input(
            "HTTPSプロキシ",
            value=current_config.get("https", ""),
            help="HTTPSプロキシのURL（空欄の場合はHTTPプロキシと同じ）",
            placeholder="http://proxy.example.com:8080",
        )

        submitted = st.form_submit_button(
            "💾 設定を保存",
            type="primary",
            use_container_width=True,
        )

        if submitted:
            # 空文字列をNoneに変換
            http_proxy = http_proxy.strip() if http_proxy.strip() else None
            https_proxy = https_proxy.strip() if https_proxy.strip() else None

            try:
                save_proxy_config(http_proxy, https_proxy)
                st.success("✅ プロキシ設定を保存しました。")
                _logger.info(
                    f"Proxy settings saved: http={http_proxy}, https={https_proxy}"
                )
            except Exception as e:
                st.error(f"❌ 設定の保存に失敗しました: {str(e)}")
                _logger.exception("Failed to save proxy settings")

    # 現在の設定を表示
    st.divider()
    st.subheader("📋 現在の設定")

    if current_config:
        with st.container(border=True):
            if current_config.get("http"):
                st.markdown(f"**HTTPプロキシ:** `{current_config['http']}`")
            if current_config.get("https"):
                st.markdown(f"**HTTPSプロキシ:** `{current_config['https']}`")
            if not current_config:
                st.info(
                    "プロキシ設定はありません。環境変数も確認してください。"
                )
    else:
        st.info("プロキシ設定はありません。")

    # 環境変数の説明
    with st.expander("ℹ️ 環境変数について"):
        st.markdown(
            """
            プロキシ設定は以下の優先順位で適用されます：

            1. **設定ファイル** (`~/.paper-manager/config.json`) - 最優先
            2. **環境変数** (`HTTP_PROXY`, `HTTPS_PROXY`)

            環境変数が設定されている場合でも、設定ファイルの値が優先されます。
            環境変数のみを使用したい場合は、設定ファイルのプロキシ設定を空欄にして保存してください。
            """
        )

    _logger.debug("Settings page End")


if __name__ == "__main__":
    from logging import DEBUG

    _logger.setLevel(DEBUG)
    main()
