import io
import os
from logging import DEBUG
from pathlib import Path
from types import MappingProxyType
from typing import Union

# https://pybtex.org/
import pybtex.database
from pybtex.database.input import bibtex

from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)


def format_person(person: "pybtex.database.Person") -> str:
    """BibTeX の人名を ``<名> <姓>`` の形の文字列に整形する。

    名は ``first_names`` と ``middle_names``、姓は ``prelast_names``
    （``van der`` などの前置詞）と ``last_names`` を空白でつなぎ、空でない
    部分を ``<名> <姓>`` の順に並べます。単純な ``First Last`` と
    ``Last, First`` の名前は ``"First Last"`` になります。

    接尾辞（``lineage_names``。``Jr.`` など）があるときは、BibTeX の正規形
    ``von Last, Jr, First`` にします。``<名> <姓>`` の形ではエクスポート時の
    再解析で接尾辞が姓として読まれ、姓と名が入れ替わるためです。名か姓が
    空でも区切りの ``,`` は残します（接尾辞が名や姓として読まれないように
    するため）。

    Parameters
    ----------
    person : pybtex.database.Person
        整形する人名

    Returns
    -------
    str
        整形した人名。名・姓・接尾辞のすべてが空なら空文字列

    Examples
    --------
    >>> from pybtex.database import Person
    >>> format_person(Person("Smith, John"))
    'John Smith'
    >>> format_person(Person("van der Waals, Johannes Diderik"))
    'Johannes Diderik van der Waals'
    >>> format_person(Person("King, Jr., Martin Luther"))
    'King, Jr., Martin Luther'

    """
    first = " ".join(person.first_names + person.middle_names)
    last = " ".join(person.prelast_names + person.last_names)
    lineage = " ".join(person.lineage_names)
    if lineage:
        return "{}, {},{}".format(last, lineage, f" {first}" if first else "")
    return " ".join(part for part in (first, last) if part)


def load_bib(
    bibtexfile_or_buffer: Union[os.PathLike, str, io.BytesIO, io.StringIO],
) -> "MappingProxyType[str, MappingProxyType[str, str]]":
    """BibTeXファイルまたは文字列から論文エントリを読み込む。

    BibTeX形式のファイル、文字列、またはファイルオブジェクトから
    論文エントリを読み込み、辞書形式で返します。

    Parameters
    ----------
    bibtexfile_or_buffer : Union[os.PathLike, str, io.BytesIO, io.StringIO]
        BibTeXファイルのパス、文字列、またはファイルオブジェクト

    Returns
    -------
    MappingProxyType[str, MappingProxyType[str, str]]
        エントリの辞書（``{cite_key: entry}``の形式）
        各エントリは、フィールド名をキーとする辞書です。

    Examples
    --------
    >>> from paper_manager.bib import load_bib
    >>> from pathlib import Path
    >>> entries = load_bib(Path("./sample.bib"))
    >>> len(entries)
    1

    文字列から読み込む場合::

        >>> from io import StringIO
        >>> bib_text = '@article{example, title={Example}, author={John Doe}}'
        >>> entries = load_bib(StringIO(bib_text))
        >>> entries['example']['title']
        'Example'

    Notes
    -----
    - 著者名は :func:`format_person` で整形され、" and " で結合されます。
      整形した結果が空の人名は捨てます
    - 著者が 1 人もいないエントリ（著者のない BibTeX など）には ``author``
      の項目を作りません（空文字列の著者は必須項目の判定を通ってしまうため）
    - エントリタイプは ``ENTRYTYPE`` フィールドに追加されます

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

        # 著者のない BibTeX では persons に "author" がない
        list_authors: list[str] = [
            name
            for name in map(format_person, entry.persons.get("author", ()))
            if name
        ]
        if list_authors:
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
