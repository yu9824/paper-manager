"""ユーティリティ関数を提供するモジュール。

このモジュールは、paper-manager全体で使用される汎用的な
ユーティリティ関数を提供します。

主な関数:
    split: セパレータで文字列を分割
    is_installed: パッケージがインストールされているか確認
    dummy_func: ダミー関数（何もしない）
    is_argument: 関数のシグネチャに引数が存在するか確認
    deprecated: 関数を非推奨としてマークするデコレータ
    dummy_tqdm: tqdmのダミークラス（プログレスバーが不要な場合に使用）
    to_fs_path: ファイル操作用のパスに変換（Windowsでは拡張長のパス）
    to_display_path: 画面とログに出すためのパスに戻す（prefixを外す）
    folder_name_key: 大文字小文字とUnicode正規化を無視したフォルダ名の比較キー

"""

from ._helper import (
    deprecated,
    dummy_func,
    dummy_tqdm,
    is_argument,
    is_installed,
    split,
)
from ._path import folder_name_key, to_display_path, to_fs_path

__all__ = (
    "deprecated",
    "dummy_func",
    "dummy_tqdm",
    "folder_name_key",
    "is_argument",
    "is_installed",
    "split",
    "to_display_path",
    "to_fs_path",
)
