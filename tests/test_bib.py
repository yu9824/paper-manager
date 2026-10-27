"""BibTeX 読み込み (``load_bib``) の単体テスト.

対象は次のとおり。

- 同じ内容の BibTeX をテキスト (``io.StringIO``)・バイト列
  (``io.BytesIO``)・ファイルパス (``Path`` と ``str``) のそれぞれで渡したとき
  に、同じ結果が得られること (要件 4.1)
- 各著者が「名 姓」の形式で `` and `` 区切りの 1 つの文字列にまとめられ、
  エントリタイプが ``ENTRYTYPE`` として付与され、引用キーが小文字化される
  こと (要件 4.2)
- 複数のエントリを含む BibTeX で、すべてのエントリが引用キーごとに順序を
  保って返ること (要件 4.3)
- 対応していない型の入力で ``TypeError`` が発生すること (要件 4.4)
- 既知バグ (KB2c: 著者のない BibTeX、KB4c: 複合姓・ミドルネームの欠落) を、
  本来あるべき振る舞いを期待する strict な xfail として記録すること
  (要件 13.1, 13.2)

``load_bib`` は ``str`` を **ファイルパス** として扱う (BibTeX の本文ではな
い)。本文を渡すときは ``io.StringIO`` か ``io.BytesIO`` に包む。素の
``bytes`` は未対応で ``TypeError`` になる。

ファイルパスの読み込みと ``io.BytesIO`` の復号はロケールの既定エンコーディ
ングに依存するため、3 つの入力形式の比較は ASCII だけの BibTeX で行い、非
ASCII の検証は ``io.StringIO`` に限る。
"""

import io
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Union

import pytest

from paper_manager.bib import load_bib

# 型の別名. ``load_bib`` に渡す入力 (テキスト・バイト列・パス).
Source = Union[io.StringIO, io.BytesIO, Path, str]

# 3 つの入力形式で比べる BibTeX. ASCII だけで、2 エントリ (article と book)。
# 引用キーに大文字を含み、著者は「名 姓」と「姓, 名」の両方の書き方を使う。
# ミドルネームと複合姓は KB4c の領分なので含めない。
BIB_TEXT = """\
@article{Smith2020ABC,
  author = {John Smith and Jones, Alice},
  title = {A Study of Things},
  journal = {Journal of Things},
  year = {2020}
}

@book{Second2021,
  author = {Bob Brown},
  title = {The Book},
  publisher = {Example Press},
  year = {2021}
}
"""

# ``BIB_TEXT`` を読み込んだ結果の期待値 (引用キーは小文字化される).
EXPECTED_ENTRIES = {
    "smith2020abc": {
        "ENTRYTYPE": "article",
        "author": "John Smith and Alice Jones",
        "title": "A Study of Things",
        "journal": "Journal of Things",
        "year": "2020",
    },
    "second2021": {
        "ENTRYTYPE": "book",
        "author": "Bob Brown",
        "title": "The Book",
        "publisher": "Example Press",
        "year": "2021",
    },
}


def to_plain(
    result: Mapping[str, Mapping[str, str]],
) -> dict[str, dict[str, str]]:
    """読み込み結果 (読み取り専用の入れ子の写像) を通常の辞書にする.

    Parameters
    ----------
    result : Mapping[str, Mapping[str, str]]
        ``load_bib`` の戻り値.

    Returns
    -------
    dict[str, dict[str, str]]
        引用キーごとのフィールド辞書. 比較やエラー表示がしやすい。
    """
    return {key: dict(entry) for key, entry in result.items()}


def as_text(bib: str, tmp_path: Path) -> io.StringIO:
    """BibTeX をテキストのバッファにする."""
    return io.StringIO(bib)


def as_bytes(bib: str, tmp_path: Path) -> io.BytesIO:
    """BibTeX を UTF-8 のバイト列のバッファにする."""
    return io.BytesIO(bib.encode("utf-8"))


def as_path(bib: str, tmp_path: Path) -> Path:
    """BibTeX を ``tmp_path`` 上のファイルに書き、その ``Path`` を返す."""
    path = tmp_path / "refs.bib"
    path.write_text(bib, encoding="utf-8")
    return path


def as_str_path(bib: str, tmp_path: Path) -> str:
    """BibTeX を ``tmp_path`` 上のファイルに書き、その ``str`` を返す."""
    return str(as_path(bib, tmp_path))


# 入力形式の名前 -> BibTeX から入力を作る関数.
INPUT_FORMS: "dict[str, Callable[[str, Path], Source]]" = {
    "text": as_text,
    "bytes": as_bytes,
    "path": as_path,
    "str-path": as_str_path,
}


