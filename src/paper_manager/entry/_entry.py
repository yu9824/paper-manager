import errno
import json
import logging
import os
import platform
import re
import shutil
from collections.abc import (
    Collection,
    ItemsView,
    KeysView,
    Mapping,
    MutableMapping,
    Sequence,
    ValuesView,
)
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Literal, NamedTuple, Optional, Union, cast

import streamlit as st

from paper_manager._constants import (
    AUTHOR_SEPARATOR,
    COLNAME_AUTHOR,
    COLNAME_TITLE,
    COLNAME_YEAR,
    DIRPATH_PDF,
    ENCODING,
    FILEPATH_LIST,
)
from paper_manager.helper import (
    deprecated,
    split,
    to_display_path,
    to_fs_path,
)
from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)

KEY_PAPER_LIST = "paper_list"
# 画面に出した PDF の移行・アクセスの警告 (同じ警告を繰り返さないための記録)
KEY_SHOWN_PDF_WARNINGS = "shown_pdf_warnings"

MAX_DIRNAME_LENGTH = 200  # フォルダ名の最大長
# 修正前の規則の名前のフォルダを作れた長さの上限 (いずれかの OS で作成
# できた長さ)。これを超える修正前の名前のフォルダは存在しえない
_MAX_LEGACY_DIRNAME_LENGTH = 255

# BibTeX の引用キーとして読めない文字のうち、空白以外の文字。pybtex は引用
# キーを空白・``,``・``}`` の手前までとして読む。``{`` は波括弧の対応を崩さない
# ために合わせて除く
_UNREADABLE_KEY_CHARS = frozenset(",{}")

# 論文リストのファイルが壊れているときに画面に出す警告 (修正前からの文言)
_BROKEN_PAPER_LIST_WARNING = "Broken paper list (json)"
# 論文リストのファイルを読むときの復号。先頭の UTF-8 の BOM (古いメモ帳や
# PowerShell 5 が付ける) を無視する。BOM がなければ ``ENCODING`` と同じ。
# 保存 (``PaperList.to_file``) は BOM を付けない ``ENCODING`` で書く
_PAPER_LIST_DECODING = "utf-8-sig"
# 壊れた論文リストのファイルの退避先の名前の日時の部分の書式。退避先は
# ``<ファイル名>.broken-<日時>`` (衝突時は ``-1``、``-2`` と連番)。復元が
# 使う ``list.json.backup`` (復元のたびに上書きされる) とは別の名前にする
_BROKEN_BACKUP_TIME_FORMAT = "%Y%m%d_%H%M%S"


def sanitize_citation_key(key: str) -> str:
    """Remove the characters that BibTeX cannot read from a citation key.

    pybtex reads the citation key of a brace-delimited entry up to the next
    whitespace, ``,`` or ``}``. Whitespace (every character for which
    :meth:`str.isspace` is true), ``,``, ``{`` and ``}`` are removed (``{`` is
    removed as well so that braces stay balanced). Every other character
    (``.``, ``-``, ``'``, ``\\``, non-ASCII characters and so on) is kept, so
    a key that is readable today does not change.

    Parameters
    ----------
    key : str
        A citation key.

    Returns
    -------
    str
        The key without the characters above, or ``Unknown`` when nothing
        is left. Applying the function again returns the same key.
    """
    cleaned = "".join(
        c for c in key if not (c.isspace() or c in _UNREADABLE_KEY_CHARS)
    )
    return cleaned or "Unknown"


def unique_citation_keys(keys: Sequence[str]) -> list[str]:
    """Return citation keys that are unique ignoring case.

    pybtex treats citation keys case-insensitively and raises an exception
    when two keys differ only in case. Each key is cleaned with
    :func:`sanitize_citation_key`. A key that duplicates an earlier one
    (ignoring case) gets the suffix ``_<n>`` (the smallest ``n`` >= 1 that no
    other key, earlier or later, uses). The first occurrence of a key is
    never changed.

    Parameters
    ----------
    keys : Sequence[str]
        Citation keys, for example the IDs of the entries to export.

    Returns
    -------
    list[str]
        Keys in the same length and order as ``keys``, with no duplicates
        ignoring case. Without duplicates, each element is
        ``sanitize_citation_key`` applied to the input element.
    """
    sanitized = [sanitize_citation_key(key) for key in keys]
    # pybtex compares keys with ``str.lower``. Every cleaned key is reserved
    # up front so that a generated key never takes over a later key.
    taken = {key.lower() for key in sanitized}
    seen: set[str] = set()
    result: list[str] = []
    for key in sanitized:
        if key.lower() in seen:
            n = 1
            while f"{key}_{n}".lower() in taken:
                n += 1
            key = f"{key}_{n}"
            taken.add(key.lower())
        seen.add(key.lower())
        result.append(key)
    return result


def _warn_pdf_problem(message: str, level: int) -> None:
    """Log a PDF migration or access problem and show it on the screen.

    同じ警告 (同じ対象と理由) は、セッションの中で 1 回だけログと画面
    (``st.warning``) に出す。PDF の一覧は、1 回の描画の中で一覧の件数・
    統計・孤立 PDF の判定のそれぞれから呼ばれるため、そのたびに同じ警告を
    出さないようにする。出した警告は session state に記録する
    (``streamlit run`` の外の bare mode でも、session state は使える)。

    Parameters
    ----------
    message : str
        The message, with display paths (see ``to_display_path``).
    level : int
        The logging level (``logging.WARNING`` or ``logging.ERROR``).
    """
    shown = st.session_state.setdefault(KEY_SHOWN_PDF_WARNINGS, set())
    if message in shown:
        return
    shown.add(message)
    _logger.log(level, message)
    st.warning(message)


def _reason(error: OSError) -> str:
    """Return the reason of ``error`` without the path it carries.

    ``str(error)`` には変換後のパス (Windows では拡張長のパスの prefix
    付き) が含まれるため、画面とログには理由だけを出す。

    Parameters
    ----------
    error : OSError
        The error.

    Returns
    -------
    str
        ``error.strerror``, or ``str(error)`` when it has no ``strerror``.
    """
    return error.strerror or str(error)


