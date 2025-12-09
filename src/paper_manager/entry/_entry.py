import json
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
from typing import Literal, Union

import streamlit as st

from paper_manager._constants import (
    AUTHOR_SEPARATOR,
    DIRPATH_PDF,
    ENCODING,
    FILEPATH_LIST,
)
from paper_manager.helper import deprecated, split
from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)

KEY_PAPER_LIST = "paper_list"

# パス長制限（WindowsのMAX_PATH 260文字を考慮して、安全な上限を設定）
# base_dir + pdf_dir_name + ファイル名（例：00.pdf）の合計がこの値を超えないようにする
MAX_PATH_LENGTH = 250  # 安全マージンを考慮
MAX_DIRNAME_LENGTH = 200  # フォルダ名の最大長


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
        # Windowsでは \\ / : + > " < > | が不適切
        invalid_chars = r"[\\/:*?<>|]"
    else:
        # LinuxとOSXでは / が不適切
        invalid_chars = r"[/]"

    # 不適切な文字をアンダースコアに置換
    return re.sub(invalid_chars, "_", filename)


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
            _key.upper()
            if _key.upper() in {"ID", "ENTRYTYPE"}
            else _key.lower(): _value.strip()
            for _key, _value in __mapping.items()
        }

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
            self["author"].split(AUTHOR_SEPARATOR)[0]
            if "author" in self
            else "Unknown"
        )
        while (
            key := "{0}{1}_{2}".format(
                first_author.replace(" ", ""), self.get("year", "YYYY"), i
            )
        ) in st_keys:
            i += 1
        return key

    @property
    def pdf_dir_name(self) -> str:
        """Generate a sanitized directory name for the entry's PDF files.

        The directory name format is '<year> - <first_author> - <title>'.
        If the resulting name is too long, the title will be truncated to
        fit within the maximum directory name length.

        Returns
        -------
        str
            A sanitized directory name for the entry.
        """
        year = self.get("year", "YYYY")
        first_author = split(self.get("author", ""), AUTHOR_SEPARATOR)[0]
        if first_author == "":
            first_author = "Unknown"
        title = self.get("title", "Unknown")

        # 基本フォーマット
        base_format = "{} - {} - {}"
        dir_name = sanitize_filename(
            base_format.format(year, first_author, title)
        )

        # フォルダ名が長すぎる場合はタイトルを切り詰める
        if len(dir_name) > MAX_DIRNAME_LENGTH:
            # 年と著者名の長さを計算
            prefix = f"{year} - {first_author} - "
            max_title_length = MAX_DIRNAME_LENGTH - len(prefix)

            if max_title_length > 0:
                # タイトルを切り詰める（末尾に...を追加）
                truncated_title = title[: max_title_length - 3] + "..."
                dir_name = sanitize_filename(
                    base_format.format(year, first_author, truncated_title)
                )
            else:
                # それでも長すぎる場合は、年と著者名のみ
                dir_name = sanitize_filename(f"{year} - {first_author}")
                if len(dir_name) > MAX_DIRNAME_LENGTH:
                    # 最後の手段：年のみ
                    dir_name = sanitize_filename(year)

        return dir_name

    def get_pdf_dir(self, base_dir: Union[Path, None] = None) -> Path:
        """Get the directory path for storing PDF files.

        このエントリ専用のPDFディレクトリのパスを返します。
        複数のPDFファイルは、このディレクトリ内に`00.pdf`, `01.pdf`のように
        連番で保存されます。

        Parameters
        ----------
        base_dir : Union[Path, None], optional
            Base directory for PDF storage. If None, uses DIRPATH_PDF.

        Returns
        -------
        Path
            The directory path for this entry's PDF files.
            The directory name is generated by `pdf_dir_name` property.
        """
        if base_dir is None:
            base_dir = DIRPATH_PDF

        pdf_dir = base_dir / self.pdf_dir_name

        # パス長をチェック（警告のみ、エラーは発生させない）
        full_path_str = str(pdf_dir)
        if len(full_path_str) > MAX_PATH_LENGTH:
            _logger.warning(
                f"PDF directory path is very long ({len(full_path_str)} chars): {pdf_dir}"
            )

        return pdf_dir

    def get_pdf_files(self, base_dir: Union[Path, None] = None) -> list[Path]:
        """Get a list of PDF files associated with this entry.

        このエントリに関連するすべてのPDFファイルのリストを返します。
        ファイルは`00.pdf`, `01.pdf`, `02.pdf`のように連番で命名され、
        ソートされた順序で返されます。

        旧バージョンとの互換性:
            旧形式のPDFファイル（単一ファイル形式）が存在する場合、
            自動的に新しい形式（ディレクトリ内の連番ファイル）に移行されます。

        Parameters
        ----------
        base_dir : Union[Path, None], optional
            Base directory for PDF storage. If None, uses DIRPATH_PDF.

        Returns
        -------
        list[Path]
            A sorted list of PDF file paths. Empty list if no PDFs exist.
        """
        pdf_dir = self.get_pdf_dir(base_dir)
        if not pdf_dir.is_dir():
            # 後方互換性: 旧形式の単一PDFファイルをチェックし、
            # 見つかった場合は現在のフォルダ方式に従ってリネームして移動する
            legacy_base_dir = base_dir or DIRPATH_PDF
            legacy_pdf = legacy_base_dir / self.pdf_filename
            if legacy_pdf.is_file():
                try:
                    pdf_dir.mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    _logger.error(
                        f"Failed to create PDF directory for migration: {pdf_dir}: {e}"
                    )
                    # ディレクトリ作成に失敗した場合は、旧形式のファイルをそのまま返す
                    return [legacy_pdf]

                try:
                    existing_files = {f.name for f in pdf_dir.glob("*.pdf")}
                except OSError as e:
                    _logger.error(
                        f"Failed to access PDF directory: {pdf_dir}: {e}"
                    )
                    return [legacy_pdf]

                index = 0
                # 既存ファイルと重複しない新しいファイル名を決定
                while True:
                    new_name = self._generate_pdf_filename(index)
                    if new_name not in existing_files:
                        break
                    index += 1

                new_path = pdf_dir / new_name
                try:
                    legacy_pdf.rename(new_path)
                    _logger.debug(
                        "Legacy PDF migrated: %s -> %s", legacy_pdf, new_path
                    )
                    return [new_path]
                except OSError as e:
                    _logger.error(
                        f"Failed to migrate legacy PDF: {legacy_pdf} -> {new_path}: {e}"
                    )
                    # リネームに失敗した場合は、旧形式のファイルをそのまま返す
                    return [legacy_pdf]

            # 旧形式のファイルも存在しない場合は空リスト
            return []

        try:
            # 後方互換性: 古い命名規則のファイルを新しい命名規則に変換
            self._migrate_legacy_pdf_filenames(base_dir)
            return sorted(pdf_dir.glob("*.pdf"))
        except OSError as e:
            _logger.error(f"Failed to access PDF directory: {pdf_dir}: {e}")
            return []

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

        Returns
        -------
        str
            A sanitized filename for the entry.
        """
        year = self.get("year", "YYYY")
        first_author = (
            self["author"].split(AUTHOR_SEPARATOR)[0]
            if "author" in self
            else "Unknown"
        )
        title = self["title"]
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
        renames them to the new zero-padded index format.

        Parameters
        ----------
        base_dir : Union[Path, None], optional
            Base directory for PDF storage. If None, uses DIRPATH_PDF.
        """
        pdf_dir = self.get_pdf_dir(base_dir)
        if not pdf_dir.is_dir():
            return

        try:
            all_pdfs = list(pdf_dir.glob("*.pdf"))
        except OSError as e:
            _logger.error(f"Failed to access PDF directory: {pdf_dir}: {e}")
            return

        # 新しい命名規則のファイル名パターン（00.pdf, 01.pdfなど）
        new_pattern = re.compile(r"^\d{2}\.pdf$")

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
                index = int(filename[:2])  # "00.pdf" -> 0
                used_indices.add(index)
            except ValueError:
                continue

        # 古いファイルを新しい命名規則に変換
        index = 0
        for legacy_file in legacy_files:
            # 使用されていないインデックスを見つける
            while index in used_indices:
                index += 1

            new_name = self._generate_pdf_filename(index)
            new_path = pdf_dir / new_name

            try:
                legacy_file.rename(new_path)
                _logger.debug(
                    f"Legacy PDF filename migrated: {legacy_file.name} -> {new_name}"
                )
                used_indices.add(index)
                index += 1
            except OSError as e:
                _logger.error(
                    f"Failed to migrate legacy PDF filename: {legacy_file.name} -> {new_name}: {e}"
                )
                # エラーが発生しても次のファイルの処理を続行


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
