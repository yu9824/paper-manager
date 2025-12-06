"""BibTeXファイルの読み込み機能を提供するモジュール。

このモジュールは、BibTeX形式のファイルから論文情報を読み込む
機能を提供します。

主な関数:
    load_bib: BibTeXファイルまたは文字列から論文エントリを読み込む

例:
    >>> from paper_manager.bib import load_bib
    >>> from io import StringIO
    >>> bib_text = '''
    ... @article{example,
    ...   title={Example Paper},
    ...   author={John Doe},
    ...   year={2024}
    ... }
    ... '''
    >>> entries = load_bib(StringIO(bib_text))
    >>> len(entries)
    1

"""

from ._load import load_bib

__all__ = ("load_bib",)
