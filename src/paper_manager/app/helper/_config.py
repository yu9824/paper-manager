import json
from collections.abc import Callable
from types import MappingProxyType
from typing import Literal

import streamlit as st

from paper_manager._constants import DIRPATH_APP, ENCODING, FILEPATH_FIELDS
from paper_manager.entry import KEY_SHOWN_PDF_WARNINGS, PaperList
from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)

# 保留中の通知 (list[str]) を入れる session state のキー。画面の再描画で
# 消えるメッセージ (論文リストの退避先など) を、次の描画まで残すために使う。
# 書くのは app/components (``save_paper_list``)、表示して消すのは
# ``config_page``。依存の向きを守るため、ここ (helper) に定義する
KEY_NOTICES = "notices"

_MAP_ENTRYTYPE4DOI = MappingProxyType(
    {
        "journal-article": "article",
        "proceedings-article": "inproceedings",
        "book": "book",
    }
)


class config_page:
    """
    A decorator-like class for configuring a Streamlit page.

    This class sets up the page configuration, initializes a sidebar with
    navigation links, shows the pending notices, and loads the `PaperList`
    from the file into the session state before the page runs. It never
    writes the paper list file: every change to the paper list is persisted
    by `save_paper_list`.

    Parameters
    ----------
    _callable : Callable
        A function that represents the main content of the page.
    """

    def __init__(self, _callable: Callable) -> None:
        """
        Initializes the `config_page` instance with a given callable function.

        Parameters
        ----------
        _callable : Callable
            A function to be executed after setting up the page configuration.
        """
        self._callable = _callable

    def __call__(self, *args, **kwargs):
        """
        Configures the Streamlit page and executes the stored function.

        This method:
        - Sets the page title to "PAPER MANAGER".
        - Forgets the PDF warnings shown so far (`KEY_SHOWN_PDF_WARNINGS`),
          so that a warning which is still unresolved is shown once again
          in this render. Only a render of the whole page gets here; a rerun
          of a fragment does not.
        - Adds navigation links to the sidebar.
        - Shows the pending notices (`KEY_NOTICES`), then clears them.
        - Loads `PaperList` from the file into the session state if the
          session state has no papers yet.
        - Calls the stored function (`_callable`).

        The paper list file is not written back after the page runs, so
        rendering a page alone never rewrites (or creates) the file.
        Registering, updating and deleting papers persist the paper list
        through `save_paper_list`.

        Parameters
        ----------
        *args : tuple
            Positional arguments passed to the stored function.
        **kwargs : dict
            Keyword arguments passed to the stored function.

        Returns
        -------
        Any
            The return value of the stored function.
        """
        _logger.debug("config_page Start")
        st.set_page_config(page_title="PAPER MANAGER")

        # 警告の抑止は 1 回の描画の中だけ。再描画の後も解消していない
        # PDF の警告は、もう一度出す
        st.session_state.pop(KEY_SHOWN_PDF_WARNINGS, None)

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
            st.page_link(
                DIRPATH_APP / "pages/_backup.py",
                label="バックアップ",
                icon=":material/download:",
            )
            st.page_link(
                DIRPATH_APP / "pages/_restore.py",
                label="復元",
                icon=":material/restore:",
            )
            st.page_link(
                DIRPATH_APP / "pages/_settings.py",
                label="設定",
                icon=":material/settings:",
            )

        st.title("PAPER MANAGER")

        _show_pending_notices()

        if not PaperList.from_session_state():
            PaperList.from_file().to_session_state()

        _return = self._callable(*args, **kwargs)

        _logger.debug("config_page End")
        return _return


def _show_pending_notices() -> None:
    """Show the notices left by the previous render, then clear them.

    The messages shown at the moment of an action (for example the backup
    path of a broken paper list, shown by `save_paper_list`) disappear when
    the page is rerun. They are also kept in the session state
    (`KEY_NOTICES`), and are shown here, at the start of the next render.
    Nothing is shown when no notice is pending.
    """
    notices = st.session_state.pop(KEY_NOTICES, [])
    for notice in notices:
        st.warning(notice)