def _is_dir(path: Path) -> bool:
    """Return whether ``path`` is a directory, without raising.

    Parameters
    ----------
    path : Path
        The path to check.

    Returns
    -------
    bool
        ``True`` if ``path`` is a directory. ``False`` if it is not, or if
        it cannot be checked. A name too long for the file system cannot
        exist, so it is ``False`` silently. Other failures are reported
        with ``_warn_pdf_problem``.
    """
    try:
        return path.is_dir()
    except OSError as e:
        _report_unreadable(path, e)
        return False


def _is_file(path: Path) -> bool:
    """Return whether ``path`` is a regular file, without raising.

    Parameters
    ----------
    path : Path
        The path to check.

    Returns
    -------
    bool
        ``True`` if ``path`` is a regular file. ``False`` if it is not, or
        if it cannot be checked (see ``_is_dir``).
    """
    try:
        return path.is_file()
    except OSError as e:
        _report_unreadable(path, e)
        return False


def _report_unreadable(path: Path, error: OSError) -> None:
    """Report that ``path`` could not be checked, unless its name is too long.

    名前が長すぎるパス (Linux の 255 バイトを超える名前など) のファイルや
    フォルダは作れないので、存在しないものとして報告しない。

    Parameters
    ----------
    path : Path
        The path that could not be checked.
    error : OSError
        The error raised by the check.
    """
    if error.errno == errno.ENAMETOOLONG:
        _logger.debug("Name too long to exist: %s", to_display_path(path))
        return
    _warn_pdf_problem(
        f"Failed to access {to_display_path(path)}: {_reason(error)}",
        logging.ERROR,
    )


def sanitize_filename(filename: str) -> str:
    """Sanitize a filename by replacing invalid characters with underscores.

    This function ensures that filenames do not contain characters that are
    invalid or problematic for different operating systems. On Windows, it
    removes characters such as `\\ / : * ? " < > |`, while on Linux and macOS,
    it only removes `/`.

    Parameters
    ----------
    filename : str
        The original filename that may contain invalid characters.

    Returns
    -------
    str
        A sanitized filename with invalid characters replaced by underscores.

    Example
    -------
    >>> sanitize_filename("invalid:file/name.txt")
    'invalid_file_name.txt'

    >>> sanitize_filename("C:\\\\Windows\\\\System32")
    'C__Windows_System32'

    Notes
    -----
    - This function does not guarantee that the resulting filename is unique
      or valid in all scenarios.
    - It does not check for reserved filenames (e.g., `CON`, `PRN` on Windows).

    """
    # OSごとに不適切な文字を定義
    if platform.system() == "Windows":
        # Windowsでは \\ / : * ? " < > | が不適切
        invalid_chars = r'[\\/:*?"<>|]'
    else:
        # LinuxとOSXでは / が不適切
        invalid_chars = r"[/]"

    # 不適切な文字をアンダースコアに置換
    return re.sub(invalid_chars, "_", filename)


def _dir_name_before_kb7_fix(year: str, first_author: str, title: str) -> str:
    """Build a PDF directory name with the rule used before the KB7 fix.

    後方互換のための修正前の規則。KB7 の範囲の移行と孤立判定だけで使う。
    known-bug-fixes spec の修正前の ``Entry.pdf_dir_name`` の処理
    (フィールドを取り出した後の部分) を変えずに残したもの。接頭辞
    ``<year> - <first_author> - `` が 198・199 文字のとき、または年が
    200 文字を超えるとき (KB7 の範囲) は、200 文字を超える名前を返すことが
    ある。200 文字以内の空でない名前を返す入力では ``Entry.pdf_dir_name``
    と同じ名前になる (空の名前のとき ``Entry.pdf_dir_name`` は
    ``Unknown`` を返す)。

    Parameters
    ----------
    year : str
        The year field (or its default).
    first_author : str
        The first author (or its default).
    title : str
        The title field (or its default).

    Returns
    -------
    str
        The sanitized directory name without trailing ``.`` or spaces.
        It may exceed ``MAX_DIRNAME_LENGTH`` characters.
    """
    # 基本フォーマット
    base_format = "{} - {} - {}"
    dir_name = sanitize_filename(base_format.format(year, first_author, title))

    # フォルダ名が長すぎる場合はタイトルを切り詰める
    if len(dir_name) > MAX_DIRNAME_LENGTH:
        # 年と著者名の長さを計算
        prefix = f"{year} - {first_author} - "
        max_title_length = MAX_DIRNAME_LENGTH - len(prefix)

        if max_title_length > 0:
            # タイトルを切り詰める（末尾に...を追加）
            # 修正前の規則のまま: max_title_length が 1・2 のとき負の
            # インデックスになり、タイトルがほぼ全文残る (KB7)
            truncated_title = title[: max_title_length - 3] + "..."
            dir_name = sanitize_filename(
                base_format.format(year, first_author, truncated_title)
            )
        else:
            # それでも長すぎる場合は、年と著者名のみ
            dir_name = sanitize_filename(f"{year} - {first_author}")
            if len(dir_name) > MAX_DIRNAME_LENGTH:
                # 最後の手段：年のみ (修正前の規則のまま: 年は切り詰めない)
                dir_name = sanitize_filename(year)

    # Windowsの制限: ディレクトリ名の最後のピリオド（.）やスペースを削除
    # macOS/Linuxでも互換性のため同様に処理
    return dir_name.rstrip(". ")