def load_single(bib: str) -> dict[str, str]:
    """1 エントリだけの BibTeX を読み込み、そのエントリを返す.

    Parameters
    ----------
    bib : str
        エントリを 1 つだけ含む BibTeX のテキスト.

    Returns
    -------
    dict[str, str]
        唯一のエントリのフィールド辞書.
    """
    result = to_plain(load_bib(io.StringIO(bib)))
    assert len(result) == 1, result
    return next(iter(result.values()))


def article_with_author(author: str) -> str:
    """著者の書き方だけを変えた ``@article`` の BibTeX を作る."""
    return "@article{k, author = {%s}, title = {T}, year = {2020}}" % author


# -- 要件 4.1: 3 つの入力形式 -------------------------------------------------


@pytest.mark.parametrize(
    "make_source", INPUT_FORMS.values(), ids=list(INPUT_FORMS)
)
def test_each_input_form_loads_the_expected_entries(
    make_source: "Callable[[str, Path], Source]", tmp_path: Path
) -> None:
    result = load_bib(make_source(BIB_TEXT, tmp_path))

    # 空の結果で偽の成功にならないよう、中身を個別に確かめる。
    assert list(result) == ["smith2020abc", "second2021"]
    assert result["smith2020abc"]["title"] == "A Study of Things"
    assert result["smith2020abc"]["author"] == "John Smith and Alice Jones"
    assert result["second2021"]["ENTRYTYPE"] == "book"
    assert to_plain(result) == EXPECTED_ENTRIES


def test_all_input_forms_give_equal_results(tmp_path: Path) -> None:
    results = {
        name: to_plain(load_bib(make_source(BIB_TEXT, tmp_path)))
        for name, make_source in INPUT_FORMS.items()
    }

    assert set(results) == {"text", "bytes", "path", "str-path"}
    reference = results["text"]
    # 比較の基準が空だと「どれも空で等しい」という偽の成功になる。
    assert reference["smith2020abc"]["title"] == "A Study of Things"
    assert reference["second2021"]["author"] == "Bob Brown"
    for name, result in results.items():
        assert result == reference, name


# -- 要件 4.2: 著者の整形・エントリタイプ・引用キー ----------------------------


@pytest.mark.parametrize(
    ("raw_author", "expected"),
    [
        pytest.param("John Smith", "John Smith", id="first-last"),
        pytest.param("Smith, John", "John Smith", id="last-comma-first"),
        pytest.param(
            "John Smith and Alice Jones",
            "John Smith and Alice Jones",
            id="two-first-last",
        ),
        pytest.param(
            "Smith, John and Jones, Alice and Brown, Bob",
            "John Smith and Alice Jones and Bob Brown",
            id="three-last-comma-first",
        ),
        pytest.param(
            "John Smith and Jones, Alice",
            "John Smith and Alice Jones",
            id="mixed-notation",
        ),
        pytest.param(
            "Smith-Jones, Mary", "Mary Smith-Jones", id="hyphenated-surname"
        ),
    ],
)
def test_authors_are_joined_as_first_last_with_and(
    raw_author: str, expected: str
) -> None:
    entry = load_single(article_with_author(raw_author))

    assert isinstance(entry["author"], str)
    assert entry["author"] == expected


def test_japanese_text_is_loaded_with_first_last_authors() -> None:
    bib = (
        "@article{yamada2021,\n"
        "  author = {山田, 太郎 and 佐藤, 花子},\n"
        "  title = {深層学習による画像認識の研究},\n"
        "  journal = {情報処理学会論文誌},\n"
        "  year = {2021}\n"
        "}\n"
    )

    entry = load_single(bib)

    assert entry == {
        "ENTRYTYPE": "article",
        "author": "太郎 山田 and 花子 佐藤",
        "title": "深層学習による画像認識の研究",
        "journal": "情報処理学会論文誌",
        "year": "2021",
    }


@pytest.mark.parametrize(
    ("bib_type", "expected"),
    [
        ("article", "article"),
        ("book", "book"),
        ("inproceedings", "inproceedings"),
        ("misc", "misc"),
        ("techreport", "techreport"),
        # pybtex はエントリタイプを小文字に正規化する。
        ("InProceedings", "inproceedings"),
        ("ARTICLE", "article"),
    ],
)
def test_entry_type_is_added_as_entrytype(
    bib_type: str, expected: str
) -> None:
    bib = "@%s{k, author = {John Smith}, title = {T}, year = {2020}}"

    entry = load_single(bib % bib_type)

    assert entry["ENTRYTYPE"] == expected
    # 元のフィールドも失われない。
    assert entry["title"] == "T"
    assert entry["year"] == "2020"


