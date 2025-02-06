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
    def __init__(
        self, __mapping: Mapping[Union[str, Literal["ENTRYTYPE"]], str]
    ):
        super().__init__()
        self.__mapping = {
            _key: _value.strip() for _key, _value in __mapping.items()
        }

    def __getitem__(self, key: str) -> str:
        return self.__mapping[key]

    def __setitem__(self, key: str, value: str) -> None:
        self.__mapping[key] = value

    def __delitem__(self, key: str) -> None:
        del self.__mapping[key]

    def __str__(self) -> str:
        return str(self.__mapping)

    def __repr__(self) -> str:
        return "{}({})".format(self.__class__.__name__, self.__mapping)

    def __iter__(self):
        return iter(self.__mapping)

    def __len__(self):
        return len(self.__mapping)

    def items(self) -> ItemsView[str, str]:
        return super().items()

    def values(self) -> ValuesView[str]:
        return super().values()

    def keys(self) -> KeysView[str]:
        return super().keys()

    def get_key(self, keys: Collection[str]) -> str:
        """get paper's key by using 'author' and 'year'

        Parameters
        ----------
        keys : Collection[str]
            keys

        Returns
        -------
        str
            key
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
        else:
            return key

    @property
    def pdf_filename(self) -> str:
        year = self.get("year", "YYYY")
        first_author = (
            self["author"].split(" and ")[0] if "author" in self else "Unknown"
        )
        title = self["title"]
        return sanitize_filename(
            "{} - {} - {}.pdf".format(year, first_author, title)
        )


class PaperList(MutableMapping):
    def __init__(self, __mapping: Mapping[str, Entry]) -> None:
        super().__init__()

        os.makedirs(DIRPATH_PDF, exist_ok=True)

        self.__mapping = dict(__mapping)

    @classmethod
    def from_file(
        cls,
        _filepath_paper_list_json: Union[os.PathLike, str, None] = None,
    ) -> "PaperList":
        with open(
            cls._get_filepath_paper_list(_filepath_paper_list_json),
            mode="r",
            encoding=ENCODING,
        ) as f:
            try:
                _paper_list_raw: Union[dict, None] = json.load(f)
            except json.JSONDecodeError:
                st.warning("Broken paper list (json)")
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
        return cls(st.session_state.get(KEY_PAPER_LIST, dict()))

    def to_file(
        self, _filepath_paper_list_json: Union[os.PathLike, str, None] = None
    ) -> None:
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
        st.session_state[KEY_PAPER_LIST] = self.__mapping

    @staticmethod
    def _get_filepath_paper_list(
        _filepath_paper_list_json: Union[os.PathLike, str, None] = None,
    ) -> Path:
        if _filepath_paper_list_json is None:
            _filepath_paper_list_json = FILEPATH_LIST
        else:
            _filepath_paper_list_json = Path(_filepath_paper_list_json)
        return _filepath_paper_list_json

    def __getitem__(self, key: str) -> Entry:
        return self.__mapping[key]

    def __setitem__(self, key: str, value: Entry) -> None:
        self.__mapping[key] = value

    def __delitem__(self, key: str) -> None:
        del self.__mapping[key]

    def __str__(self) -> str:
        return str(self.__mapping)

    def __repr__(self) -> str:
        return "{}({})".format(self.__class__.__name__, self.__mapping)

    def __iter__(self):
        return iter(self.__mapping)

    def __len__(self):
        return len(self.__mapping)

    def items(self) -> ItemsView[str, Entry]:
        return super().items()

    def values(self) -> ValuesView[Entry]:
        return super().values()

    def keys(self) -> KeysView[str]:
        return super().keys()


if __name__ == "__main__":
    for key, value in PaperList.from_file().items():
        print(type(value))