class Entry(MutableMapping):
    """論文エントリを表す辞書風のクラス。

    このクラスは、学術論文の情報（著者、タイトル、ジャーナル、DOIなど）を
    管理するための辞書風のインターフェースを提供します。

    特徴:
        - 辞書のようにアクセス可能（``entry["title"]``）
        - 値の前後の空白を自動的に削除
        - 一意なキーの生成機能
        - 複数のPDFファイルをサポート（各エントリごとに専用ディレクトリ）

    PDFファイルの管理:
        各エントリは、`pdf_dir_name`プロパティで生成されるディレクトリ名の
        フォルダにPDFファイルを保存します。複数のPDFファイルをサポートしており、
        各ファイルは`00.pdf`, `01.pdf`, `02.pdf`のように連番で命名されます。

        旧バージョンとの互換性:
            - `pdf_filename`プロパティは非推奨（deprecated）です
            - 旧形式のPDFファイルは自動的に新しい形式に移行されます

    属性:
        pdf_dir_name: PDFファイルを保存するディレクトリ名を生成するプロパティ
            （形式: ``'<year> - <first_author> - <title>'``）

    メソッド:
        get_key: 既存のキーと重複しない一意なキーを生成
        get_pdf_dir: PDFファイルを保存するディレクトリのパスを取得
        get_pdf_files: このエントリに関連するPDFファイルのリストを取得
        has_pdf: PDFファイルが存在するかどうかを確認
        get_pdf_count: 関連するPDFファイルの数を取得

    例:
        >>> entry = Entry({
        ...     "ENTRYTYPE": "article",
        ...     "title": "Example Paper",
        ...     "author": "John Doe",
        ...     "year": "2024"
        ... })
        >>> entry["title"]
        'Example Paper'
        >>> entry.pdf_dir_name
        '2024 - John Doe - Example Paper'
        >>> pdf_files = entry.get_pdf_files()
        >>> len(pdf_files)
        2  # 複数のPDFファイルが存在する場合

    """

    def __init__(
        self, __mapping: Mapping[Union[str, Literal["ENTRYTYPE", "ID"]], str]
    ):
        """Initialize an Entry object with a given mapping.

        Parameters
        ----------
        __mapping : Mapping[Union[str, Literal["ENTRYTYPE"]], str]
            A mapping containing entry data where keys are strings and values are stripped strings.
        """
        super().__init__()
        self.__mapping = {
            self._normalize_key(key): value.strip()
            for key, value in __mapping.items()
        }

    def _normalize_key(self, key: str) -> str:
        """Normalize a key according to the entry's key naming convention.

        Keys "ID", "ENTRYTYPE", and "DOI" are kept in uppercase, while all other keys
        are converted to lowercase.

        Parameters
        ----------
        key : str
            The key to normalize.

        Returns
        -------
        str
            The normalized key (uppercase for "ID"/"ENTRYTYPE"/"DOI", lowercase for others).
        """
        if key.upper() in {"ID", "ENTRYTYPE", "DOI"}:
            return key.upper()
        return key.lower()

    def __getitem__(self, key: str) -> str:
        """Retrieve the value associated with the given key.

        Parameters
        ----------
        key : str
            The key to look up.

        Returns
        -------
        str
            The value associated with the key.
        """
        return self.__mapping[key]

    def __setitem__(self, key: str, value: str) -> None:
        """Set the value for a given key.

        Parameters
        ----------
        key : str
            The key to update.
        value : str
            The value to assign to the key.
        """
        self.__mapping[key] = value

    def __delitem__(self, key: str) -> None:
        """Delete a key-value pair from the mapping.

        Parameters
        ----------
        key : str
            The key to remove.
        """
        del self.__mapping[key]

    def __str__(self) -> str:
        """Return a string representation of the mapping."""
        return "{}({})".format(self.__class__.__name__, self.__mapping)

    def __repr__(self) -> str:
        """Return a string representation suitable for debugging."""
        return str(self)

    def __iter__(self):
        """Return an iterator over the keys of the mapping."""
        return iter(self.__mapping)

    def __len__(self):
        """Return the number of key-value pairs in the mapping."""
        return len(self.__mapping)

    def items(self) -> ItemsView[str, str]:
        """Return a view of the mapping's items."""
        return super().items()

    def values(self) -> ValuesView[str]:
        """Return a view of the mapping's values."""
        return super().values()

    def keys(self) -> KeysView[str]:
        """Return a view of the mapping's keys."""
        return super().keys()

    def get_key(self, keys: Collection[str]) -> str:
        """Generate a unique key using the 'author' and 'year' fields.

        Parameters
        ----------
        keys : Collection[str]
            A collection of existing keys to avoid duplicates.

        Returns
        -------
        str
            A unique key based on the first author's name and the year,
            ``<first_author><year>_<n>``. Whitespace, ``,``, ``{`` and ``}``
            are removed (see :func:`sanitize_citation_key`) because BibTeX
            cannot read them in a citation key. The duplicate check against
            ``keys`` is case-sensitive.
        """
        i = 0
        st_keys = set(keys)

        first_author = (
            self[COLNAME_AUTHOR].split(AUTHOR_SEPARATOR)[0]
            if COLNAME_AUTHOR in self
            else "Unknown"
        )
        while (
            key := sanitize_citation_key(
                "{0}{1}_{2}".format(
                    first_author, self.get(COLNAME_YEAR, "YYYY"), i
                )
            )
        ) in st_keys:
            i += 1
        return key

    def _pdf_dir_name_fields(self) -> tuple[str, str, str]:
        """Return the year, first author and title used in the folder name.

        Missing fields fall back to ``YYYY`` (year) and ``Unknown`` (title).
        The first author is ``Unknown`` when the author is missing or empty.

        Returns
        -------
        tuple[str, str, str]
            ``(year, first_author, title)``.
        """
        year = self.get(COLNAME_YEAR, "YYYY")
        authors = split(self.get(COLNAME_AUTHOR, ""), AUTHOR_SEPARATOR)
        first_author = authors[0] if authors else ""
        if first_author == "":
            first_author = "Unknown"
        title = self.get(COLNAME_TITLE, "Unknown")
        return year, first_author, title

    @property
    def pdf_dir_name(self) -> str:
        """Generate a sanitized directory name for the entry's PDF files.

        The directory name format is '<year> - <first_author> - <title>'.
        If the resulting name is too long, the title will be truncated to
        fit within the maximum directory name length. If it is still too
        long, '<year> - <first_author>' is used, and then the year cut to
        the maximum length. A name that would be empty is replaced with
        ``Unknown``. The name does not depend on where the PDF directory
        is located.

        Returns
        -------
        str
            A sanitized, non-empty directory name for the entry. It has at
            most ``MAX_DIRNAME_LENGTH`` characters and does not end with
            ``.`` or a space. A non-empty name that fits with the rule used
            before the KB7 fix is returned unchanged, so existing folders
            keep their names.
        """
        year, first_author, title = self._pdf_dir_name_fields()

        dir_name = _dir_name_before_kb7_fix(year, first_author, title)
        if len(dir_name) > MAX_DIRNAME_LENGTH:
            # KB7: 修正前の規則で長すぎる場合だけ、年と著者名のみにする
            dir_name = sanitize_filename(f"{year} - {first_author}")
            dir_name = dir_name.rstrip(". ")
        if len(dir_name) > MAX_DIRNAME_LENGTH:
            # 最後の手段：年のみ（長さの上限で切り詰める）
            dir_name = sanitize_filename(year)[:MAX_DIRNAME_LENGTH]
            dir_name = dir_name.rstrip(". ")

        # 空の名前は PDF ディレクトリそのものを指すため使わない
        # (修正前の規則でも、年が空や `.` だけで著者名が長いと空になる)
        return dir_name or "Unknown"

    def _pdf_dir_name_before_kb7_fix(self) -> Optional[str]:
        """Return the directory name built with the rule before the KB7 fix.

        後方互換のための修正前の規則。KB7 の範囲の移行と孤立判定だけで使う。
        KB7 の範囲では ``pdf_dir_name`` と異なる、200 文字を超える名前を
        返す。それ以外の入力では ``pdf_dir_name`` と同じ名前を返す。

        修正前の規則は、著者のキーがない、または著者が空のときに
        ``IndexError`` になり (KB2a)、その名前のフォルダは作られなかった。
        この場合は例外を送出せずに ``None`` を返す。修正前の規則の名前が
        空になる場合 (PDF ディレクトリそのものを指し、フォルダ名になり
        えない) も ``None`` を返す。

        Returns
        -------
        Optional[str]
            The pre-fix directory name, or ``None`` when the pre-fix rule
            could not build a folder name (missing or empty author, or an
            empty name). It never raises.
        """
        if not self.get(COLNAME_AUTHOR, ""):
            return None
        dir_name = _dir_name_before_kb7_fix(*self._pdf_dir_name_fields())
        return dir_name or None

    @property
    def legacy_pdf_dir_name(self) -> Optional[str]:
        """Return the folder name built with the rule before the KB7 fix.

        後方互換のための修正前の規則の名前。KB7 の範囲で修正後の名前
        (``pdf_dir_name``) と異なり、いずれかの OS でフォルダを作れた長さ
        (255 文字以内) のときだけ値を持つ。その名前のフォルダは、PDF の
        一覧の取得時に現在の名前へ移す (KB7 の範囲の移行と孤立判定だけで
        使う)。

        Returns
        -------
        Optional[str]
            The pre-fix folder name when it differs from ``pdf_dir_name``
            and has at most 255 characters, otherwise ``None``. It never
            raises.
        """
        dir_name = self._pdf_dir_name_before_kb7_fix()
        if (
            dir_name is None
            or dir_name == self.pdf_dir_name
            or len(dir_name) > _MAX_LEGACY_DIRNAME_LENGTH
        ):
            return None
        return dir_name

    def get_pdf_dir(self, base_dir: Union[Path, None] = None) -> Path:
        """Get the directory path for storing PDF files.

        このエントリ専用のPDFディレクトリのパスを返します。
        複数のPDFファイルは、このディレクトリ内に`00.pdf`, `01.pdf`のように
        連番で保存されます。パスは、ファイル操作用に変換した PDF ルート
        (``to_fs_path``。Windows では拡張長のパス) から作るので、パス全体の
        長さにかかわらず、そのままファイル操作に使えます。画面とログには
        ``to_display_path`` で戻したパスを出します。

        Parameters
        ----------
        base_dir : Union[Path, None], optional
            Base directory for PDF storage. If None, uses DIRPATH_PDF.

        Returns
        -------
        Path
            ``to_fs_path(base_dir or DIRPATH_PDF) / pdf_dir_name``.
        """
        return to_fs_path(base_dir or DIRPATH_PDF) / self.pdf_dir_name

    def get_pdf_files(self, base_dir: Union[Path, None] = None) -> list[Path]:
        """Get a list of PDF files associated with this entry.

        このエントリに関連するすべてのPDFファイルのリストを返します。
        ファイルは`00.pdf`, `01.pdf`, `02.pdf`のように連番で命名され、
        ソートされた順序で返されます。

        旧バージョンとの互換性:
            一覧を作る前に、次の順で旧形式から移行します。どの移行も
            既存のファイルを上書きしません。

            1. 現在の名前のフォルダがなく、修正前の規則の名前
               (``legacy_pdf_dir_name``) のフォルダがあれば、現在の名前へ
               移す。移す先が既にあれば移さない
            2. 旧形式の単一 PDF (``base_dir/pdf_filename``) があれば、
               フォルダの有無にかかわらず、フォルダの空いている番号へ移す
            3. フォルダ内の旧命名規則のファイル名を ``NN.pdf`` に変える

            移行に失敗したとき、または移す先が既にあるため移さなかった
            ときは、ログと画面の警告 (表示用のパス) の両方に出します
            (同じ警告はセッションの中で 1 回だけ)。

        Parameters
        ----------
        base_dir : Union[Path, None], optional
            Base directory for PDF storage. If None, uses DIRPATH_PDF.

        Returns
        -------
        list[Path]
            The PDF files in the folder sorted by name, followed by the
            legacy single PDF when it could not be moved into the folder
            (so that it stays visible). Paths are under the converted PDF
            root (see ``get_pdf_dir``). Empty list if no PDFs exist. It
            never raises.
        """
        pdf_root = to_fs_path(base_dir or DIRPATH_PDF)
        self._migrate_legacy_pdf_dir(pdf_root)
        left_behind = self._migrate_legacy_single_pdf(base_dir)

        pdf_dir = self.get_pdf_dir(base_dir)
        pdf_files: list[Path] = []
        if _is_dir(pdf_dir):
            # 後方互換性: 古い命名規則のファイルを新しい命名規則に変換
            self._migrate_legacy_pdf_filenames(base_dir)
            try:
                pdf_files = sorted(pdf_dir.glob("*.pdf"))
            except OSError as e:
                _warn_pdf_problem(
                    "Failed to access PDF directory: "
                    f"{to_display_path(pdf_dir)}: {_reason(e)}",
                    logging.ERROR,
                )
        if left_behind is not None:
            pdf_files.append(left_behind)
        return pdf_files

    def _migrate_legacy_pdf_dir(self, pdf_root: Path) -> None:
        """Move the folder with the pre-fix name to the current name.

        後方互換のための移行 (KB7 の範囲)。現在の名前 (``pdf_dir_name``)
        のフォルダがなく、修正前の規則の名前 (``legacy_pdf_dir_name``) の
        フォルダがあれば、現在の名前へ改名する。現在の名前のフォルダや
        ファイルが既にあれば、上書きしないよう移さずに警告する (POSIX の
        改名は空のフォルダを置き換えるため、移す前に存在を確かめる)。

        Parameters
        ----------
        pdf_root : Path
            The converted PDF root (``to_fs_path`` of the base directory).
        """
        legacy_name = self.legacy_pdf_dir_name
        if legacy_name is None:
            return
        legacy_dir = pdf_root / legacy_name
        if not _is_dir(legacy_dir):
            return

        pdf_dir = pdf_root / self.pdf_dir_name
        source = to_display_path(legacy_dir)
        destination = to_display_path(pdf_dir)
        if os.path.lexists(pdf_dir):
            _warn_pdf_problem(
                "PDF folder was not migrated because the destination "
                f"already exists: {source} -> {destination}",
                logging.WARNING,
            )
            return
        try:
            legacy_dir.rename(pdf_dir)
        except OSError as e:
            _warn_pdf_problem(
                f"Failed to migrate PDF folder: {source} -> {destination}: "
                f"{_reason(e)}",
                logging.ERROR,
            )
            return
        _logger.info("PDF folder migrated: %s -> %s", source, destination)

    def _next_free_pdf_index(self, pdf_dir: Path, start: int = 0) -> int:
        """Return the smallest number whose ``NN.pdf`` does not exist.

        存在の確認には ``os.path.lexists`` を使う。大文字小文字を区別しない
        ファイルシステムでは ``00.PDF`` があれば ``00.pdf`` も存在するとみなす
        ので、既存のファイルを改名で上書きしない。

        Parameters
        ----------
        pdf_dir : Path
            The PDF folder of this entry.
        start : int, default 0
            The first number to try.

        Returns
        -------
        int
            The smallest number ``>= start`` such that
            ``pdf_dir / _generate_pdf_filename(number)`` does not exist.
        """
        index = start
        while os.path.lexists(pdf_dir / self._generate_pdf_filename(index)):
            index += 1
        return index

    def _migrate_legacy_single_pdf(
        self, base_dir: Union[Path, None] = None
    ) -> Optional[Path]:
        """Migrate a legacy single PDF file to the new directory-based format.

        旧形式の単一PDFファイル（base_dir/pdf_filename形式）を、PDF
        フォルダ（base_dir/pdf_dir_name/）の空いている番号（``NN.pdf``）へ
        移します。フォルダが既にあっても移し、既存の PDF は上書きしません。
        失敗したときは、ログと画面の警告に出します。

        Parameters
        ----------
        base_dir : Union[Path, None], optional
            Base directory for PDF storage. If None, uses DIRPATH_PDF.

        Returns
        -------
        Optional[Path]
            The legacy single PDF when it exists but could not be moved
            (it is left in place), otherwise ``None`` (moved, or there is
            no legacy single PDF).
        """
        legacy_pdf = to_fs_path(base_dir or DIRPATH_PDF) / self.pdf_filename
        if not _is_file(legacy_pdf):
            return None

        pdf_dir = self.get_pdf_dir(base_dir)
        try:
            pdf_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            _warn_pdf_problem(
                "Failed to create PDF directory for migration: "
                f"{to_display_path(pdf_dir)}: {_reason(e)}",
                logging.ERROR,
            )
            return legacy_pdf

        index = self._next_free_pdf_index(pdf_dir)
        new_path = pdf_dir / self._generate_pdf_filename(index)
        source = to_display_path(legacy_pdf)
        destination = to_display_path(new_path)
        try:
            legacy_pdf.rename(new_path)
        except OSError as e:
            _warn_pdf_problem(
                f"Failed to migrate legacy PDF: {source} -> {destination}: "
                f"{_reason(e)}",
                logging.ERROR,
            )
            return legacy_pdf
        _logger.info("Legacy PDF migrated: %s -> %s", source, destination)
        return None

    def has_pdf(self, base_dir: Union[Path, None] = None) -> bool:
        """Check if this entry has any associated PDF files.

        Parameters
        ----------
        base_dir : Union[Path, None], optional
            Base directory for PDF storage. If None, uses DIRPATH_PDF.

        Returns
        -------
        bool
            True if at least one PDF file exists, False otherwise.
        """
        return len(self.get_pdf_files(base_dir)) > 0

    def get_pdf_count(self, base_dir: Union[Path, None] = None) -> int:
        """Get the number of PDF files associated with this entry.

        Parameters
        ----------
        base_dir : Union[Path, None], optional
            Base directory for PDF storage. If None, uses DIRPATH_PDF.

        Returns
        -------
        int
            The number of PDF files.
        """
        return len(self.get_pdf_files(base_dir))

    @property
    @deprecated(
        "Use `pdf_dir_name` and `get_pdf_files()` for multiple PDF support. "
        "This property is kept for backward compatibility and will be removed "
        "in a future version."
    )
    def pdf_filename(self) -> str:
        """Generate a sanitized filename for the entry's PDF file.

        .. deprecated::
            Use `pdf_dir_name` and `get_pdf_files()` for multiple PDF support.
            This property is kept for backward compatibility and will be removed
            in a future version.

        The filename format is '<year> - <first_author> - <title>.pdf'.
        A missing title falls back to ``Unknown`` (as in `pdf_dir_name`)
        so that entries without a title do not raise. Otherwise the name
        is kept unchanged to find legacy single PDF files.

        Returns
        -------
        str
            A sanitized filename for the entry.
        """
        year = self.get(COLNAME_YEAR, "YYYY")
        first_author = (
            self[COLNAME_AUTHOR].split(AUTHOR_SEPARATOR)[0]
            if COLNAME_AUTHOR in self
            else "Unknown"
        )
        title = self.get(COLNAME_TITLE, "Unknown")
        return sanitize_filename(
            "{} - {} - {}.pdf".format(year, first_author, title)
        )

    def _generate_pdf_filename(self, index: int) -> str:
        """Generate a standardized PDF filename inside the PDF directory.

        The filename is a zero-padded index to keep the file path short and
        avoid path length errors. The folder name already contains the entry
        information, so the filename only needs to be an index.

        Parameters
        ----------
        index : int
            Index of the PDF file for this entry. ``0`` is used for the first
            file, ``1`` for the second, and so on.

        Returns
        -------
        str
            A zero-padded filename such as ``'00.pdf'`` (for index 0) or
            ``'01.pdf'`` (for index 1).
        """
        filename = f"{index:02d}.pdf"
        return filename

    @deprecated(
        "This function is for backward compatibility and will be removed "
        "in a future version. It converts old PDF filenames (e.g., "
        "'<pdf_dir_name>.pdf' or '<pdf_dir_name>_01.pdf') to the new naming "
        "convention (e.g., '00.pdf', '01.pdf')."
    )
    def _migrate_legacy_pdf_filenames(
        self, base_dir: Union[Path, None] = None
    ) -> None:
        """Migrate legacy PDF filenames to the new naming convention.

        .. deprecated::
            This function is for backward compatibility and will be removed
            in a future version. It converts old PDF filenames (e.g.,
            ``'<pdf_dir_name>.pdf'`` or ``'<pdf_dir_name>_01.pdf'``) to the
            new naming convention (e.g., ``'00.pdf'``, ``'01.pdf'``).

        This function checks for PDF files with old naming patterns and
        renames them to the new zero-padded index format. A file is never
        overwritten: the new name must not exist (checked right before the
        rename). Failures are logged and shown with ``st.warning``.

        Parameters
        ----------
        base_dir : Union[Path, None], optional
            Base directory for PDF storage. If None, uses DIRPATH_PDF.
        """
        pdf_dir = self.get_pdf_dir(base_dir)
        if not _is_dir(pdf_dir):
            return

        try:
            all_pdfs = list(pdf_dir.glob("*.pdf"))
        except OSError as e:
            _warn_pdf_problem(
                "Failed to access PDF directory: "
                f"{to_display_path(pdf_dir)}: {_reason(e)}",
                logging.ERROR,
            )
            return

        # 新しい命名規則のファイル名パターン（00.pdf, 01.pdf, ..., 100.pdf）
        # _generate_pdf_filename は 100 番以降を 3 桁以上で作る
        new_pattern = re.compile(r"^\d{2,}\.pdf$")

        # 古い命名規則のファイルを検出
        legacy_files = []
        new_files = []

        for pdf_file in all_pdfs:
            filename = pdf_file.name
            if new_pattern.match(filename):
                # 新しい命名規則のファイル
                new_files.append(filename)
            else:
                # 古い命名規則のファイル
                legacy_files.append(pdf_file)

        if not legacy_files:
            # 古いファイルがない場合は何もしない
            return

        # 既存の新しいファイル名から使用されているインデックスを取得
        used_indices = set()
        for filename in new_files:
            try:
                index = int(filename[: -len(".pdf")])  # "00.pdf" -> 0
                used_indices.add(index)
            except ValueError:
                continue

        # 古いファイルを新しい命名規則に変換
        index = 0
        for legacy_file in legacy_files:
            # 使用されていないインデックスを見つける。POSIX の改名は既存の
            # ファイルを置き換えるため、移す先が存在しないことも確かめる
            while index in used_indices:
                index += 1
            index = self._next_free_pdf_index(pdf_dir, index)
            new_path = pdf_dir / self._generate_pdf_filename(index)
            source = to_display_path(legacy_file)
            destination = to_display_path(new_path)

            try:
                legacy_file.rename(new_path)
            except OSError as e:
                _warn_pdf_problem(
                    "Failed to migrate legacy PDF filename: "
                    f"{source} -> {destination}: {_reason(e)}",
                    logging.ERROR,
                )
                # エラーが発生しても次のファイルの処理を続行
                continue
            _logger.info(
                "Legacy PDF filename migrated: %s -> %s", source, destination
            )
            used_indices.add(index)
            index += 1


