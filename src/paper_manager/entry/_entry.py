import platform
import re
import sys

if sys.version_info >= (3, 9):
    from collections.abc import Sequence
else:
    from typing import Sequence

from paper_manager.entry.typing import ENTRY


def get_key(entry: ENTRY, keys: Sequence[str]) -> str:
    """get paper's key by using 'author' and 'year'

    Parameters
    ----------
    entry : ENTRY
        _description_
    keys : Sequence[str]
        _description_

    Returns
    -------
    str
        _description_
    """
    i = 0
    st_keys = set(keys)

    first_author = entry["author"].split(" and ")[0]
    if (
        key := "{0}{1}_{2}".format(
            first_author.replace(" ", ""), entry["year"], i
        )
    ) in st_keys:
        i += 1
    else:
        return key


def get_filename_pdf(entry: ENTRY) -> str:
    first_author = entry["author"].split(" and ")[0]
    year = entry["year"]
    title = entry["title"]
    return sanitize_filename(
        "{} - {} - {}.pdf".format(first_author, year, title)
    )


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
