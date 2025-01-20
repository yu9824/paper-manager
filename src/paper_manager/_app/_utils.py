from collections.abc import Callable

import streamlit as st


class config_page:
    def __init__(self, _callable: Callable) -> None:
        self._callable = _callable

    def __call__(self, *args, **kwargs):
        st.title("PAPER MANAGER")
        return self._callable(*args, **kwargs)
