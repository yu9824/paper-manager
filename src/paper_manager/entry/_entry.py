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

from paper_manager._constants import DIRPATH_PDF, ENCODING, FILEPATH_LIST
from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)

KEY_PAPER_LIST = "paper_list"


def sanitize_filename(filename: str) -> str:
    """Sanitize a filename by replacing invalid characters with underscores.

    This function ensures that filenames do not contain characters that are
    invalid or problematic for different operating systems. On Windows, it
    removes characters such as `\ / : * ? " < > |`, while on Linux and macOS,
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

    >>> sanitize_filename("C:\\Windows\\System32")
    'C__Windows_System32'

    Notes
    -----
    - This function does not guarantee that the resulting filename is unique
      or valid in all scenarios.
    - It does not check for reserved filenames (e.g., `CON`, `PRN` on Windows).

    """
    # OSごとに不適切な文字を定義
    if platform.system() == "Windows":
        # Windowsでは \ / : + > " < > | が不適切
        invalid_chars = r"[\\/:*?<>|]"
    else:
        # LinuxとOSXでは / が不適切
        invalid_chars = r"[/]"

    # 不適切な文字をアンダースコアに置換
    return re.sub(invalid_chars, "_", filename)


class Entry(MutableMapping):
    """A dictionary-like class for handling bibliographic entries.

    This class stores and manages bibliographic entry data, ensuring that values
    are stripped of leading and trailing whitespace. It also provides methods
    for generating unique keys and sanitized filenames.
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
            _key: _value.strip() for _key, _value in __mapping.items()
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
        return str(self.__mapping)

    def __repr__(self) -> str:
        """Return a string representation suitable for debugging."""
        return "{}({})".format(self.__class__.__name__, self.__mapping)

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
            self["author"].split(" and ")[0] if "author" in self else "Unknown"
        )
        while (
            key := "{0}{1}_{2}".format(
                first_author.replace(" ", ""), self.get("year", "YYYY"), i
            )
        ) in st_keys:
            i += 1
        return key

    @property
    def pdf_filename(self) -> str:
        """Generate a sanitized filename for the entry's PDF file.

        The filename format is '<year> - <first_author> - <title>.pdf'.

        Returns
        -------
        str
            A sanitized filename for the entry.
        """
        year = self.get("year", "YYYY")
        first_author = (
            self["author"].split(" and ")[0] if "author" in self else "Unknown"
        )
        title = self["title"]
        return sanitize_filename(
            "{} - {} - {}.pdf".format(year, first_author, title)
        )


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
