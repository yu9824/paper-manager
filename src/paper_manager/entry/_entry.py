import errno
import json
import logging
import os
import platform
import re
from collections.abc import (
    Collection,
    ItemsView,
    KeysView,
    Mapping,
    MutableMapping,
    ValuesView,
)
from pathlib import Path
from typing import Literal, Optional, Union

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
            A unique key based on the first author's name and the year.
        """
        i = 0
        st_keys = set(keys)

        first_author = (
            self[COLNAME_AUTHOR].split(AUTHOR_SEPARATOR)[0]
            if COLNAME_AUTHOR in self
            else "Unknown"
        )
        while (
            key := "{0}{1}_{2}".format(
                first_author.replace(" ", ""),
                self.get(COLNAME_YEAR, "YYYY"),
                i,
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

        Parameters
        ----------
        _filepath_paper_list_json : Union[os.PathLike, str, None], optional
            The file path to load the paper list from. If None, a default path is used.

        Returns
        -------
        PaperList
            An instance of `PaperList` initialized with the loaded data.
        """
        _filepath_paper_list_json = cls._get_filepath_paper_list(
            _filepath_paper_list_json
        )
        if _filepath_paper_list_json.is_file():
            with open(
                _filepath_paper_list_json,
                mode="r",
                encoding=ENCODING,
            ) as f:
                try:
                    _paper_list_raw: Union[dict, None] = json.load(f)
                except json.JSONDecodeError:
                    st.warning("Broken paper list (json)")
                    _paper_list_raw = None
        else:
            _paper_list_raw = None

        return (
            cls(dict())
            if _paper_list_raw is None
            else cls(
                {
                    _key: Entry(_value)
                    for _key, _value in _paper_list_raw.items()
                }
            )
        )

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


if __name__ == "__main__":
    for key, value in PaperList.from_file().items():
        print(type(value))
