import json
from collections.abc import Callable
from typing import Literal

import streamlit as st

from paper_manager._constants import DIRPATH_APP, FILEPATH_FIELDS
from paper_manager.entry import PaperList
from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)


class config_page:
    def __init__(self, _callable: Callable) -> None:
        self._callable = _callable

    def __call__(self, *args, **kwargs):
        _logger.debug("config_page Start")
        st.set_page_config(page_title="PAPER MANAGER")

        with st.sidebar:
            st.page_link(
                DIRPATH_APP / "_list.py",
                label="リスト・編集",
                icon=":material/menu:",
            )
            st.page_link(
                DIRPATH_APP / "pages/_register.py",
                label="登録",
                icon=":material/add:",
            )

        st.title("PAPER MANAGER")

        if not PaperList.from_session_state():
            PaperList.from_file().to_session_state()

        _return = self._callable(*args, **kwargs)

        PaperList.from_session_state().to_file()
        # st.button("Sync")
        _logger.debug("config_page End")
        return _return


def load_fields() -> dict[str, dict[str, dict[Literal["required"], bool]]]:
    if not FILEPATH_FIELDS.is_file():
        raise FileNotFoundError(f"{FILEPATH_FIELDS}")
    with open(FILEPATH_FIELDS, mode="r", encoding="utf-8") as f:
        dict_fields = json.load(f)
    return dict_fields