class PaperList(MutableMapping):
    """
    A mutable mapping that manages a collection of `Entry` objects representing academic papers.

    This class provides methods for loading and saving the paper list from a file or session state.
    """

    def __init__(self, __mapping: Mapping[str, Entry]) -> None:
        """
        Initializes the PaperList with a given mapping of paper entries.

        Parameters
        ----------
        __mapping : Mapping[str, Entry]
            A dictionary where keys are paper identifiers and values are `Entry` objects.
        """
        super().__init__()

        os.makedirs(DIRPATH_PDF, exist_ok=True)

        self.__mapping = dict(__mapping)

    @classmethod
    def from_file(
        cls,
        _filepath_paper_list_json: Union[os.PathLike, str, None] = None,
    ) -> "PaperList":
        """
        Loads a paper list from a JSON file.

        ``load_paper_list`` の互換のための薄いラッパー。ファイルが壊れて
        いる (``PaperListStatus.BROKEN``) ときは、例外を送出せずに、画面に
        警告 ``Broken paper list (json)`` を、ログに理由を出し、空の論文
        リストを返す。ファイルがないときは、警告を出さずに空の論文リストを
        返す。ファイルは変更しない (壊れたファイルの退避もしない)。

        Parameters
        ----------
        _filepath_paper_list_json : Union[os.PathLike, str, None], optional
            The file path to load the paper list from. If None, a default path is used.

        Returns
        -------
        PaperList
            An instance of `PaperList` initialized with the loaded data.

        Raises
        ------
        OSError
            If the file exists but cannot be read (e.g. no permission to
            read it). See ``load_paper_list``.
        """
        filepath = cls._get_filepath_paper_list(_filepath_paper_list_json)
        paper_list, status, reason = load_paper_list(filepath)
        if status is PaperListStatus.BROKEN:
            _logger.warning(
                "%s: %s: %s", _BROKEN_PAPER_LIST_WARNING, filepath, reason
            )
            st.warning(_BROKEN_PAPER_LIST_WARNING)
        return paper_list

    @classmethod
    def from_session_state(cls) -> "PaperList":
        """
        Loads the paper list from Streamlit's session state.

        Returns
        -------
        PaperList
            An instance of `PaperList` initialized with the session state data.
        """
        return cls(st.session_state.get(KEY_PAPER_LIST, dict()))

    def to_file(
        self, _filepath_paper_list_json: Union[os.PathLike, str, None] = None
    ) -> None:
        """
        Saves the current paper list to a JSON file.

        UTF-8 で、BOM を付けずに書く (BOM 付きのファイルも読み込めるが、
        書き戻すと BOM はなくなる)。

        Parameters
        ----------
        _filepath_paper_list_json : Union[os.PathLike, str, None], optional
            The file path to save the paper list. If None, a default path is used.
        """
        with open(
            self._get_filepath_paper_list(_filepath_paper_list_json),
            mode="w",
            encoding=ENCODING,
        ) as f:
            json.dump(
                {
                    _key: dict(_value)
                    for _key, _value in self.__mapping.items()
                },
                f,
                ensure_ascii=False,
                indent=4,
            )

    def to_session_state(self) -> None:
        """
        Saves the current paper list to Streamlit's session state.
        """
        st.session_state[KEY_PAPER_LIST] = self.__mapping

    @staticmethod
    def _get_filepath_paper_list(
        _filepath_paper_list_json: Union[os.PathLike, str, None] = None,
    ) -> Path:
        """
        Determines the file path for the paper list JSON file.

        Parameters
        ----------
        _filepath_paper_list_json : Union[os.PathLike, str, None], optional
            A custom file path. If None, the default path is used.

        Returns
        -------
        Path
            The resolved file path.
        """
        if _filepath_paper_list_json is None:
            _filepath_paper_list_json = FILEPATH_LIST
        else:
            _filepath_paper_list_json = Path(_filepath_paper_list_json)
        return _filepath_paper_list_json

    def __getitem__(self, key: str) -> Entry:
        """
        Retrieves an `Entry` from the paper list.

        Parameters
        ----------
        key : str
            The key of the paper to retrieve.

        Returns
        -------
        Entry
            The corresponding `Entry` object.
        """
        return self.__mapping[key]

    def __setitem__(self, key: str, value: Entry) -> None:
        """
        Adds or updates an `Entry` in the paper list.

        Parameters
        ----------
        key : str
            The key for the paper.
        value : Entry
            The `Entry` object to be stored.
        """
        self.__mapping[key] = value

    def __delitem__(self, key: str) -> None:
        """
        Deletes an `Entry` from the paper list.

        Parameters
        ----------
        key : str
            The key of the paper to remove.
        """
        del self.__mapping[key]

    def __str__(self) -> str:
        """
        Returns a string representation of the paper list.

        Returns
        -------
        str
            A string representation of the internal dictionary.
        """
        return str(self.__mapping)

    def __repr__(self) -> str:
        """
        Returns a detailed string representation of the object.

        Returns
        -------
        str
            A formatted string showing the class name and its content.
        """
        return "{}({})".format(self.__class__.__name__, self.__mapping)

    def __iter__(self):
        """
        Returns an iterator over the paper list keys.

        Returns
        -------
        Iterator[str]
            An iterator over the dictionary keys.
        """
        return iter(self.__mapping)

    def __len__(self) -> int:
        """
        Returns the number of papers in the list.

        Returns
        -------
        int
            The number of stored entries.
        """
        return len(self.__mapping)

    def items(self) -> ItemsView[str, Entry]:
        """
        Returns a view of the paper list's items.

        Returns
        -------
        ItemsView[str, Entry]
            A view of key-value pairs.
        """
        return super().items()

    def values(self) -> ValuesView[Entry]:
        """
        Returns a view of the paper list's values.

        Returns
        -------
        ValuesView[Entry]
            A view of `Entry` values.
        """
        return super().values()

    def keys(self) -> KeysView[str]:
        """
        Returns a view of the paper list's keys.

        Returns
        -------
        KeysView[str]
            A view of the keys in the dictionary.
        """
        return super().keys()


