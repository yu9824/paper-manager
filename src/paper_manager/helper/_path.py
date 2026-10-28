r"""ファイル操作用のパスの変換と、PDF フォルダ名の比較キーを提供するモジュール.

Windows では、prefix のないパスでのファイル操作は、パス全体が MAX_PATH
(260 文字) を超えると失敗する。そこで、ファイル操作の起点となるパス
(PDF ルート、エントリの PDF フォルダなど) を、長さにかかわらず常に拡張長の
パス (``\\?\C:\...``、UNC は ``\\?\UNC\server\share\...``) に変換する。
起点から得た子のパス (``glob`` や ``iterdir`` の結果) は変換後の形のままに
なるため、比較や ``relative_to`` は変換後のパスどうしで行い、画面とログには
:func:`to_display_path` で prefix を外したパスを出す。Windows 以外では
パスを変えない。

変換の実体は、モジュールの属性 ``_convert`` / ``_unconvert`` を呼び出し時に
参照する。テストはこれを差し替えて、CI の Linux でも Windows のパスの扱いを
模すことができる (``from paper_manager.helper import to_fs_path`` のように
名前で import した呼び出し側にも差し替えが効く)。

主な関数:
    to_fs_path: ファイル操作用のパスに変換する
    to_display_path: 画面とログに出すためのパスに戻す
    folder_name_key: 大文字小文字と Unicode 正規化を無視した比較キー
"""

import ntpath
import os
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Union

from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)

PathLike = Union[str, "os.PathLike[str]"]
_Converter = Callable[[str], str]

_EXTENDED_PREFIX = "\\\\?\\"
_EXTENDED_UNC_PREFIX = "\\\\?\\UNC\\"
# ``\\?\`` (拡張長) と ``\\.\`` (device) のパスは変換せずにそのまま返す
# (正規化を飛ばすのは ``\\?\`` だけだが、``\\.\`` も保守的に変更しない)
_DEVICE_PREFIXES = (_EXTENDED_PREFIX, "\\\\.\\")
_UNC_ROOT = "\\\\"


def _to_extended_length(path: str) -> str:
    r"""Windows のパスを拡張長のパスに変換する.

    ``ntpath`` の意味論で動く純粋な文字列変換で、どの OS でも動く。
    ``\\?\`` のパスは ``/`` 区切りや ``.``・``..`` を解釈しないため、
    ``ntpath.abspath`` で絶対パスに正規化してから prefix を付ける。

    Parameters
    ----------
    path : str
        変換するパス。

    Returns
    -------
    str
        ``\\?\`` (UNC は ``\\?\UNC\``) で始まる拡張長のパス。既に
        ``\\?\`` または ``\\.\`` で始まるパスはそのまま返す (冪等)。
        正規化しても完全修飾 (ドライブとルートを持つ) にならないパス
        (Windows 以外での、ドライブ相対や相対のパス) は、prefix だけを
        付けると別の場所を指すため、そのまま返す。
    """
    if path.startswith(_DEVICE_PREFIXES):
        return path
    full = ntpath.abspath(path)
    if full.startswith(_DEVICE_PREFIXES):
        # ``//?/C:/a`` のように ``/`` で書かれた device パス
        return full
    drive, rest = ntpath.splitdrive(full)
    if drive.startswith(_UNC_ROOT):
        return _EXTENDED_UNC_PREFIX + full[len(_UNC_ROOT) :]
    if len(drive) == 2 and drive[1] == ":" and rest.startswith("\\"):
        return _EXTENDED_PREFIX + full
    return path


def _from_extended_length(path: str) -> str:
    r"""拡張長のパスから prefix を外す.

    :func:`_to_extended_length` の逆変換で、純粋な文字列変換である。

    Parameters
    ----------
    path : str
        prefix を外すパス。

    Returns
    -------
    str
        ``\\?\UNC\server\...`` は ``\\server\...``、``\\?\C:\...`` は
        ``C:\...`` にしたパス。prefix のないパスはそのまま返す。
    """
    if path.startswith(_EXTENDED_UNC_PREFIX):
        return _UNC_ROOT + path[len(_EXTENDED_UNC_PREFIX) :]
    if path.startswith(_EXTENDED_PREFIX):
        return path[len(_EXTENDED_PREFIX) :]
    return path


def _keep(path: str) -> str:
    """パスを変えずに返す (Windows 以外の変換と逆変換).

    Parameters
    ----------
    path : str
        パス。

    Returns
    -------
    str
        ``path`` そのもの。
    """
    return path


def _select_converters(os_name: str) -> "tuple[_Converter, _Converter]":
    """OS に応じた変換と逆変換の組を返す.

    Parameters
    ----------
    os_name : str
        ``os.name`` の値。

    Returns
    -------
    tuple[Callable[[str], str], Callable[[str], str]]
        ``(変換, 逆変換)``。``"nt"`` (Windows) では拡張長のパスへの変換と
        その逆変換、それ以外ではパスを変えない関数の組。
    """
    if os_name == "nt":
        return _to_extended_length, _from_extended_length
    return _keep, _keep


# 差し替え口: to_fs_path / to_display_path は呼び出し時にこの属性を参照する
_convert, _unconvert = _select_converters(os.name)


def to_fs_path(path: PathLike) -> Path:
    r"""ファイル操作に使うパスに変換する.

    Windows では、長さにかかわらず常に絶対パスに正規化してから拡張長の
    パス (``\\?\``、UNC は ``\\?\UNC\``) に変換する。既に変換済みの
    パスはそのまま返す。Windows 以外では ``Path(path)`` をそのまま返す。

    Parameters
    ----------
    path : str or os.PathLike[str]
        変換するパス。空であってはならない。

    Returns
    -------
    Path
        ファイル操作に使うパス。

    Raises
    ------
    ValueError
        ``path`` が空の場合 (カレントディレクトリを指してしまうため)。
    """
    raw = os.fspath(path)
    if not raw:
        raise ValueError("path must not be empty")
    return Path(_convert(raw))


def to_display_path(path: PathLike) -> str:
    r"""画面とログに出すためのパスを返す.

    :func:`to_fs_path` の逆変換で、拡張長のパスの prefix を外す。prefix の
    ないパスはそのまま文字列にする。``to_display_path(to_fs_path(p))`` は、
    Windows では ``os.path.abspath(p)``、それ以外では ``str(p)`` に等しい。

    Parameters
    ----------
    path : str or os.PathLike[str]
        表示するパス。

    Returns
    -------
    str
        prefix を外したパス。
    """
    return _unconvert(os.fspath(path))


def folder_name_key(name: str) -> str:
    """フォルダ名の同一性を判定する比較キーを返す.

    NFD → casefold → NFD (Unicode の canonical caseless match) で、
    大文字小文字だけ、または Unicode 正規化 (NFC と NFD など) だけが
    違う名前を同じキーにする。``casefold`` は ``ß`` と ``ss`` も同じに
    するため、NTFS (大文字小文字だけを区別しない) より広く一致する
    (同じとみなす安全側)。文字列だけから決まり、OS やファイルシステムに
    依存しない。

    Parameters
    ----------
    name : str
        フォルダ名。

    Returns
    -------
    str
        比較キー。
    """
    return unicodedata.normalize(
        "NFD", unicodedata.normalize("NFD", name).casefold()
    )
