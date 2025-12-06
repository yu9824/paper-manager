"""論文エントリと論文リストを管理するモジュール。

このモジュールは、学術論文の情報を管理するためのクラスを提供します。

主なクラス:
    Entry: 個々の論文エントリを表す辞書風のクラス
    PaperList: 複数の論文エントリを管理する辞書風のクラス

例:
    >>> from paper_manager.entry import Entry, PaperList
    >>> entry = Entry({
    ...     "ENTRYTYPE": "article",
    ...     "title": "Example Paper",
    ...     "author": "John Doe",
    ...     "year": "2024"
    ... })
    >>> paper_list = PaperList({"key1": entry})
    >>> len(paper_list)
    1

"""

from ._entry import Entry, PaperList

__all__ = ("Entry", "PaperList")
