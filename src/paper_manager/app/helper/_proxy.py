"""プロキシ設定を管理するモジュール。"""

import json
import os
from typing import Optional

from paper_manager._constants import ENCODING, FILEPATH_CONFIG
from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)


def get_proxy_config() -> dict[str, Optional[str]]:
    """プロキシ設定を取得する。

    環境変数と設定ファイルの両方をチェックし、設定ファイルが優先される。

    Returns
    -------
    dict[str, Optional[str]]
        プロキシ設定の辞書。キーは 'http', 'https'。
    """
    config: dict[str, Optional[str]] = {"http": None, "https": None}

    # 設定ファイルから読み込み
    if FILEPATH_CONFIG.is_file():
        try:
            with open(FILEPATH_CONFIG, "r", encoding=ENCODING) as f:
                file_config = json.load(f)
                if "proxy" in file_config:
                    proxy_config = file_config["proxy"]
                    config["http"] = proxy_config.get("http")
                    config["https"] = proxy_config.get("https")
                    _logger.debug(f"Loaded proxy config from file: {config}")
        except Exception as e:
            _logger.warning(f"Failed to load proxy config from file: {e}")

    # 環境変数から読み込み（設定ファイルにない場合のみ）
    if config["http"] is None:
        env_http = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
        if env_http:
            config["http"] = env_http
    if config["https"] is None:
        env_https = (
            os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy")
            or config["http"]
        )
        if env_https:
            config["https"] = env_https

    # Noneの値を削除
    return {k: v for k, v in config.items() if v is not None}


def save_proxy_config(
    http_proxy: Optional[str], https_proxy: Optional[str]
) -> None:
    """プロキシ設定を保存する。

    Parameters
    ----------
    http_proxy : Optional[str]
        HTTPプロキシのURL（例: http://proxy.example.com:8080）
    https_proxy : Optional[str]
        HTTPSプロキシのURL（例: http://proxy.example.com:8080）
    """
    FILEPATH_CONFIG.parent.mkdir(parents=True, exist_ok=True)

    # 既存の設定を読み込み
    config = {}
    if FILEPATH_CONFIG.is_file():
        try:
            with open(FILEPATH_CONFIG, "r", encoding=ENCODING) as f:
                config = json.load(f)
        except Exception as e:
            _logger.warning(f"Failed to load existing config: {e}")

    # プロキシ設定を更新
    proxy_config = {}
    if http_proxy:
        proxy_config["http"] = http_proxy
    if https_proxy:
        proxy_config["https"] = https_proxy

    if proxy_config:
        config["proxy"] = proxy_config
    elif "proxy" in config:
        # プロキシ設定を削除
        del config["proxy"]

    # 設定を保存
    with open(FILEPATH_CONFIG, "w", encoding=ENCODING) as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    _logger.info(f"Saved proxy config: {proxy_config}")


def apply_proxy_to_environment() -> None:
    """プロキシ設定を環境変数に適用する。

    これにより、requestsライブラリなどが自動的にプロキシを使用する。
    """
    proxy_config = get_proxy_config()

    http_proxy = proxy_config.get("http")
    if http_proxy:
        os.environ["HTTP_PROXY"] = http_proxy
        os.environ["http_proxy"] = http_proxy
        _logger.debug(f"Set HTTP_PROXY: {http_proxy}")

    https_proxy = proxy_config.get("https")
    if https_proxy:
        os.environ["HTTPS_PROXY"] = https_proxy
        os.environ["https_proxy"] = https_proxy
        _logger.debug(f"Set HTTPS_PROXY: {https_proxy}")
