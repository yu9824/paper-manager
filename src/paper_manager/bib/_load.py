import io
import os
from logging import DEBUG
from pathlib import Path
from types import MappingProxyType
from typing import Union

from pybtex.database import Entry, Person
from pybtex.database.input import bibtex  # https://github.com/chbrown/pybtex

from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)


def load_bib(
    bibtexfile_or_buffer: Union[os.PathLike, str, io.BytesIO, io.StringIO],
) -> "MappingProxyType[str, MappingProxyType[str, str]]":
    bib_parser = bibtex.Parser()

    _logger.debug(type(bibtexfile_or_buffer))

    if isinstance(bibtexfile_or_buffer, io.BytesIO):
        bibdata = bib_parser.parse_bytes(bibtexfile_or_buffer.getvalue())
    elif isinstance(bibtexfile_or_buffer, io.StringIO):
        bibdata = bib_parser.parse_string(bibtexfile_or_buffer.getvalue())
    elif isinstance(bibtexfile_or_buffer, (str, os.PathLike)):
        bibdata = bib_parser.parse_file(Path(bibtexfile_or_buffer))
    else:
        raise TypeError(f"{type(bib_parser)}")

    _logger.debug(bibdata)
    # return MappingProxyType(
    #     {
    #         key: MappingProxyType(dict(entry.fields))
    #         for key, entry in bibdata.entries.items()
    #     }
    # )

    dict_entries: "dict[str, MappingProxyType]" = dict()
    for key, entry in bibdata.entries.items():
        # typing
        key: str
        entry: Entry

        dict_entry: dict[str, str] = dict(entry.fields)

        list_authors = list()
        for author in entry.persons["author"]:
            # HACK: typing
            author: Person

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
