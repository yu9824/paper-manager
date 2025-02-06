import io
import os
from logging import DEBUG
from pathlib import Path
from types import MappingProxyType
from typing import Union

# https://github.com/chbrown/pybtex
import pybtex.database  # type: ignore[import-untyped]
from pybtex.database.input import bibtex  # type: ignore[import-untyped]

from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)


def load_bib(
    bibtexfile_or_buffer: Union[os.PathLike, str, io.BytesIO, io.StringIO],
) -> "MappingProxyType[str, MappingProxyType[str, str]]":
    """load bibtex file (.bib)

    Parameters
    ----------
    bibtexfile_or_buffer : Union[os.PathLike, str, io.BytesIO, io.StringIO]


    Returns
    -------
    MappingProxyType[str, MappingProxyType[str, str]]
        Map of entries (`{cite_key: entry}`)

    Examples
    --------
    >>> map_entries = load_bib("./path/to/sample.bib")
    """
    bib_parser = bibtex.Parser()

    if isinstance(bibtexfile_or_buffer, io.BytesIO):
        bibdata = bib_parser.parse_bytes(bibtexfile_or_buffer.getvalue())
    elif isinstance(bibtexfile_or_buffer, io.StringIO):
        bibdata = bib_parser.parse_string(bibtexfile_or_buffer.getvalue())
    elif isinstance(bibtexfile_or_buffer, (str, os.PathLike)):
        bibdata = bib_parser.parse_file(Path(bibtexfile_or_buffer))
    else:
        raise TypeError(f"{type(bib_parser)}")

    assert isinstance(bibdata, pybtex.database.BibliographyData)

    # _logger.debug(f"{bibdata=}")
    # return MappingProxyType(
    #     {
    #         key: MappingProxyType(dict(entry.fields))
    #         for key, entry in bibdata.entries.items()
    #     }
    # )

    dict_entries: "dict[str, MappingProxyType]" = dict()
    for key, entry in bibdata.entries.items_lower():
        assert isinstance(entry, pybtex.database.Entry)
        dict_entry: dict[str, str] = dict(entry.fields)

        list_authors: list[str] = list()
        author: pybtex.database.Person
        for author in entry.persons["author"]:
            first_name = author.first_names[0] if author.first_names else ""
            last_name = author.last_names[0]
            list_authors.append("{} {}".format(first_name, last_name))

        dict_entry["author"] = " and ".join(list_authors)
        dict_entry["ENTRYTYPE"] = entry.type

        dict_entries[key] = MappingProxyType(dict_entry)
    return MappingProxyType(dict_entries)


if __name__ == "__main__":
    _logger.setLevel(DEBUG)
    entries = load_bib(
        Path(__file__).parent.parent.parent.parent
        / "examples"
        / "bib-example.bib"
    )
    _logger.debug(tuple(entries.items())[1])
