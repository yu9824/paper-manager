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
- 修正した既知バグ (known-bug-fixes spec)。著者のない BibTeX が例外になら
  ず、著者の項目のないエントリとして読み込まれること (KB2c、要件 5.1)。
  人名 (``format_person``) が名 (ミドルネームを含む) と姓 (``van der`` など
  の前置詞を含む) のすべての部分を保って ``<名> <姓>`` の形になり、接尾辞
  (``Jr.`` など) があればエクスポート時の再解析で姓と名が入れ替わらない
  ``von Last, Jr, First`` の形になること、空の人名が捨てられること
  (KB4c、要件 5.3)
- 単純な ``First Last`` と ``Last, First`` の著者 (複数の著者を含む) の出力
  が、修正前の整形の規則と完全に同じであること (修正前の論文リストとの互換)

``load_bib`` は ``str`` を **ファイルパス** として扱う (BibTeX の本文ではな
い)。本文を渡すときは ``io.StringIO`` か ``io.BytesIO`` に包む。素の
``bytes`` は未対応で ``TypeError`` になる。

ファイルパスの読み込みと ``io.BytesIO`` の復号はロケールの既定エンコーディ
ングに依存するため、3 つの入力形式の比較は ASCII だけの BibTeX で行い、非
ASCII の検証は ``io.StringIO`` に限る。
"""

import io
import itertools
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Union

import pytest
from pybtex.database import Person
from pybtex.database.input import bibtex

from paper_manager.app._list import _render_citation
from paper_manager.bib import format_person, load_bib
from paper_manager.entry import Entry

# 型の別名. ``load_bib`` に渡す入力 (テキスト・バイト列・パス).
Source = Union[io.StringIO, io.BytesIO, Path, str]

# 3 つの入力形式で比べる BibTeX. ASCII だけで、2 エントリ (article と book)。
# 引用キーに大文字を含み、著者は「名 姓」と「姓, 名」の両方の書き方を使う。
# ミドルネームと複合姓は、人名の整形の規則のテスト (末尾) で扱う。
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


# -- 修正した既知バグ (要件 13.1, 13.2 / known-bug-fixes 要件 5.1, 5.3) -------


def test_bib_without_author_loads_as_entry_without_author() -> None:
    # KB2c (修正前は KeyError('author'))
    bib = "@misc{nokey, title = {No Author}, year = {2020}}"

    result = load_bib(io.StringIO(bib))

    assert list(result) == ["nokey"]
    entry = result["nokey"]
    assert entry.get("author", "") == ""
    assert entry["title"] == "No Author"
    assert entry["ENTRYTYPE"] == "misc"


def test_compound_surname_and_middle_names_are_kept() -> None:
    # KB4c (修正前は 'Johannes Waals')
    entry = load_single(
        article_with_author("van der Waals, Johannes Diderik")
    )

    assert "van der Waals" in entry["author"]
    assert "Johannes Diderik" in entry["author"]


# -- known-bug-fixes 要件 5.1: 著者のない BibTeX ------------------------------


@pytest.mark.parametrize(
    "author_field",
    [
        pytest.param("", id="no-author-field"),
        pytest.param("author = {},", id="empty-braces"),
        pytest.param("author = { },", id="blank-braces"),
        pytest.param('author = "",', id="empty-quotes"),
        # 人名が 1 つあるが、名も姓も空
        pytest.param("author = {,},", id="one-empty-name"),
        pytest.param("author = {, and ,},", id="two-empty-names"),
    ],
)
def test_bib_without_named_author_has_no_author_key(
    author_field: str,
) -> None:
    # 空文字の著者を入れると必須項目の判定を通ってしまうため、著者が
    # 1 人もいなければ author の項目そのものを作らない。
    bib = "@misc{k, %s title = {T}, year = {2020}}" % author_field

    entry = load_single(bib)

    assert "author" not in entry
    assert entry == {"ENTRYTYPE": "misc", "title": "T", "year": "2020"}


def test_entry_from_bib_without_author_uses_unknown_folder_name() -> None:
    # 登録で作る Entry でも例外にならず、著者を Unknown としてフォルダ名を
    # 作る (known-bug-fixes 2.1 の規則 ``<year> - <first_author> - <title>``)
    bib = "@misc{nokey, title = {No Author}, year = {2020}}"

    entry = Entry(load_single(bib))

    assert "author" not in entry
    assert entry.pdf_dir_name == "2020 - Unknown - No Author"


@pytest.mark.parametrize(
    ("raw_author", "expected"),
    [
        pytest.param(
            "John Smith and ,", "John Smith", id="empty-name-at-end"
        ),
        pytest.param(
            ", and John Smith", "John Smith", id="empty-name-at-start"
        ),
        pytest.param(
            "John Smith and , and Alice Jones",
            "John Smith and Alice Jones",
            id="empty-name-in-between",
        ),
    ],
)
def test_empty_person_names_are_dropped(
    raw_author: str, expected: str
) -> None:
    entry = load_single(article_with_author(raw_author))

    assert entry["author"] == expected


# -- known-bug-fixes 要件 5.3: 人名の整形の規則 (format_person) ---------------
# 名は first + middle、姓は prelast + last を空白でつなぎ、空でない部分を
# ``<名> <姓>`` の順に並べる。接尾辞 (lineage) があるときは BibTeX の正規形
# ``von Last, Jr, First`` にする。


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # 単純な名前 (修正前と同じ出力)
        pytest.param("John Smith", "John Smith", id="first-last"),
        pytest.param("Smith, John", "John Smith", id="last-comma-first"),
        # ミドルネーム
        pytest.param("Alice B. Jones", "Alice B. Jones", id="middle"),
        pytest.param(
            "Jones, Alice Beth Carol",
            "Alice Beth Carol Jones",
            id="two-middle-names",
        ),
        # 前置詞 (prelast)
        pytest.param(
            "van der Waals, Johannes Diderik",
            "Johannes Diderik van der Waals",
            id="prelast-and-middle",
        ),
        pytest.param(
            "Ludwig van Beethoven",
            "Ludwig van Beethoven",
            id="prelast-first-last",
        ),
        pytest.param("van Gogh, Vincent", "Vincent van Gogh", id="prelast"),
        # 前置詞のあとの姓が 2 語 (prelast = de, last = La Fontaine)
        pytest.param(
            "Jean de La Fontaine", "Jean de La Fontaine", id="two-word-last"
        ),
        # 接尾辞 (lineage): von Last, Jr, First
        pytest.param(
            "King, Jr., Martin Luther",
            "King, Jr., Martin Luther",
            id="lineage",
        ),
        pytest.param(
            "van Doe, III, John Paul",
            "van Doe, III, John Paul",
            id="lineage-prelast-middle",
        ),
        # 接尾辞があり名か姓が空: 再解析で接尾辞が名や姓にならないよう、
        # 区切りの ``,`` を残す
        pytest.param("King, Jr.,", "King, Jr.,", id="lineage-without-first"),
        pytest.param(
            ", Jr., Martin", ", Jr., Martin", id="lineage-without-last"
        ),
        # 名か姓だけ
        pytest.param("Cher", "Cher", id="last-only"),
        pytest.param("Smith,", "Smith", id="last-only-with-comma"),
        pytest.param(", Jane", "Jane", id="first-only"),
        pytest.param(
            "{ACME Collaboration}", "{ACME Collaboration}", id="braced-name"
        ),
        # 空の人名
        pytest.param("", "", id="empty"),
        pytest.param(",", "", id="empty-with-comma"),
        pytest.param(", , ", "", id="empty-with-two-commas"),
    ],
)
def test_format_person(name: str, expected: str) -> None:
    assert format_person(Person(name)) == expected


def test_format_person_joins_every_part_in_order() -> None:
    # 解析を介さず、5 種類の部分を直接与えたときの並び
    person = Person(
        first="Anna Maria",
        middle="Beth",
        prelast="von der",
        last="Berg Hof",
        lineage="",
    )
    with_lineage = Person(
        first="Anna", middle="Beth", prelast="von", last="Berg", lineage="Jr."
    )

    assert format_person(person) == "Anna Maria Beth von der Berg Hof"
    assert format_person(with_lineage) == "von Berg, Jr., Anna Beth"


# -- known-bug-fixes 要件 5.3: 修正前の整形との互換 ---------------------------

# 単純な名前 (名・姓がちょうど 1 語ずつで、ミドルネーム・前置詞・接尾辞の
# ない名前) を作る部品。非 ASCII、ハイフン、アポストロフィ、イニシャル、
# 波括弧のアクセントを含む。
SIMPLE_FIRST_NAMES = (
    "John",
    "Alice",
    "J.",
    "Jean-Paul",
    "太郎",
    "{\\'E}mile",
)
SIMPLE_LAST_NAMES = (
    "Smith",
    "Smith-Jones",
    "O'Brien",
    "山田",
    'M{\\"u}ller',
)


def simple_author_names() -> list[str]:
    """``First Last`` と ``Last, First`` の両方の書き方の単純な名前を返す."""
    return [
        notation.format(first=first, last=last)
        for first, last in itertools.product(
            SIMPLE_FIRST_NAMES, SIMPLE_LAST_NAMES
        )
        for notation in ("{first} {last}", "{last}, {first}")
    ]


def simple_author_fields() -> list[str]:
    """1〜3 人の単純な名前を `` and `` でつないだ著者の値を返す.

    1 人はすべての名前、2 人と 3 人は名前の一覧を決まった間隔で選んだ
    組み合わせ (書き方の混在を含む) にする。
    """
    names = simple_author_names()
    count = len(names)
    singles = list(names)
    pairs = [
        " and ".join((names[i], names[(i * 7 + 3) % count]))
        for i in range(count)
    ]
    triples = [
        " and ".join(
            (
                names[i],
                names[(i * 5 + 1) % count],
                names[(i * 11 + 2) % count],
            )
        )
        for i in range(count)
    ]
    return singles + pairs + triples


def authors_before_kb4c_fix(raw_author: str) -> str:
    """修正前 (KB4c の修正前) の ``load_bib`` の著者の整形を再現する.

    修正前の ``load_bib`` の処理をそのまま写したもの (各人名の
    ``first_names[0]`` と ``last_names[0]`` を空白でつなぎ、`` and `` で
    結合する)。修正後の実装の出力をこの規則と比べ、単純な名前で出力が
    変わらないこと (修正前の論文リストとの互換) を確かめる。

    Parameters
    ----------
    raw_author : str
        BibTeX の author の値.

    Returns
    -------
    str
        修正前の規則で整形した著者.
    """
    bibdata = bibtex.Parser().parse_string(article_with_author(raw_author))
    list_authors = []
    for author in bibdata.entries["k"].persons["author"]:
        first_name = author.first_names[0] if author.first_names else ""
        last_name = author.last_names[0]
        list_authors.append("{} {}".format(first_name, last_name))
    return " and ".join(list_authors)


def test_simple_names_are_the_simple_case() -> None:
    # 比較に使う名前が、本当に「単純な名前」であることを確かめる (ミドル
    # ネームなどが混ざっていると、互換の確認の範囲を取り違える)。
    names = simple_author_names()

    assert len(names) == 2 * 6 * 5
    for name in names:
        person = Person(name)
        assert len(person.first_names) == 1, name
        assert len(person.last_names) == 1, name
        assert person.middle_names == [], name
        assert person.prelast_names == [], name
        assert person.lineage_names == [], name


def test_simple_authors_are_formatted_as_before_the_fix() -> None:
    fields = simple_author_fields()
    assert len(fields) == 3 * 60

    mismatches = []
    for raw in fields:
        actual = load_single(article_with_author(raw))["author"]
        expected = authors_before_kb4c_fix(raw)
        if actual != expected:
            mismatches.append((raw, actual, expected))

    assert mismatches == []


# -- known-bug-fixes 要件 5.3: エクスポート時の再解析 -------------------------


def name_parts(person: Person) -> "tuple[tuple[str, ...], ...]":
    """人名の 5 種類の部分 (名・ミドル・前置詞・姓・接尾辞) を返す."""
    return (
        tuple(person.first_names),
        tuple(person.middle_names),
        tuple(person.prelast_names),
        tuple(person.last_names),
        tuple(person.lineage_names),
    )


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("John Smith", id="first-last"),
        pytest.param("Smith, John", id="last-comma-first"),
        pytest.param("Jones, Alice B.", id="middle"),
        pytest.param("van der Waals, Johannes Diderik", id="prelast"),
        pytest.param("Ludwig van Beethoven", id="prelast-first-last"),
        pytest.param("King, Jr., Martin Luther", id="lineage"),
        pytest.param("Gates, III, William Henry", id="lineage-roman"),
        pytest.param("van Doe, Jr., John", id="lineage-prelast"),
        pytest.param("King, Jr.,", id="lineage-without-first"),
        pytest.param(", Jr., Martin", id="lineage-without-last"),
    ],
)
@pytest.mark.parametrize("position", ["first-author", "second-author"])
def test_name_parts_survive_the_export_reparse(
    name: str, position: str
) -> None:
    # エクスポート (詳細の引用情報) は、論文の BibTeX を書き出してから
    # pybtex で解析し直す。読み込んだ人名の部分 (特に姓と名、接尾辞) が
    # 再解析で入れ替わらない。
    other = "Alice Jones"
    names = [name, other] if position == "first-author" else [other, name]
    bib = (
        "@article{k, author = {%s}, title = {T}, journal = {J},"
        " year = {2020}}" % " and ".join(names)
    )
    entry = Entry(load_single(bib))
    # 引用キーは登録時に付く。ここでは人名の再解析だけを確かめるため、
    # 固定の値にする。
    entry["ID"] = "k"

    _, bibdata = _render_citation(entry)

    persons = bibdata.entries["k"].persons["author"]
    assert [name_parts(person) for person in persons] == [
        name_parts(Person(raw)) for raw in names
    ]
