import json
from collections.abc import Callable
from pathlib import Path

import streamlit as st

from paper_manager.entry.typing import ENTRY

DIRPATH_ROOT = Path(__file__).parent
DIRPATH_DATA = DIRPATH_ROOT / "data"
FILEPATH_LIST = DIRPATH_DATA / "list.json"


class config_page:
    def __init__(self, _callable: Callable) -> None:
        self._callable = _callable

    def __call__(self, *args, **kwargs):
        st.title("PAPER MANAGER")

        st.session_state["paper_list"] = load_paper_list()

        _return = self._callable(*args, **kwargs)

        with open(FILEPATH_LIST, mode="w", encoding="utf-8") as f:
            json.dump(
                st.session_state["paper_list"], f, indent=4, ensure_ascii=False
            )
        return _return


def load_paper_list() -> dict[str, ENTRY]:
    if FILEPATH_LIST.is_file():
        try:
            with open(FILEPATH_LIST, mode="r", encoding="utf-8") as f:
                dict_paper_list: dict[str, ENTRY] = json.load(f)  # type: ignore[annotation-unchecked]
        except json.JSONDecodeError:
            dict_paper_list = dict()
    else:
        dict_paper_list = dict()
    return dict_paper_list
