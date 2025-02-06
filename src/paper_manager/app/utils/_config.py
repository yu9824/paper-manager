import json
from collections.abc import Callable
from types import MappingProxyType
from typing import Literal

import streamlit as st

from paper_manager._constants import DIRPATH_APP, ENCODING, FILEPATH_FIELDS
from paper_manager.entry import PaperList
from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)

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

    This class sets up the page configuration, initializes a sidebar with navigation links,
    and ensures that the `PaperList` is synchronized between the session state and a file.

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
        - Adds navigation links to the sidebar.
        - Ensures that `PaperList` is loaded into the session state.
        - Calls the stored function (`_callable`).
        - Saves the updated `PaperList` back to the file.

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