@pytest.mark.parametrize(
    ("cite_key", "expected"),
    [
        ("Smith2020ABC", "smith2020abc"),
        ("ALLCAPS", "allcaps"),
        ("alreadylower", "alreadylower"),
    ],
)
def test_cite_key_is_lowercased(cite_key: str, expected: str) -> None:
    bib = "@article{%s, author = {John Smith}, title = {T}}" % cite_key

    result = load_bib(io.StringIO(bib))

    assert list(result) == [expected]
    if cite_key != expected:
        assert cite_key not in result
    assert result[expected]["title"] == "T"


# -- 要件 4.3: 複数エントリ ----------------------------------------------------


def test_all_entries_are_returned_per_cite_key_in_order() -> None:
    bib = """\
@article{Zeta2019,
  author = {Zed Zeta},
  title = {Zeta Paper},
  journal = {J},
  year = {2019}
}
@inproceedings{Alpha2020,
  author = {Smith, Anna and Jones, Ben},
  title = {Alpha Paper},
  booktitle = {Proc. of Things},
  year = {2020}
}
@misc{mid2021,
  author = {Mid Dle},
  title = {Mid Paper}
}
@book{Omega2022,
  author = {Omar Omega},
  title = {Omega Book},
  publisher = {P},
  year = {2022}
}
"""

    result = load_bib(io.StringIO(bib))

    # 引用キーのアルファベット順ではなく、BibTeX に書かれた順序を保つ。
    assert list(result) == ["zeta2019", "alpha2020", "mid2021", "omega2022"]
    assert [entry["title"] for entry in result.values()] == [
        "Zeta Paper",
        "Alpha Paper",
        "Mid Paper",
        "Omega Book",
    ]
    assert [entry["ENTRYTYPE"] for entry in result.values()] == [
        "article",
        "inproceedings",
        "misc",
        "book",
    ]
    assert result["alpha2020"]["author"] == "Anna Smith and Ben Jones"
    assert result["alpha2020"]["booktitle"] == "Proc. of Things"


# -- 要件 4.4: 未対応の入力型 --------------------------------------------------


@pytest.mark.parametrize(
    "unsupported",
    [
        # 素の bytes は未対応 (バイト列は ``io.BytesIO`` に包んで渡す)。
        pytest.param(b"@misc{k, title = {T}}", id="raw-bytes"),
        pytest.param(bytearray(b"@misc{k, title = {T}}"), id="bytearray"),
        pytest.param(None, id="none"),
        pytest.param(123, id="int"),
        pytest.param(1.5, id="float"),
        pytest.param(["@misc{k, title = {T}}"], id="list"),
        pytest.param({"k": "@misc{k, title = {T}}"}, id="dict"),
    ],
)
def test_unsupported_input_type_raises_type_error(unsupported: Any) -> None:
    with pytest.raises(TypeError):
        load_bib(unsupported)


# -- 既知バグ (要件 13.1, 13.2) -----------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN BUG: 著者のない BibTeX の読み込みで KeyError('author') が"
        " 発生する (KB2c). Repro: load_bib(io.StringIO('@misc{nokey,"
        " title = {No Author}, year = {2020}}')) (entry.persons['author']"
        " を無条件に参照するため). 本来は例外にならず、author が空または"
        " 存在しない著者なしのエントリとして読み込まれる"
    ),
)
def test_bib_without_author_loads_as_entry_without_author() -> None:
    bib = "@misc{nokey, title = {No Author}, year = {2020}}"

    result = load_bib(io.StringIO(bib))

    assert list(result) == ["nokey"]
    entry = result["nokey"]
    assert entry.get("author", "") == ""
    assert entry["title"] == "No Author"
    assert entry["ENTRYTYPE"] == "misc"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN BUG: 複合姓・ミドルネームが欠落する (KB4c). Repro:"
        " author = {van der Waals, Johannes Diderik} を読み込むと、"
        "author は 'Johannes Waals' になる (複合姓の 'van der' と、名の"
        " 2 つ目 'Diderik' が欠落。first_names[0] と last_names[0] だけを"
        " 使うため。'Alice B. Jones' は 'Alice Jones' になる). 本来は"
        " 'van der Waals' と 'Johannes Diderik' の両方が author に含まれる"
    ),
)
def test_compound_surname_and_middle_names_are_kept() -> None:
    entry = load_single(
        article_with_author("van der Waals, Johannes Diderik")
    )

    assert "van der Waals" in entry["author"]
    assert "Johannes Diderik" in entry["author"]