class PaperListStatus(str, Enum):
    """State of the paper list file, judged from its contents.

    Attributes
    ----------
    OK
        The file is a JSON object of entries (key -> JSON object of fields)
        whose field values are all strings.
    MISSING
        There is no file at the path. A directory at the path also counts
        as no file (as before this state was introduced).
    BROKEN
        The file cannot be decoded as UTF-8, cannot be parsed as JSON, or
        is not a JSON object of entries whose field values are strings
        (the top level is not an object, including ``null``; an entry is
        not an object; or a field value is not a string).
    """

    OK = "ok"
    MISSING = "missing"
    BROKEN = "broken"


class PaperListLoad(NamedTuple):
    """Result of ``load_paper_list``.

    Attributes
    ----------
    paper_list : PaperList
        The loaded paper list. Empty when ``status`` is ``MISSING`` or
        ``BROKEN`` (no entry of a broken file is loaded).
    status : PaperListStatus
        The state of the file.
    reason : Optional[str]
        Why the file is broken. ``None`` unless ``status`` is ``BROKEN``.
    """

    paper_list: PaperList
    status: PaperListStatus
    reason: Optional[str]


# 論文リストのファイルの中身 (キー -> フィールド名 -> 値の文字列)
_RawPaperList = dict[str, dict[str, str]]


