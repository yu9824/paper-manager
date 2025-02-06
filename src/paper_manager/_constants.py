from pathlib import Path

__all__ = (
    "DIRPATH_ROOT",
    "DIRPATH_APP",
    "DIRPATH_DATA",
    "DIRPATH_PDF",
    "FILEPATH_LIST",
    "COLS_TABLE",
    "ENCODING",
)

DIRPATH_ROOT = Path(__file__).parent

DIRPATH_APP = DIRPATH_ROOT / "_app"

# DIRPATH_DATA = Path(os.environ["HOME"], "Documents/paper-manager")
DIRPATH_DATA = DIRPATH_APP / "data"  # for debug


DIRPATH_PDF = DIRPATH_DATA / "pdf"
FILEPATH_LIST = DIRPATH_DATA / "list.json"
FILEPATH_FIELDS = DIRPATH_APP / "fields.json"

COLS_TABLE = (
    "year",
    "author",
    "title",
    "journal",
    "volume",
    "number",
    "pages",
    "DOI",
)

ENCODING = "utf-8"