def _load_fields() -> dict[str, dict[str, dict[Literal["required"], bool]]]:
    """
    Loads field configuration from a JSON file.

    This function reads a JSON file containing field definitions and returns the data as a nested dictionary.
    The expected structure of the JSON file is:

    {
        "entry_type_1": {
            "field_name_1": {"required": true},
            "field_name_2": {"required": false}
        },
        "entry_type_2": {
            "field_name_3": {"required": true}
        }
    }

    Raises
    ------
    FileNotFoundError
        If the JSON file does not exist.

    Returns
    -------
    dict[str, dict[str, dict[Literal["required"], bool]]]
        A nested dictionary containing field information, where:
        - The first-level keys are entry types.
        - The second-level keys are field names.
        - The third-level dictionary contains field attributes (e.g., whether the field is required).
    """
    if not FILEPATH_FIELDS.is_file():
        raise FileNotFoundError(f"{FILEPATH_FIELDS}")
    with open(FILEPATH_FIELDS, mode="r", encoding=ENCODING) as f:
        dict_fields = json.load(f)
    return dict_fields


# Load field definitions from the JSON file and create an immutable mapping
MAP_FIELDS = MappingProxyType(_load_fields())
"""
An immutable mapping of field definitions loaded from a JSON file.

MAP_FIELDS is a `MappingProxyType` object that provides a read-only view of
the nested dictionary returned by `_load_fields()`. This structure defines
fields for different entry types, specifying attributes such as whether
a field is required.

Example structure:
------------------
{
    "article": {
        "author": {"required": True},
        "title": {"required": True},
        "year": {"required": True}
    },
    "book": {
        "author": {"required": True},
        "title": {"required": True},
        "publisher": {"required": True},
        "year": {"required": True}
    }
}

Since `MappingProxyType` makes the dictionary immutable, it prevents accidental
modifications of field definitions at runtime.

Raises
------
FileNotFoundError
    If the JSON file containing field definitions does not exist.

See Also
--------
_load_fields : Function that loads the field definitions from a JSON file.
"""


MAP_REQUIRED_FIELDS = {
    entry_type: set(
        field
        for field in MAP_FIELDS[entry_type]
        if MAP_FIELDS[entry_type][field]["required"]
    )
    for entry_type in MAP_FIELDS
}
"""
MAP_REQUIRED_FIELDS is a dictionary that creates a set of required fields for each entry type.

The keys of the dictionary are entry types, and the values are sets containing the fields that have the 'required' attribute set to True for that entry type.

For each entry type in the MAP_FIELDS dictionary, the required fields (where 'required' is True) are extracted and stored as sets.

Dictionary structure:
- Key: Entry type (e.g., "user", "order")
- Value: Set of required fields (e.g., {"name", "email", "address"})

This allows easy identification of required fields for a specific entry type.
"""


def entrytype4doi(entrytype: str) -> str:
    """
    Map an entry type to its corresponding DOI entry type.

    Parameters
    ----------
    entrytype : str
        The entry type to map (e.g., "article", "book").

    Returns
    -------
    str
        The corresponding DOI entry type (e.g., "journal", "misc").

    Notes
    -----
    If the provided entry type is not found in `_MAP_ENTRYTYPE4DOI`, a warning is logged, and
    "misc" is returned as the default.

    Examples
    --------
    entrytype4doi("article") -> "journal"
    entrytype4doi("unknown") -> "misc" and logs a warning.
    """
    if entrytype in _MAP_ENTRYTYPE4DOI:
        return _MAP_ENTRYTYPE4DOI[entrytype]
    else:
        _logger.warning(f"Unknown entrytype: {entrytype}. Use 'misc' instead.")
        return "misc"