def _json_type_name(value: object) -> str:
    """Return the JSON type name of a value decoded by ``json``.

    Parameters
    ----------
    value : object
        A value returned by ``json.loads``.

    Returns
    -------
    str
        ``object``, ``array``, ``string``, ``number``, ``boolean`` or
        ``null``.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _find_shape_problem(raw: object) -> Optional[str]:
    """Return why ``raw`` is not a paper list, or ``None`` if it is one.

    論文リストの形は、キーから論文 (フィールド名から値への辞書) への辞書で、
    フィールドの値はすべて文字列。修正前の保存処理 (``PaperList.to_file``)
    はこの形だけを書き、修正前の読み込みはこの形以外で例外を送出していた
    (``null`` だけは空の論文リストとして読んでいた)。

    Parameters
    ----------
    raw : object
        The value decoded from the file by ``json.loads``.

    Returns
    -------
    Optional[str]
        The first problem found, or ``None``.
    """
    if not isinstance(raw, dict):
        return (
            f"the top level is a JSON {_json_type_name(raw)}, "
            "not an object of entries"
        )
    for key, fields in raw.items():
        if not isinstance(fields, dict):
            return (
                f"entry {key!r} is a JSON {_json_type_name(fields)}, "
                "not an object of fields"
            )
        for name, value in fields.items():
            if not isinstance(value, str):
                return (
                    f"field {name!r} of entry {key!r} is a JSON "
                    f"{_json_type_name(value)}, not a string"
                )
    return None


def _decode_paper_list(
    content: bytes,
) -> Union[tuple[_RawPaperList, None], tuple[None, str]]:
    """Decode the bytes of a paper list file.

    先頭の UTF-8 の BOM は無視する (``utf-8-sig``)。BOM の後が論文リストの
    形をしていれば正常、そうでなければ壊れている。BOM が先頭以外にある、
    BOM が 2 つ続く、BOM だけである、UTF-16 などの別の BOM である、のいずれ
    も壊れている。

    Parameters
    ----------
    content : bytes
        The contents of the file.

    Returns
    -------
    Union[tuple[_RawPaperList, None], tuple[None, str]]
        ``(raw, None)`` if ``content`` is a paper list, or
        ``(None, reason)`` if it is broken.
    """
    try:
        text = content.decode(_PAPER_LIST_DECODING)
    except UnicodeDecodeError as e:
        return None, f"not valid {ENCODING}: {e}"
    try:
        raw = json.loads(text)
    except (ValueError, RecursionError) as e:
        # JSONDecodeError (ValueError の派生) のほか、桁数の上限を超える
        # 整数 (Python 3.11 以降の ValueError) と深すぎる入れ子
        # (RecursionError) も、JSON として読めないものとして扱う
        return None, f"not valid JSON: {e}"
    problem = _find_shape_problem(raw)
    if problem is not None:
        return None, problem
    return cast(_RawPaperList, raw), None


def _read_paper_list(
    filepath: Path,
) -> tuple[PaperListStatus, Optional[_RawPaperList], Optional[str]]:
    """Read the paper list file and judge its state, without Streamlit.

    Parameters
    ----------
    filepath : Path
        The paper list file.

    Returns
    -------
    tuple[PaperListStatus, Optional[_RawPaperList], Optional[str]]
        The state, the contents (only when ``OK``) and the reason (only
        when ``BROKEN``).

    Raises
    ------
    OSError
        If the file exists but cannot be read (e.g. no permission). This is
        not ``BROKEN``: the contents are unknown, so they must not be
        treated as replaceable.
    """
    if not filepath.is_file():
        return PaperListStatus.MISSING, None, None
    try:
        content = filepath.read_bytes()
    except FileNotFoundError:
        # 有無を確かめてから読むまでの間に削除された
        return PaperListStatus.MISSING, None, None
    raw, reason = _decode_paper_list(content)
    if raw is None:
        return PaperListStatus.BROKEN, None, reason
    return PaperListStatus.OK, raw, None


def load_paper_list(
    filepath: Union[os.PathLike, str, None] = None,
) -> PaperListLoad:
    """Load the paper list file and report its state.

    Streamlit を呼ばない (画面に何も出さない)。壊れているときの通知は
    呼び出し元が行う (``PaperList.from_file`` は従来の警告を出す)。
    ファイルは変更しない。先頭に BOM の付いた UTF-8 のファイルは、BOM の後が
    正しい形なら ``OK`` とする。

    Parameters
    ----------
    filepath : Union[os.PathLike, str, None], optional
        The paper list file. If None, ``FILEPATH_LIST`` is used.

    Returns
    -------
    PaperListLoad
        The paper list (empty unless ``OK``), the state (see
        ``PaperListStatus``) and the reason why the file is broken.

    Raises
    ------
    OSError
        If the file exists but cannot be read (e.g. no permission to read
        it). Broken contents never raise.
    """
    status, raw, reason = _read_paper_list(
        PaperList._get_filepath_paper_list(filepath)
    )
    entries = {} if raw is None else raw
    return PaperListLoad(
        paper_list=PaperList(
            {key: Entry(fields) for key, fields in entries.items()}
        ),
        status=status,
        reason=reason,
    )


def _reserve_backup_path(directory: Path, base_name: str) -> Path:
    """Create an empty file at the first free backup name and return it.

    名前は ``base_name``、``base_name-1``、``base_name-2`` … の順に試す。
    排他的な作成 (``O_EXCL``) で名前を確保するので、既にあるファイルを
    上書きせず、その名前のリンク (指す先がないものを含む) もたどらない。

    Parameters
    ----------
    directory : Path
        The directory to create the file in.
    base_name : str
        The name to try first.

    Returns
    -------
    Path
        The created (empty) file.

    Raises
    ------
    OSError
        If the file cannot be created for a reason other than the name
        being taken.
    """
    number = 0
    while True:
        name = base_name if number == 0 else f"{base_name}-{number}"
        candidate = directory / name
        try:
            fd = os.open(
                candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666
            )
        except FileExistsError:
            number += 1
            continue
        os.close(fd)
        return candidate


def _discard_reserved_backup(path: Path) -> None:
    """Remove a backup file reserved by ``_reserve_backup_path``.

    複製に失敗した退避先 (空、または途中まで書いたファイル) を残さない。
    削除にも失敗したら、ログに出す (元の失敗を優先して呼び出し元へ送る)。

    Parameters
    ----------
    path : Path
        The reserved backup file.
    """
    try:
        path.unlink()
    except OSError as e:
        _logger.error(
            "Failed to remove the incomplete paper list backup: %s: %s",
            path,
            _reason(e),
        )


def backup_broken_paper_list(
    filepath: Union[os.PathLike, str, None] = None,
    now: Optional[datetime] = None,
) -> Optional[Path]:
    """Copy the paper list file aside when it is broken.

    状態が ``BROKEN`` のときだけ、同じディレクトリの
    ``<ファイル名>.broken-<YYYYmmdd_HHMMSS>`` (既にあれば ``-1``、``-2`` と
    連番) へ ``shutil.copy2`` で複製する。元のファイルは変更しない。復元が
    使う ``list.json.backup`` は使わない。Streamlit を呼ばない (退避先の
    表示は呼び出し元が行う)。

    Parameters
    ----------
    filepath : Union[os.PathLike, str, None], optional
        The paper list file. If None, ``FILEPATH_LIST`` is used.
    now : Optional[datetime], optional
        The time put in the name. If None, the current local time is used.

    Returns
    -------
    Optional[Path]
        The backup file, or ``None`` if the file is not broken (``OK`` or
        ``MISSING``; nothing is created).

    Raises
    ------
    OSError
        If the file cannot be read, or the backup cannot be made. No backup
        file is left behind then, and the caller must not overwrite the
        broken file.
    """
    path = PaperList._get_filepath_paper_list(filepath)
    status, _, reason = _read_paper_list(path)
    if status is not PaperListStatus.BROKEN:
        return None

    stamp = (datetime.now() if now is None else now).strftime(
        _BROKEN_BACKUP_TIME_FORMAT
    )
    backup = _reserve_backup_path(path.parent, f"{path.name}.broken-{stamp}")
    try:
        shutil.copy2(path, backup)
    except OSError:
        _discard_reserved_backup(backup)
        raise
    _logger.warning(
        "Backed up the broken paper list (%s): %s -> %s", reason, path, backup
    )
    return backup


if __name__ == "__main__":
    for key, value in PaperList.from_file().items():
        print(type(value))
