"""論文エントリ (``Entry``) の単体テスト.

対象は次のとおり。

- キーの正規化と値の前後の空白の除去 (要件 2.1)
- 識別子 (``get_key``) の形式・重複回避・欠落時の既定値 (要件 2.2)
- PDF フォルダ名 (``pdf_dir_name``) の形式と欠落時の既定値 (要件 2.3)
- 200 文字を超えるフォルダ名の切り詰めと末尾の扱い (要件 2.4)
- ファイル名に使えない文字の置換規則 (``sanitize_filename``) を、OS の判定を
  差し替えて Windows 用とそれ以外の両方について検証する (要件 2.5)
- PDF 一覧 (``get_pdf_files``・``has_pdf``・``get_pdf_count``) が番号順で、
  有無と件数が一覧と一致すること (要件 2.6)
- 旧形式の単一 PDF と旧命名規則の PDF が、既存ファイルを上書きせずに新形式へ
  移行されること (要件 2.7, 2.8)
- 保存先の基準ディレクトリ (``base_dir``) を指定しても、既定の PDF ディレクトリ
  が変化しないこと (要件 2.9)
- test-suite spec で strict な xfail として記録した既知バグ (KB2a, KB2b, KB5,
  KB7) は known-bug-fixes spec で修正し、通常のテストとして検証する。著者が
  ない・空のエントリのフォルダ名が ``Unknown`` で作られること (KB2a,
  known-bug-fixes 要件 2.3)、タイトルがないエントリの PDF の一覧・有無・件数
  が例外にならないこと (KB2b, 同 2.4)、Windows で ``"`` も ``_`` に置換され
  ること (KB5, 同 2.5)、接頭辞がほぼ 200 文字を占めるときもフォルダ名が
  200 文字以内で、末尾が ``.``・空白でないこと (KB7, 同 2.1, 2.2)
- 修正前の規則との一致 (known-bug-fixes 要件 2.6): 接頭辞 197〜200 文字 ×
  タイトル 0〜300 文字の境界で、修正前の規則が 200 文字以内を返す入力では
  ``pdf_dir_name`` が修正前と完全に同一で、200 文字を超えていた入力
  (KB7 の範囲) だけが ``<year> - <first_author>`` に変わること。年が
  200 文字を超える入力も 200 文字以内になること (同 2.1)。後方互換のために
  残した修正前の規則が、このモジュールの独立した写しと一致すること
- フォルダ名が保存先ディレクトリの長さに依存しないこと (known-bug-fixes
  要件 1.3)
- 引用キー (known-bug-fixes 要件 13): ``get_key`` が作るキーに空白・``,``・
  ``{``・``}`` が入らず、``, `` 区切りの著者、接尾辞付きの筆頭著者
  (``King, Jr., Martin``)、波括弧を含む人名、空白を含む年から作ったキーを
  pybtex が解析できること (同 13.1)。これらの文字を含まない著者と年からは、
  修正前の ``get_key`` の写しと完全に同じキーになること (同 13.2)。
  ``sanitize_citation_key`` が除く文字だけを除いて冪等であること、
  ``unique_citation_keys`` が整えたうえで大文字小文字を区別せずに重複しない
  キーを入力と同じ長さ・順序で返し、pybtex の ``BibliographyData`` に全キー
  を加えても重複の例外が出ないこと (同 13.4)
- 修正前の名前 (``legacy_pdf_dir_name``) が KB7 の範囲で、修正後と異なり
  255 文字以内のときだけ値を持つこと。PDF 一覧の取得で、修正前の名前の
  フォルダが現在の名前へ移り、移す先が既にあれば何も移さずに警告が出ること
  (known-bug-fixes 要件 2.7, 12.5)
- 旧形式の単一 PDF が、PDF フォルダが既にあっても空いている番号へ上書き
  せずに移り、移せなかったときは一覧に含まれて警告が出ること (同 8.3, 1.4)。
  同じ対象の警告は、一覧の件数・統計・孤立 PDF の判定から何度呼ばれても、
  1 回の描画で 1 回だけ出ること。再描画の後も解消していない警告は、
  (セッションで 1 回だけに抑えず) 描画のたびに 1 回ずつ出ること
- PDF フォルダと旧形式の単一 PDF のパスが変換後の PDF ルートから作られ
  (Windows を模した ``windows_like_paths`` で ``//`` 付きになる)、一覧と
  移行が変換後の空間で成功すること。変換後のパスが別のディレクトリを指す
  空間 (``converted_space`` の ``redirected``) でも同じ結果になること。
  警告とログには表示用のパスだけを出し、変換後のパスを出さないこと。長い
  パスでも警告のログを出さず、旧形式のファイル名が長すぎても一覧が落ちない
  こと (同 1.1, 1.2, 1.4)

OS の判定は ``paper_manager.entry._entry`` の ``platform.system()`` で行われる。
``set_os`` fixture でそのモジュールの ``platform`` を差し替えるため、実行 OS に
依存しない (要件 14.2)。OS 固有の文字 (``:`` など) を含まないテストは、差し替え
なしでも実行 OS によらず同じ結果になる。

旧形式の移行は非推奨の後方互換コードで、実行すると ``DeprecationWarning`` が
出る。移行を検証するテストでは警告が出ることも確かめ、それ以外のテストでは
警告を無視する。
"""

import errno
import io
import logging
from collections.abc import (
    Callable,
    Collection,
    Iterable,
    Iterator,
    Mapping,
)
from itertools import product
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest
from conftest import (
    PDF_BYTES,
    ConvertedSpace,
    DataPaths,
    SampleData,
    StMessages,
    run_page,
    write_pdf,
)
from pybtex.database import BibliographyData, BibliographyDataError
from pybtex.database import Entry as PybtexEntry
from pybtex.database.input import bibtex

from paper_manager.bib import load_bib
from paper_manager.entry import (
    Entry,
    PaperList,
    _entry,
    sanitize_citation_key,
    unique_citation_keys,
)
from paper_manager.entry._entry import sanitize_filename
from paper_manager.helper import to_display_path, to_fs_path

# steering (tech.md) と要件 2.4 が定めるフォルダ名の最大長.
MAX_DIR_NAME_LENGTH = 200
# 年 ``2020`` と著者 ``Alice Smith`` から作られるフォルダ名の接頭辞 (21 文字).
PREFIX = "2020 - Alice Smith - "


@pytest.fixture
def make_partial_entry(
    make_entry: Callable[..., Entry],
) -> Callable[..., Entry]:
    """フィールドを欠落させたエントリを作る関数を返す.

    ``make_entry`` は既定値を消せない。作成後に ``drop`` で指定した
    フィールドを削除することで、著者・年・タイトルが欠落したエントリを作る。

    Returns
    -------
    Callable[..., Entry]
        ``make_partial_entry(drop=(), **fields) -> Entry``。
    """

    def _make(drop: Iterable[str] = (), **fields: str) -> Entry:
        entry = make_entry(**fields)
        for name in drop:
            del entry[name]
        return entry

    return _make


@pytest.fixture
def set_os(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    """実行 OS の判定結果を差し替える関数を返す.

    ``sanitize_filename`` が使う ``platform.system()`` を、指定した OS 名を
    返すものに差し替える。差し替えは ``paper_manager.entry._entry`` の
    ``platform`` だけに効き、``platform`` モジュール自体は変えない。
    テストの後に元へ戻る。

    Returns
    -------
    Callable[[str], None]
        ``set_os("Windows")`` のように呼ぶ。OS 名は
        ``platform.system()`` が返す値 (``Windows``・``Linux``・``Darwin``)。
    """

    def _set_os(name: str) -> None:
        monkeypatch.setattr(
            _entry, "platform", SimpleNamespace(system=lambda: name)
        )

    return _set_os


def assert_valid_dir_name(name: str) -> None:
    """フォルダ名が 200 文字以内で、末尾が ``.`` や空白でないことを検証する."""
    assert len(name) <= MAX_DIR_NAME_LENGTH, f"{len(name)} chars: {name!r}"
    assert not name.endswith((".", " ")), f"bad tail: {name[-5:]!r}"


# --- キーの正規化と値の空白除去 (要件 2.1) --------------------------------


@pytest.mark.parametrize(
    ("raw_key", "expected_key"),
    [
        ("ENTRYTYPE", "ENTRYTYPE"),
        ("entrytype", "ENTRYTYPE"),
        ("EntryType", "ENTRYTYPE"),
        ("ID", "ID"),
        ("id", "ID"),
        ("Id", "ID"),
        ("DOI", "DOI"),
        ("doi", "DOI"),
        ("Doi", "DOI"),
        ("author", "author"),
        ("Author", "author"),
        ("AUTHOR", "author"),
        ("Title", "title"),
        ("YEAR", "year"),
        ("Journal", "journal"),
        ("tags", "tags"),
    ],
)
def test_entry_normalizes_keys(raw_key: str, expected_key: str) -> None:
    """ENTRYTYPE・ID・DOI は大文字、それ以外のキーは小文字になる."""
    entry = Entry({raw_key: "value"})

    assert dict(entry) == {expected_key: "value"}


@pytest.mark.parametrize(
    "padded",
    [" v", "v ", "  v  ", "\tv\n", "\u3000v\u3000"],
    ids=["leading", "trailing", "both", "tab-newline", "full-width-space"],
)
def test_entry_strips_surrounding_whitespace_of_values(padded: str) -> None:
    """値の前後の空白 (改行・全角空白を含む) が除去される."""
    entry = Entry({"title": padded})

    assert entry["title"] == "v"


def test_entry_keeps_inner_whitespace_of_values() -> None:
    """値の内側の空白は変更されない."""
    entry = Entry({"title": "  A  Study of\tTesting \n"})

    assert entry["title"] == "A  Study of\tTesting"


def test_entry_normalizes_all_keys_and_values_together() -> None:
    """すべてのキーと値が、作成時にまとめて正規化される."""
    entry = Entry(
        {
            "EntryType": " article ",
            "Id": "key1 ",
            "doi": " 10.1234/test",
            "Author": " Alice Smith ",
            "TITLE": "\tA Study\n",
            "Year": "2020",
        }
    )

    assert dict(entry) == {
        "ENTRYTYPE": "article",
        "ID": "key1",
        "DOI": "10.1234/test",
        "author": "Alice Smith",
        "title": "A Study",
        "year": "2020",
    }


# --- 識別子 get_key (要件 2.2) ----------------------------------------------


def test_get_key_format_is_author_year_and_index(
    make_entry: Callable[..., Entry],
) -> None:
    """識別子は「空白を除いた第一著者名 + 年 + ``_`` + 連番」の形式になる."""
    entry = make_entry(author="Alice Smith", year="2020")

    assert entry.get_key([]) == "AliceSmith2020_0"


def test_get_key_uses_first_author_without_any_space(
    make_entry: Callable[..., Entry],
) -> None:
    """第一著者だけを使い、名前の中の空白はすべて取り除かれる."""
    entry = make_entry(
        author="Alice Marie Smith and Bob Jones and Carol Orphan",
        year="2020",
    )

    assert entry.get_key([]) == "AliceMarieSmith2020_0"


@pytest.mark.parametrize(
    "existing",
    [
        pytest.param(["AliceSmith2020_0"], id="list"),
        pytest.param(("AliceSmith2020_0",), id="tuple"),
        pytest.param({"AliceSmith2020_0"}, id="set"),
        pytest.param(frozenset({"AliceSmith2020_0"}), id="frozenset"),
        pytest.param({"AliceSmith2020_0": 1}.keys(), id="dict-keys"),
    ],
)
def test_get_key_avoids_existing_key_in_any_collection(
    make_entry: Callable[..., Entry], existing: Iterable[str]
) -> None:
    """既存キーの集合の型によらず、重複しない連番が付く."""
    entry = make_entry(author="Alice Smith", year="2020")

    assert entry.get_key(existing) == "AliceSmith2020_1"


@pytest.mark.parametrize(
    "indices",
    [(0,), (0, 1), (0, 1, 2), (0, 2), (1,), (0, 1, 2, 3, 4, 5, 6, 7, 8, 9)],
    ids=["0", "0-1", "0-2", "0-and-2", "1-only", "0-9"],
)
def test_get_key_never_returns_an_existing_key(
    make_entry: Callable[..., Entry], indices: tuple
) -> None:
    """連番の欠けがあっても、返る識別子は既存キーと重複しない."""
    entry = make_entry(author="Alice Smith", year="2020")
    existing = {f"AliceSmith2020_{i}" for i in indices}

    key = entry.get_key(existing)

    assert key not in existing
    assert key.startswith("AliceSmith2020_")
    assert key.removeprefix("AliceSmith2020_").isdigit()


def test_get_key_ignores_keys_of_other_author_or_year(
    make_entry: Callable[..., Entry],
) -> None:
    """著者または年が異なるキーは、連番に影響しない."""
    entry = make_entry(author="Alice Smith", year="2020")
    existing = ["BobJones2020_0", "AliceSmith2021_0", "AliceSmith2020x_0"]

    assert entry.get_key(existing) == "AliceSmith2020_0"


def test_get_key_stays_unique_over_repeated_registration(
    make_entry: Callable[..., Entry],
) -> None:
    """同じ著者・年のエントリを順に追加すると、連番で別のキーが付く."""
    paper_list = PaperList({})

    for _ in range(3):
        entry = make_entry(author="Alice Smith", year="2020")
        paper_list[entry.get_key(paper_list.keys())] = entry

    assert sorted(paper_list) == [
        "AliceSmith2020_0",
        "AliceSmith2020_1",
        "AliceSmith2020_2",
    ]


@pytest.mark.parametrize(
    ("dropped", "expected"),
    [
        (("author",), "Unknown2020_0"),
        (("year",), "AliceSmithYYYY_0"),
        (("author", "year"), "UnknownYYYY_0"),
    ],
    ids=["no-author", "no-year", "no-author-no-year"],
)
def test_get_key_uses_defaults_for_missing_author_and_year(
    make_partial_entry: Callable[..., Entry],
    dropped: tuple,
    expected: str,
) -> None:
    """著者が欠落すると ``Unknown``、年が欠落すると ``YYYY`` が使われる."""
    entry = make_partial_entry(drop=dropped, author="Alice Smith", year="2020")

    assert entry.get_key([]) == expected


def test_get_key_avoids_existing_key_built_from_defaults(
    make_partial_entry: Callable[..., Entry],
) -> None:
    """既定値から作られた識別子も、重複しないように連番が進む."""
    entry = make_partial_entry(drop=("author", "year"))

    assert entry.get_key(["UnknownYYYY_0"]) == "UnknownYYYY_1"


# --- 引用キー: 読めない文字の除去と重複の解消 (known-bug-fixes 要件 13) -----

# pybtex が波括弧で囲んだ引用キーの区切りとして読む文字 (空白・``,``・``}``)
# と、波括弧の対応を崩す ``{``.
UNREADABLE_PUNCTUATION = ",{}"
# ``str.isspace`` が真になる文字 (最後の空白は U+3000)
WHITESPACE_CHARS = tuple(
    chr(code) for code in range(0x3100) if chr(code).isspace()
)


def _is_readable_key(key: str) -> bool:
    """``key`` が空白・``,``・``{``・``}`` を含まなければ真を返す."""
    return key != "" and not any(
        c.isspace() or c in UNREADABLE_PUNCTUATION for c in key
    )


def _get_key_before_fix(entry: Entry, keys: Iterable[str]) -> str:
    """修正前の ``Entry.get_key`` の写し (後方互換の比較の基準).

    筆頭著者の ``" "`` だけを除き、年と連番をつなぐ。既存のキーとの重複は
    大文字小文字を区別して判定する。
    """
    i = 0
    st_keys = set(keys)
    first_author = (
        entry["author"].split(" and ")[0] if "author" in entry else "Unknown"
    )
    while (
        key := "{0}{1}_{2}".format(
            first_author.replace(" ", ""), entry.get("year", "YYYY"), i
        )
    ) in st_keys:
        i += 1
    return key


def _parse_keys(key: str) -> list[str]:
    """``key`` を引用キーにした最小の BibTeX を pybtex で解析して返す.

    Returns
    -------
    list[str]
        pybtex が読み取ったエントリの引用キーの一覧。
    """
    data = bibtex.Parser().parse_string(f"@article{{{key}, title = {{T}}}}")
    return list(data.entries.keys())


def _add_to_bibliography(keys: Iterable[str]) -> BibliographyData:
    """pybtex の ``BibliographyData`` に ``keys`` を順に加えて返す.

    引用キーが (大文字小文字を区別せずに) 重複すると
    ``BibliographyDataError`` が送出される。
    """
    data = BibliographyData()
    for key in keys:
        data.add_entry(key, PybtexEntry("article"))
    return data


# 修正前と同じキーになる著者・年 (読めない文字を含まない)
READABLE_AUTHORS = (
    "Alice Smith",
    "Alice Marie Smith and Bob Jones and Carol Orphan",
    "Smith",
    "Alice  Smith",
    "O'Brien Pat",
    "Dr. A.-B. Smith-Jones",
    "Zoë Müller",
    "山田 太郎",
    "van der Waals Johannes Diderik",
    'Say "Hi"',
    "Back\\slash",
    "Alice (Al) Smith",
)
READABLE_YEARS = ("2020", "1999", "YYYY", "n.d.", "2020a", "20-21", "'20")

# 空白・``,``・``{``・``}`` を含む著者と年から、除いた後の期待するキーを返す
UNREADABLE_KEY_CASES = [
    pytest.param(
        "Smith, Alice", "2020", "SmithAlice2020_0", id="comma-space-author"
    ),
    pytest.param("Smith,Alice", "2020", "SmithAlice2020_0", id="comma-author"),
    pytest.param(
        "Smith, Alice and Bob Jones",
        "2020",
        "SmithAlice2020_0",
        id="comma-author-of-many",
    ),
    pytest.param(
        "King, Jr., Martin Luther",
        "2020",
        "KingJr.MartinLuther2020_0",
        id="suffix-form",
    ),
    pytest.param(
        "Doe, {Jr.}, John", "2020", "DoeJr.John2020_0", id="braced-suffix"
    ),
    pytest.param(
        'M{\\"u}ller, Hans', "2020", 'M\\"ullerHans2020_0', id="braces-accent"
    ),
    pytest.param(
        "{ACME Collaboration}",
        "2020",
        "ACMECollaboration2020_0",
        id="braced-name",
    ),
    pytest.param("{{A} B} C", "2020", "ABC2020_0", id="nested-braces"),
    pytest.param(
        "Alice Smith", "2020 a", "AliceSmith2020a_0", id="year-space"
    ),
    pytest.param("Alice Smith", "n. d.", "AliceSmithn.d._0", id="year-dots"),
    pytest.param("Alice Smith", "20,20", "AliceSmith2020_0", id="year-comma"),
    pytest.param(
        "Alice Smith", "{2020}", "AliceSmith2020_0", id="year-braces"
    ),
    pytest.param("Alice\tSmith", "2020", "AliceSmith2020_0", id="tab"),
    pytest.param("Alice\nSmith", "2020", "AliceSmith2020_0", id="newline"),
    pytest.param("Alice\u00a0Smith", "2020", "AliceSmith2020_0", id="nbsp"),
    pytest.param(
        "Alice\u3000Smith", "2020", "AliceSmith2020_0", id="ideographic-space"
    ),
    pytest.param("Alice Smith", "20\t20", "AliceSmith2020_0", id="year-tab"),
]


@pytest.mark.parametrize("year", READABLE_YEARS)
@pytest.mark.parametrize("author", READABLE_AUTHORS)
def test_get_key_is_unchanged_without_unreadable_characters(
    make_entry: Callable[..., Entry], author: str, year: str
) -> None:
    """読めない文字を含まない著者と年からは、修正前と同じキーを作る (13.2).

    既存のキーがない場合と、既存のキーと重なる場合の両方で、修正前の
    ``get_key`` の写しと完全に一致する。
    """
    entry = make_entry(author=author, year=year)
    first = _get_key_before_fix(entry, ())
    second = _get_key_before_fix(entry, {first})
    existing_sets: list[Collection[str]] = [
        (),
        {first},
        {first, second},
        {"Other2020_0", first.lower()},
    ]

    for existing in existing_sets:
        assert entry.get_key(existing) == _get_key_before_fix(
            entry, existing
        ), existing


@pytest.mark.parametrize(("author", "year", "expected"), UNREADABLE_KEY_CASES)
def test_get_key_drops_unreadable_characters(
    make_entry: Callable[..., Entry], author: str, year: str, expected: str
) -> None:
    """空白・``,``・``{``・``}`` を除き、ほかの文字は変えない (13.1)."""
    entry = make_entry(author=author, year=year)

    key = entry.get_key([])

    assert key == expected
    assert _is_readable_key(key)


@pytest.mark.parametrize(("author", "year", "expected"), UNREADABLE_KEY_CASES)
def test_get_key_result_is_readable_by_pybtex(
    make_entry: Callable[..., Entry], author: str, year: str, expected: str
) -> None:
    """作ったキーを引用キーにした BibTeX を pybtex が解析できる (13.1)."""
    key = make_entry(author=author, year=year).get_key([])

    assert _parse_keys(key) == [key]


def test_get_key_checks_duplicates_after_removing_characters(
    make_entry: Callable[..., Entry],
) -> None:
    """重複の判定は、読めない文字を除いたキーで行う (13.1)."""
    entry = make_entry(author="Smith, Alice", year="2020")

    assert entry.get_key(["SmithAlice2020_0"]) == "SmithAlice2020_1"
    assert (
        entry.get_key(["SmithAlice2020_0", "SmithAlice2020_1"])
        == "SmithAlice2020_2"
    )
    # 整える前の形は既存のキーとして数えない (整えたキーが空いていれば使う)
    assert entry.get_key(["Smith, Alice2020_0"]) == "SmithAlice2020_0"


def test_get_key_duplicate_check_stays_case_sensitive(
    make_entry: Callable[..., Entry],
) -> None:
    """既存のキーとの重複判定は、大文字小文字を区別したままにする (13.2)."""
    entry = make_entry(author="Alice Smith", year="2020")

    assert entry.get_key(["alicesmith2020_0"]) == "AliceSmith2020_0"


# 2.4 の ``load_bib`` が返す人名の形 (接尾辞、波括弧、アクセント) を含む著者
BIB_AUTHORS = (
    "King, Jr., Martin Luther",
    "King, Jr.,",
    ", Jr., Martin",
    "Doe, {Jr.}, John",
    "{ACME Collaboration}",
    "{\\'E}mile Zola",
    'M{\\"u}ller, Hans',
    "{\\relax Ch}arles Dickens",
    "O'Brien, Pat",
    "山田, 太郎",
    "van der Waals, Johannes Diderik",
    "{\\O}ystein Ore",
    "Gates, III, William Henry",
    "{{Nested} Braces} Inc",
    "Smith, Alice",
    "Alice Smith",
)


def _entry_from_bib(first_author: str, year: str = "2020") -> Entry:
    """``load_bib`` の出力から Entry を作る (筆頭著者は ``first_author``)."""
    bib = (
        "@article{k, author = {%s and Alice Jones}, title = {T},"
        " journal = {J}, year = {%s}}" % (first_author, year)
    )
    return Entry(dict(load_bib(io.StringIO(bib))["k"]))


@pytest.mark.parametrize("raw", BIB_AUTHORS)
def test_get_key_from_load_bib_output_is_readable_by_pybtex(raw: str) -> None:
    """``load_bib`` の人名から作ったキーを pybtex が解析できる (13.1)."""
    key = _entry_from_bib(raw).get_key([])

    assert _is_readable_key(key)
    assert _parse_keys(key) == [key]


def test_get_key_for_suffix_first_author_has_no_comma() -> None:
    """接尾辞の形 (``King, Jr., Martin``) の筆頭著者でも ``,`` が入らない."""
    entry = _entry_from_bib("King, Jr., Martin Luther")

    assert entry["author"].split(" and ")[0] == "King, Jr., Martin Luther"
    assert entry.get_key([]) == "KingJr.MartinLuther2020_0"


@pytest.mark.parametrize("char", [*WHITESPACE_CHARS, ",", "}"], ids=repr)
def test_pybtex_cannot_read_a_key_with_a_separator_character(
    char: str,
) -> None:
    """空白・``,``・``}`` を含むキーは、pybtex が元のままでは読めない.

    pybtex はこれらの文字をキーの区切りとして読む (前提の確認)。``{`` は
    pybtex が読めるが、波括弧の対応を崩さないために合わせて除く。
    """
    key = f"A{char}B2020_0"

    try:
        keys = _parse_keys(key)
    except Exception:  # noqa: BLE001  pybtex が構文エラーにする場合
        return
    assert keys != [key]


@pytest.mark.parametrize("char", [*WHITESPACE_CHARS, ",", "{", "}"], ids=repr)
def test_sanitize_citation_key_removes_each_unreadable_character(
    char: str,
) -> None:
    """空白 (``str.isspace`` が真の文字)・``,``・``{``・``}`` を除く (13.1)."""
    assert (
        sanitize_citation_key(f"Alice{char}Smith2020_0") == "AliceSmith2020_0"
    )
    assert sanitize_citation_key(f"{char}Alice{char}{char}") == "Alice"


@pytest.mark.parametrize(
    "key",
    [
        "AliceSmith2020_0",
        "O'Brien2020_0",
        "Smith.Jr-2020_0",
        'M\\"uller2020_0',
        "Zoë山田2020_0",
        'a"b',
        "a\\b",
        "a:b/c",
        "a#b%c@d(e)f=g",
        "Unknown",
        "x" * 300,
    ],
)
def test_sanitize_citation_key_keeps_readable_keys(key: str) -> None:
    """読めない文字を含まないキーはそのまま返す (13.2)."""
    assert sanitize_citation_key(key) == key


@pytest.mark.parametrize(
    "key", ["", " ", ",", "{}", " \t\n,{ }", "\u3000"], ids=repr
)
def test_sanitize_citation_key_returns_unknown_when_nothing_is_left(
    key: str,
) -> None:
    """除いた結果が空になるときは ``Unknown`` にする (13.1)."""
    assert sanitize_citation_key(key) == "Unknown"


@pytest.mark.parametrize(
    "key",
    [
        "Smith, Alice2020_0",
        'M{\\"u}ller2020_0',
        "A B\tC\nD,E{F}G",
        " , ",
        "AliceSmith2020_0",
        "Unknown",
    ],
    ids=repr,
)
def test_sanitize_citation_key_is_idempotent_and_readable(key: str) -> None:
    """整えたキーを整えても変わらず、pybtex が解析できる (13.1)."""
    once = sanitize_citation_key(key)

    assert sanitize_citation_key(once) == once
    assert _is_readable_key(once)
    assert _parse_keys(once) == [once]


def test_pybtex_rejects_keys_that_differ_only_in_case() -> None:
    """pybtex は大文字小文字だけ違うキーを重複として例外にする (前提の確認)."""
    with pytest.raises(BibliographyDataError):
        _add_to_bibliography(["Smith2020_0", "smith2020_0"])


def test_unique_citation_keys_of_empty_input_is_empty() -> None:
    """空の入力には空のリストを返す (13.4)."""
    assert unique_citation_keys([]) == []
    assert unique_citation_keys(()) == []


def test_unique_citation_keys_applies_sanitize_without_duplicates() -> None:
    """重複がなければ、各要素に ``sanitize_citation_key`` を適用したもの."""
    keys = ["Smith, Alice2020_0", "Doe2020_0", "A B", "{}", "King,Jr.2020_1"]

    assert unique_citation_keys(keys) == [
        sanitize_citation_key(key) for key in keys
    ]
    assert unique_citation_keys(tuple(keys)) == unique_citation_keys(keys)


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        pytest.param(
            ["Smith2020_0", "smith2020_0"],
            ["Smith2020_0", "smith2020_0_1"],
            id="case-only",
        ),
        pytest.param(
            ["SMITH", "Smith", "smith"],
            ["SMITH", "Smith_1", "smith_2"],
            id="three-case-variants",
        ),
        pytest.param(
            ["Smith,Alice", "SmithAlice"],
            ["SmithAlice", "SmithAlice_1"],
            id="same-after-sanitize",
        ),
        pytest.param(
            ["A b", "a, B", "AB"],
            ["Ab", "aB_1", "AB_2"],
            id="same-after-sanitize-and-case",
        ),
        pytest.param(
            ["Key", "key", "key_1"],
            ["Key", "key_2", "key_1"],
            id="skips-existing-suffixed-key",
        ),
        pytest.param(
            ["key_1", "key", "KEY"],
            ["key_1", "key", "KEY_2"],
            id="suffixed-key-before-duplicate",
        ),
        pytest.param(
            ["a", "A", "a_1", "a_2"],
            ["a", "A_3", "a_1", "a_2"],
            id="smallest-free-number",
        ),
        pytest.param(
            ["a", "A", "A", "a_1"],
            ["a", "A_2", "A_3", "a_1"],
            id="generated-keys-do-not-collide",
        ),
        pytest.param(
            ["a", "a", "a"], ["a", "a_1", "a_2"], id="exact-duplicates"
        ),
        pytest.param(["", " "], ["Unknown", "Unknown_1"], id="empty-keys"),
        # pybtex は ``str.lower`` で比べる (``casefold`` ではない)
        pytest.param(
            ["Émile2020_0", "émile2020_0"],
            ["Émile2020_0", "émile2020_0_1"],
            id="non-ascii-case-differs",
        ),
        pytest.param(["İ", "i̇"], ["İ", "i̇_1"], id="dotted-capital-i"),
        pytest.param(["ß", "SS"], ["ß", "SS"], id="sharp-s-is-not-ss"),
    ],
)
def test_unique_citation_keys_resolves_case_insensitive_duplicates(
    keys: list[str], expected: list[str]
) -> None:
    """整えたうえで大文字小文字を区別せずに重複したら、後のものに連番を付ける.

    連番は他のキー (入力の後ろのキーも含む) と重ならない最小の値で、
    pybtex の ``BibliographyData`` に全キーを加えても例外が出ない (13.4)。
    大文字小文字の判定は pybtex と同じ ``str.lower`` で、ASCII に限らない。
    """
    result = unique_citation_keys(keys)

    assert result == expected
    assert len(_add_to_bibliography(result).entries) == len(keys)


def test_unique_citation_keys_properties_over_many_combinations() -> None:
    """キーの組み合わせすべてで、長さ・順序・一意性・冪等性が保たれる (13.4)."""
    pool = ("a", "A", "a_1", "A_1", "a b", "a,", "{a}", "b")

    for keys in product(pool, repeat=4):
        result = unique_citation_keys(keys)
        sanitized = [sanitize_citation_key(key) for key in keys]
        first_seen: dict[str, int] = {}
        for i, key in enumerate(sanitized):
            first_seen.setdefault(key.lower(), i)
        label = f"{keys!r} -> {result!r}"

        assert len(result) == len(keys), label
        assert len({key.lower() for key in result}) == len(keys), label
        assert all(_is_readable_key(key) for key in result), label
        assert len(_add_to_bibliography(result).entries) == len(keys), label
        assert unique_citation_keys(result) == result, label
        for i, (before, after) in enumerate(zip(sanitized, result)):
            if first_seen[before.lower()] == i:
                assert after == before, label  # 最初に現れたキーは変えない
            else:
                prefix, _, number = after.rpartition("_")
                assert prefix == before and number.isdigit(), label
        if len(first_seen) == len(keys):
            assert result == sanitized, label  # 重複がなければ整えるだけ


def test_unique_citation_keys_does_not_touch_the_input() -> None:
    """入力を書き換えず、新しいリストを返す (13.3: 保存された ID を守る)."""
    keys = ["Smith, A2020_0", "smith, a2020_0"]
    snapshot = list(keys)

    result = unique_citation_keys(keys)

    assert keys == snapshot
    assert result is not keys


# --- PDF フォルダ名 pdf_dir_name の形式と既定値 (要件 2.3) ------------------


def test_pdf_dir_name_follows_year_author_title_format(
    make_entry: Callable[..., Entry],
) -> None:
    """フォルダ名は ``<year> - <first_author> - <title>`` 形式になる."""
    entry = make_entry(
        author="Alice Smith", title="A Study of Testing", year="2020"
    )

    assert entry.pdf_dir_name == "2020 - Alice Smith - A Study of Testing"


def test_pdf_dir_name_uses_first_author_only(
    make_entry: Callable[..., Entry],
) -> None:
    """著者が複数いる場合は、第一著者だけがフォルダ名に使われる."""
    entry = make_entry(
        author="Alice Smith and Bob Jones and Carol Orphan",
        title="T",
        year="2020",
    )

    assert entry.pdf_dir_name == "2020 - Alice Smith - T"


def test_pdf_dir_name_keeps_non_ascii_title(
    make_entry: Callable[..., Entry],
) -> None:
    """日本語など ASCII 以外の文字は、そのままフォルダ名に残る."""
    entry = make_entry(
        author="Taro Yamada",
        title="深層学習による画像認識の研究",
        year="2021",
    )

    assert (
        entry.pdf_dir_name == "2021 - Taro Yamada - 深層学習による画像認識の研究"
    )


@pytest.mark.parametrize(
    ("dropped", "expected"),
    [
        (("year",), "YYYY - Alice Smith - T"),
        (("title",), "2020 - Alice Smith - Unknown"),
        (("year", "title"), "YYYY - Alice Smith - Unknown"),
    ],
    ids=["no-year", "no-title", "no-year-no-title"],
)
def test_pdf_dir_name_uses_defaults_for_missing_year_and_title(
    make_partial_entry: Callable[..., Entry], dropped: tuple, expected: str
) -> None:
    """年が欠落すると ``YYYY``、タイトルが欠落すると ``Unknown`` が使われる."""
    entry = make_partial_entry(
        drop=dropped, author="Alice Smith", title="T", year="2020"
    )

    assert entry.pdf_dir_name == expected


@pytest.mark.parametrize(
    ("os_name", "expected"),
    [
        ("Linux", "2020 - Alice Smith - A_B: C"),
        ("Darwin", "2020 - Alice Smith - A_B: C"),
        ("Windows", "2020 - Alice Smith - A_B_ C"),
    ],
)
def test_pdf_dir_name_applies_filename_rules_of_current_os(
    make_entry: Callable[..., Entry],
    set_os: Callable[[str], None],
    os_name: str,
    expected: str,
) -> None:
    """フォルダ名には、実行 OS のファイル名置換規則が適用される.

    ``/`` はどの OS でも置換され (サブフォルダにならない)、``:`` は
    Windows でだけ置換される。
    """
    set_os(os_name)
    entry = make_entry(author="Alice Smith", title="A/B: C", year="2020")

    assert entry.pdf_dir_name == expected


@pytest.mark.parametrize(
    "title",
    ["Study of X.", "Study of X...", "Study of X. .", "Study of X.  "],
    ids=["dot", "dots", "dot-space-dot", "dot-spaces"],
)
def test_pdf_dir_name_has_no_trailing_period_or_space(
    make_entry: Callable[..., Entry], title: str
) -> None:
    """末尾の ``.`` と空白は、短いフォルダ名でも除去される (tech.md)."""
    entry = make_entry(author="Alice Smith", year="2020")
    # 代入 (``__setitem__``) は値を加工しないので、末尾の空白も残る。
    entry["title"] = title

    assert entry.pdf_dir_name == "2020 - Alice Smith - Study of X"


def test_pdf_dir_name_keeps_periods_inside_the_name(
    make_entry: Callable[..., Entry],
) -> None:
    """末尾以外の ``.`` は除去されない."""
    entry = make_entry(
        author="Alice Smith", title="Vol. 2. Part A", year="2020"
    )

    assert entry.pdf_dir_name == "2020 - Alice Smith - Vol. 2. Part A"


# --- 200 文字を超える場合の切り詰めと末尾の扱い (要件 2.4) ------------------


def _separator_run_title() -> str:
    """切り詰め位置が ``.`` と空白の連続に当たるタイトル (300 文字)."""
    return "x" * 150 + ". " * 40 + "y" * 70


@pytest.mark.parametrize(
    "title",
    [
        pytest.param("x" * 300, id="plain"),
        pytest.param(("word " * 60).strip(), id="words"),
        pytest.param(_separator_run_title(), id="separator-run"),
        pytest.param("あ" * 300, id="non-ascii"),
    ],
)
def test_long_title_is_truncated_within_200_chars(
    make_entry: Callable[..., Entry], title: str
) -> None:
    """フォルダ名は 200 文字以内に切り詰められ、末尾が ``.``・空白でない.

    年と第一著者は残り、タイトルは先頭側が残る。
    """
    entry = make_entry(author="Alice Smith", title=title, year="2020")

    name = entry.pdf_dir_name

    assert_valid_dir_name(name)
    assert name.startswith(PREFIX)
    kept = name[len(PREFIX) :]
    assert kept != ""
    assert title.startswith(kept)


def test_long_title_is_cut_so_that_the_name_fits_200_chars(
    make_entry: Callable[..., Entry],
) -> None:
    """切り詰め位置は後方互換のために固定されている.

    既存ユーザーの PDF フォルダ名と食い違うと、PDF が見つからなくなる。
    全体で 200 文字 = 接頭辞 21 文字 + タイトル 176 文字 + 省略記号 3 文字
    で、省略記号の ``...`` は末尾の ``.`` として取り除かれる。
    """
    entry = make_entry(author="Alice Smith", title="x" * 300, year="2020")

    assert entry.pdf_dir_name == PREFIX + "x" * 176


def test_name_of_exactly_200_chars_is_not_truncated(
    make_entry: Callable[..., Entry],
) -> None:
    """ちょうど 200 文字のフォルダ名は切り詰められない."""
    title = "x" * (MAX_DIR_NAME_LENGTH - len(PREFIX))
    entry = make_entry(author="Alice Smith", title=title, year="2020")

    assert entry.pdf_dir_name == PREFIX + title
    assert len(entry.pdf_dir_name) == MAX_DIR_NAME_LENGTH


def test_name_of_201_chars_is_truncated(
    make_entry: Callable[..., Entry],
) -> None:
    """201 文字になるフォルダ名は 200 文字以内に切り詰められる."""
    title = "x" * (MAX_DIR_NAME_LENGTH - len(PREFIX) + 1)
    entry = make_entry(author="Alice Smith", title=title, year="2020")

    name = entry.pdf_dir_name

    assert_valid_dir_name(name)
    assert name.startswith(PREFIX)
    assert name != PREFIX + title


@pytest.mark.parametrize(
    "author_length",
    [185, 186, 187, 188, 189, 190, 191, 250],
    ids=lambda n: f"author-{n}",
)
def test_very_long_author_still_fits_in_200_chars(
    make_entry: Callable[..., Entry], author_length: int
) -> None:
    """接頭辞だけで 200 文字前後になる長い著者名でも 200 文字以内に収まる.

    接頭辞 (``2020 - <著者> - ``) が 195〜260 文字になる範囲を検証する。
    著者名が 188・189 文字 (接頭辞 198・199 文字) は、修正前の規則では
    200 文字を超えていた KB7 の範囲 (known-bug-fixes 要件 2.1)。
    """
    entry = make_entry(
        author="A" * author_length, title="x" * 300, year="2020"
    )

    name = entry.pdf_dir_name

    assert_valid_dir_name(name)
    assert name.startswith("2020")


# --- ファイル名の置換規則 sanitize_filename (要件 2.5) ----------------------


@pytest.mark.parametrize("char", list("\\/:*?<>|"))
def test_windows_replaces_each_reserved_char_with_underscore(
    set_os: Callable[[str], None], char: str
) -> None:
    """Windows では ``\\ / : * ? < > |`` のそれぞれが ``_`` に置換される."""
    set_os("Windows")

    assert sanitize_filename(f"a{char}b") == "a_b"


def test_windows_replaces_every_reserved_char_in_one_name(
    set_os: Callable[[str], None],
) -> None:
    """Windows では、1 つの名前に含まれる禁止文字がすべて置換される."""
    set_os("Windows")

    assert sanitize_filename("a\\b/c:d*e?f<g>h|i") == "a_b_c_d_e_f_g_h_i"
    assert sanitize_filename("C:\\Windows\\System32") == "C__Windows_System32"


def test_windows_keeps_ordinary_chars(set_os: Callable[[str], None]) -> None:
    """Windows でも、禁止文字以外はそのまま残る."""
    set_os("Windows")
    name = "2020 - Alice Smith - 深層学習 (a, b) [c] #1 & 'd'.pdf"

    assert sanitize_filename(name) == name


@pytest.mark.parametrize("os_name", ["Linux", "Darwin"])
def test_non_windows_replaces_only_slash(
    set_os: Callable[[str], None], os_name: str
) -> None:
    """Windows 以外の OS では ``/`` だけが ``_`` に置換される."""
    set_os(os_name)

    assert sanitize_filename("a/b") == "a_b"
    assert sanitize_filename("a/b/c/") == "a_b_c_"


@pytest.mark.parametrize("os_name", ["Linux", "Darwin"])
@pytest.mark.parametrize("char", list('\\:*?<>|"'))
def test_non_windows_keeps_chars_other_than_slash(
    set_os: Callable[[str], None], os_name: str, char: str
) -> None:
    """Windows 以外の OS では、``/`` 以外の文字は置換されない."""
    set_os(os_name)

    assert sanitize_filename(f"a{char}b") == f"a{char}b"


# --- PDF 一覧の補助 (要件 2.6〜2.9 のテストで共有) --------------------------

# ``make_entry()`` の既定値から作られるフォルダ名. 実装の ``pdf_dir_name`` と
# 食い違うと、以降のテストがすべて失敗する (``test_get_pdf_dir_*`` で確認).
ENTRY_DIR_NAME = "2020 - Alice Smith - A Study of Testing"
# 旧形式の単一 PDF (``<base>/<旧ファイル名>.pdf``) のファイル名.
# 既定のエントリでは ``<pdf_dir_name>.pdf`` と一致する.
LEGACY_SINGLE_NAME = f"{ENTRY_DIR_NAME}.pdf"

# 旧形式の移行は非推奨の後方互換コード. 警告の確認が目的でないテストでは無視する.
ignore_deprecation = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def pdf_content(label: str) -> bytes:
    """``label`` ごとに異なる PDF の中身を作る (ファイルの取り違えを検出する)."""
    return PDF_BYTES + f"%{label}\n".encode("ascii")


def write_pdfs(directory: Path, contents: Mapping[str, bytes]) -> None:
    """``directory`` に、ファイル名と中身の対応どおりに PDF を書き出す."""
    for name, content in contents.items():
        write_pdf(directory / name, content)


def snapshot(root: Path) -> Optional[dict[str, Optional[bytes]]]:
    """``root`` 配下のフォルダとファイルの状態を取る.

    Returns
    -------
    Optional[dict[str, Optional[bytes]]]
        ``root`` からの相対パス (``/`` 区切り) → ファイルの中身
        (フォルダは ``None``)。``root`` が存在しなければ ``None``。
        前後で比べることで、作成・削除・改名・書き換えを検出する。
    """
    if not root.exists():
        return None
    return {
        path.relative_to(root).as_posix(): (
            path.read_bytes() if path.is_file() else None
        )
        for path in sorted(root.rglob("*"))
    }


def list_pdfs_migrating(
    entry: Entry, base_dir: Optional[Path] = None
) -> list[Path]:
    """PDF 一覧を取得する. 旧形式の移行の非推奨警告が出ることも確かめる."""
    with pytest.warns(DeprecationWarning):
        return entry.get_pdf_files(base_dir)


@pytest.fixture
def entry(make_entry: Callable[..., Entry]) -> Entry:
    """既定値のエントリ. PDF フォルダ名は ``ENTRY_DIR_NAME``."""
    return make_entry()


@pytest.fixture
def pdf_dir(data_paths: DataPaths) -> Path:
    """``entry`` の PDF フォルダ (既定の PDF ディレクトリの配下. 未作成)."""
    return data_paths.pdf / ENTRY_DIR_NAME


@pytest.fixture
def alt_pdf_dir(tmp_path: Path) -> Path:
    """既定とは別の、保存先の基準ディレクトリ (未作成)."""
    return tmp_path / "alternative-pdf"


# --- PDF 一覧 (要件 2.6) ------------------------------------------------------


def test_get_pdf_dir_is_pdf_dir_name_under_default_pdf_dir(
    entry: Entry, data_paths: DataPaths, pdf_dir: Path
) -> None:
    """PDF フォルダは、既定の PDF ディレクトリの下の ``pdf_dir_name``."""
    assert entry.pdf_dir_name == ENTRY_DIR_NAME
    assert entry.get_pdf_dir() == pdf_dir
    assert pdf_dir.parent == data_paths.pdf


@ignore_deprecation
@pytest.mark.parametrize(
    "created_order",
    [(3, 2, 1, 0), (2, 0, 10, 1, 9), (7, 0, 3)],
    ids=["descending", "shuffled-across-9-10", "with-gaps"],
)
def test_get_pdf_files_returns_pdfs_in_number_order(
    entry: Entry, pdf_dir: Path, created_order: tuple
) -> None:
    """PDF 一覧は ``NN.pdf`` の番号順で、作成した順には依存しない."""
    for number in created_order:
        write_pdf(pdf_dir / f"{number:02d}.pdf", pdf_content(f"{number:02d}"))

    files = entry.get_pdf_files()

    assert files == [pdf_dir / f"{n:02d}.pdf" for n in sorted(created_order)]


@ignore_deprecation
@pytest.mark.parametrize(
    "other_files",
    [None, [], ["memo.txt", "scan.png"]],
    ids=["no-directory", "empty-directory", "non-pdf-files-only"],
)
def test_pdf_listing_is_empty_without_pdf(
    entry: Entry, pdf_dir: Path, other_files: Optional[list]
) -> None:
    """PDF がなければ一覧は空で、有無は偽・件数は 0 になる.

    フォルダがなくても、取得でフォルダは作られず、他のファイルも変わらない。
    """
    if other_files is not None:
        pdf_dir.mkdir(parents=True)
        for name in other_files:
            (pdf_dir / name).write_text("not a pdf", encoding="utf-8")
    before = snapshot(pdf_dir)

    assert entry.get_pdf_files() == []
    assert entry.has_pdf() is False
    assert entry.get_pdf_count() == 0
    assert snapshot(pdf_dir) == before


@ignore_deprecation
@pytest.mark.parametrize("count", [1, 2, 5])
def test_has_pdf_and_count_match_the_listing(
    entry: Entry, pdf_dir: Path, count: int
) -> None:
    """PDF の有無と件数は、一覧と一致する."""
    for number in range(count):
        write_pdf(pdf_dir / f"{number:02d}.pdf", pdf_content(f"{number:02d}"))

    files = entry.get_pdf_files()

    assert len(files) == count
    assert entry.has_pdf() is True
    assert entry.get_pdf_count() == len(files)


@ignore_deprecation
def test_get_pdf_files_ignores_non_pdf_files_in_the_folder(
    entry: Entry, pdf_dir: Path
) -> None:
    """PDF 以外のファイルは、一覧にも件数にも含まれず、変更もされない."""
    write_pdf(pdf_dir / "00.pdf", pdf_content("00"))
    (pdf_dir / "memo.txt").write_text("memo", encoding="utf-8")

    assert entry.get_pdf_files() == [pdf_dir / "00.pdf"]
    assert entry.get_pdf_count() == 1
    assert (pdf_dir / "memo.txt").read_text(encoding="utf-8") == "memo"


@ignore_deprecation
def test_get_pdf_files_lists_only_pdfs_of_the_entry(
    make_entry: Callable[..., Entry],
) -> None:
    """別のエントリの PDF フォルダにある PDF は、一覧に含まれない."""
    mine = make_entry()
    other = make_entry(title="Another Study")
    write_pdf(mine.get_pdf_dir() / "00.pdf", pdf_content("mine 00"))
    write_pdfs(
        other.get_pdf_dir(),
        {"00.pdf": pdf_content("other 00"), "01.pdf": pdf_content("other 01")},
    )

    assert mine.get_pdf_files() == [mine.get_pdf_dir() / "00.pdf"]
    assert mine.get_pdf_count() == 1
    assert other.get_pdf_count() == 2


@ignore_deprecation
def test_sample_data_entries_report_their_pdfs(
    sample_data: SampleData, data_paths: DataPaths
) -> None:
    """サンプルデータでは、PDF のあるエントリだけが番号順に 2 件を返す.

    日本語のタイトルのフォルダでも同じに動作し、孤立 PDF はどのエントリの
    一覧にも含まれない。
    """
    papers = sample_data.paper_list
    with_pdf = papers[sample_data.with_pdf_key]
    without_pdf = papers[sample_data.without_pdf_key]
    folder = data_paths.pdf / with_pdf.pdf_dir_name

    assert with_pdf.get_pdf_files() == [folder / "00.pdf", folder / "01.pdf"]
    assert with_pdf.has_pdf() is True
    assert with_pdf.get_pdf_count() == 2
    assert without_pdf.get_pdf_files() == []
    assert without_pdf.has_pdf() is False
    assert without_pdf.get_pdf_count() == 0
    assert sample_data.orphan_pdf.parent.name not in {
        with_pdf.pdf_dir_name,
        without_pdf.pdf_dir_name,
    }


# --- 旧形式の単一 PDF の移行 (要件 2.7) ---------------------------------------


@pytest.mark.parametrize(
    ("title", "legacy_name"),
    [
        (
            "A Study of Testing",
            "2020 - Alice Smith - A Study of Testing.pdf",
        ),
        (
            "深層学習による画像認識の研究",
            "2020 - Alice Smith - 深層学習による画像認識の研究.pdf",
        ),
        ("A/B Testing", "2020 - Alice Smith - A_B Testing.pdf"),
    ],
    ids=["ascii", "non-ascii", "slash-replaced"],
)
def test_legacy_single_pdf_is_migrated_to_00_pdf(
    make_entry: Callable[..., Entry],
    data_paths: DataPaths,
    title: str,
    legacy_name: str,
) -> None:
    """``<base>/<旧ファイル名>.pdf`` は、一覧の取得時に ``00.pdf`` へ移る.

    元のファイルは残らず、中身はそのまま保たれる。
    """
    entry = make_entry(title=title)
    legacy = write_pdf(data_paths.pdf / legacy_name, pdf_content("legacy"))
    folder = entry.pdf_dir_name

    files = list_pdfs_migrating(entry)

    assert files == [data_paths.pdf / folder / "00.pdf"]
    assert not legacy.exists()
    assert snapshot(data_paths.pdf) == {
        folder: None,
        f"{folder}/00.pdf": pdf_content("legacy"),
    }


@ignore_deprecation
def test_has_pdf_and_count_agree_after_legacy_single_pdf_migration(
    entry: Entry, data_paths: DataPaths, pdf_dir: Path
) -> None:
    """旧形式の単一 PDF は ``has_pdf`` からも見え、移行後の一覧と一致する."""
    write_pdf(data_paths.pdf / LEGACY_SINGLE_NAME, pdf_content("legacy"))

    assert entry.has_pdf() is True
    assert entry.get_pdf_count() == 1
    assert entry.get_pdf_files() == [pdf_dir / "00.pdf"]
    assert not (data_paths.pdf / LEGACY_SINGLE_NAME).exists()


@ignore_deprecation
def test_legacy_single_pdf_of_another_entry_is_left_alone(
    entry: Entry, data_paths: DataPaths
) -> None:
    """別のエントリの旧形式の単一 PDF は、移行も削除もされない."""
    other_name = "2020 - Alice Smith - Another Study.pdf"
    write_pdf(data_paths.pdf / other_name, pdf_content("other"))
    before = snapshot(data_paths.pdf)

    assert entry.get_pdf_files() == []
    assert snapshot(data_paths.pdf) == before


# --- 旧命名規則の PDF の改名 (要件 2.8) ---------------------------------------


@pytest.mark.parametrize(
    "legacy_name",
    [f"{ENTRY_DIR_NAME}.pdf", f"{ENTRY_DIR_NAME}_01.pdf"],
    ids=["plain", "suffixed"],
)
def test_legacy_named_pdf_in_folder_is_renamed_to_00_pdf(
    entry: Entry, pdf_dir: Path, legacy_name: str
) -> None:
    """フォルダ内の ``<pdf_dir_name>.pdf``・``<pdf_dir_name>_01.pdf`` は
    ``00.pdf`` へ改名され、中身は保たれる."""
    write_pdf(pdf_dir / legacy_name, pdf_content("legacy"))

    files = list_pdfs_migrating(entry)

    assert files == [pdf_dir / "00.pdf"]
    assert snapshot(pdf_dir) == {"00.pdf": pdf_content("legacy")}


@pytest.mark.parametrize(
    ("existing_numbers", "legacy_number"),
    [
        ((0,), 1),
        ((1,), 0),
        ((0, 2), 1),
        ((0, 1, 2), 3),
        ((0, 1, 2, 3, 4, 5, 6, 7, 8, 9), 10),
    ],
    ids=["00", "01", "00-and-02", "00-01-02", "00-to-09"],
)
def test_legacy_named_pdf_never_overwrites_existing_numbered_pdf(
    entry: Entry,
    pdf_dir: Path,
    existing_numbers: tuple,
    legacy_number: int,
) -> None:
    """旧命名規則の PDF は、既存の ``NN.pdf`` と衝突しない最小の番号になる.

    既存の ``NN.pdf`` は、中身も名前も変わらない。
    """
    existing = {
        f"{n:02d}.pdf": pdf_content(f"existing {n:02d}")
        for n in existing_numbers
    }
    legacy = pdf_content("legacy")
    write_pdfs(pdf_dir, {**existing, f"{ENTRY_DIR_NAME}.pdf": legacy})

    files = list_pdfs_migrating(entry)

    expected = {**existing, f"{legacy_number:02d}.pdf": legacy}
    assert snapshot(pdf_dir) == expected
    assert files == [pdf_dir / name for name in sorted(expected)]


@ignore_deprecation
def test_several_legacy_named_pdfs_are_all_kept_without_overwriting(
    entry: Entry, pdf_dir: Path
) -> None:
    """旧命名規則の PDF が複数あっても、すべて別の番号で残る.

    どの旧ファイルがどの番号になるかは規定されないため、中身の集合で
    比べる。移行後にもう一度一覧を取得しても、何も変わらない。
    """
    existing_00 = pdf_content("existing 00")
    existing_01 = pdf_content("existing 01")
    legacy_a = pdf_content("legacy a")
    legacy_b = pdf_content("legacy b")
    write_pdfs(
        pdf_dir,
        {
            "00.pdf": existing_00,
            "01.pdf": existing_01,
            f"{ENTRY_DIR_NAME}.pdf": legacy_a,
            f"{ENTRY_DIR_NAME}_01.pdf": legacy_b,
        },
    )

    files = list_pdfs_migrating(entry)

    assert [path.name for path in files] == [
        "00.pdf",
        "01.pdf",
        "02.pdf",
        "03.pdf",
    ]
    migrated = snapshot(pdf_dir)
    assert migrated is not None
    assert migrated["00.pdf"] == existing_00
    assert migrated["01.pdf"] == existing_01
    assert {migrated["02.pdf"], migrated["03.pdf"]} == {legacy_a, legacy_b}
    assert entry.get_pdf_files() == files
    assert snapshot(pdf_dir) == migrated


# --- 保存先の基準ディレクトリ (要件 2.9) --------------------------------------


def test_get_pdf_dir_with_base_dir_is_under_the_given_directory(
    entry: Entry, alt_pdf_dir: Path
) -> None:
    """基準ディレクトリを指定すると、PDF フォルダはその直下になる."""
    assert entry.get_pdf_dir(alt_pdf_dir) == alt_pdf_dir / ENTRY_DIR_NAME


@ignore_deprecation
@pytest.mark.parametrize(
    "default_pdf_count",
    [0, 2],
    ids=["default-dir-absent", "default-dir-has-pdfs"],
)
def test_listing_with_base_dir_leaves_default_pdf_dir_unchanged(
    entry: Entry,
    data_paths: DataPaths,
    pdf_dir: Path,
    alt_pdf_dir: Path,
    default_pdf_count: int,
) -> None:
    """基準ディレクトリを指定した一覧・有無・件数は、指定先だけを見る.

    既定の PDF ディレクトリは、あっても (件数が異なる) なくても、
    ファイルもフォルダも増減・変更されない。
    """
    for number in range(default_pdf_count):
        write_pdf(
            pdf_dir / f"{number:02d}.pdf", pdf_content(f"default {number:02d}")
        )
    alt_dir = alt_pdf_dir / ENTRY_DIR_NAME
    write_pdfs(
        alt_dir,
        {f"{n:02d}.pdf": pdf_content(f"alt {n:02d}") for n in range(3)},
    )
    default_before = snapshot(data_paths.pdf)

    assert entry.get_pdf_files(alt_pdf_dir) == [
        alt_dir / "00.pdf",
        alt_dir / "01.pdf",
        alt_dir / "02.pdf",
    ]
    assert entry.has_pdf(alt_pdf_dir) is True
    assert entry.get_pdf_count(alt_pdf_dir) == 3
    assert snapshot(data_paths.pdf) == default_before


@ignore_deprecation
def test_base_dir_without_pdfs_does_not_look_into_default_pdf_dir(
    entry: Entry, data_paths: DataPaths, pdf_dir: Path, alt_pdf_dir: Path
) -> None:
    """基準ディレクトリに PDF がなければ、既定の PDF があっても空になる.

    指定先にも既定の PDF ディレクトリにも、何も作られない。
    """
    write_pdf(pdf_dir / "00.pdf", pdf_content("default 00"))
    default_before = snapshot(data_paths.pdf)

    assert entry.get_pdf_files(alt_pdf_dir) == []
    assert entry.has_pdf(alt_pdf_dir) is False
    assert entry.get_pdf_count(alt_pdf_dir) == 0
    assert snapshot(data_paths.pdf) == default_before
    assert snapshot(alt_pdf_dir) is None


def test_legacy_single_pdf_is_migrated_inside_base_dir_only(
    entry: Entry, data_paths: DataPaths, alt_pdf_dir: Path
) -> None:
    """旧形式の単一 PDF の移行は、指定した基準ディレクトリの中だけで行われる.

    既定の PDF ディレクトリに同名の旧形式の PDF があっても、触られない。
    """
    write_pdf(alt_pdf_dir / LEGACY_SINGLE_NAME, pdf_content("alt legacy"))
    write_pdf(
        data_paths.pdf / LEGACY_SINGLE_NAME, pdf_content("default legacy")
    )
    default_before = snapshot(data_paths.pdf)

    files = list_pdfs_migrating(entry, alt_pdf_dir)

    assert files == [alt_pdf_dir / ENTRY_DIR_NAME / "00.pdf"]
    assert snapshot(alt_pdf_dir) == {
        ENTRY_DIR_NAME: None,
        f"{ENTRY_DIR_NAME}/00.pdf": pdf_content("alt legacy"),
    }
    assert snapshot(data_paths.pdf) == default_before


def test_legacy_named_pdf_is_renamed_inside_base_dir_only(
    entry: Entry, data_paths: DataPaths, pdf_dir: Path, alt_pdf_dir: Path
) -> None:
    """旧命名規則の PDF の改名は、指定した基準ディレクトリの中だけで行われる.

    既定の PDF フォルダに旧命名規則の PDF があっても、改名されない。
    """
    legacy_name = f"{ENTRY_DIR_NAME}.pdf"
    alt_dir = alt_pdf_dir / ENTRY_DIR_NAME
    write_pdfs(
        alt_dir,
        {"00.pdf": pdf_content("alt 00"), legacy_name: pdf_content("alt old")},
    )
    write_pdf(pdf_dir / legacy_name, pdf_content("default old"))
    default_before = snapshot(data_paths.pdf)

    files = list_pdfs_migrating(entry, alt_pdf_dir)

    assert files == [alt_dir / "00.pdf", alt_dir / "01.pdf"]
    assert snapshot(alt_dir) == {
        "00.pdf": pdf_content("alt 00"),
        "01.pdf": pdf_content("alt old"),
    }
    assert snapshot(data_paths.pdf) == default_before


# --- 修正した既知バグ KB2a, KB2b, KB5, KB7 (known-bug-fixes 要件 2.1〜2.5) ---


def test_pdf_dir_name_uses_unknown_for_missing_author(
    make_partial_entry: Callable[..., Entry],
) -> None:
    """著者が欠落したエントリのフォルダ名は ``Unknown`` で作られる (KB2a)."""
    entry = make_partial_entry(drop=("author",), title="T", year="2020")

    assert entry.pdf_dir_name == "2020 - Unknown - T"


@pytest.mark.parametrize(
    "author", ["", "   ", "\t\n"], ids=["empty", "spaces", "tab-newline"]
)
def test_pdf_dir_name_uses_unknown_for_empty_author(
    make_entry: Callable[..., Entry], author: str
) -> None:
    """著者が空 (空白だけを含む) のエントリも ``Unknown`` で作られる (KB2a).

    作成時に値の前後の空白が除去されるので、どれも空の著者になる。
    """
    entry = make_entry(author=author, title="T", year="2020")

    assert entry.pdf_dir_name == "2020 - Unknown - T"


def test_windows_replaces_double_quote_with_underscore(
    set_os: Callable[[str], None],
) -> None:
    """Windows では、禁止文字の ``"`` も ``_`` に置換される (KB5)."""
    set_os("Windows")

    assert sanitize_filename('a"b') == "a_b"


@pytest.mark.parametrize(
    ("os_name", "expected"),
    [
        ("Linux", '2020 - Alice Smith - The "Best" Paper'),
        ("Darwin", '2020 - Alice Smith - The "Best" Paper'),
        ("Windows", "2020 - Alice Smith - The _Best_ Paper"),
    ],
)
def test_pdf_dir_name_replaces_double_quote_only_on_windows(
    make_entry: Callable[..., Entry],
    set_os: Callable[[str], None],
    os_name: str,
    expected: str,
) -> None:
    """タイトルの ``"`` は、Windows でだけフォルダ名で ``_`` になる (KB5).

    Windows 以外の置換規則は変わらない。
    """
    set_os(os_name)
    entry = make_entry(
        author="Alice Smith", title='The "Best" Paper', year="2020"
    )

    assert entry.pdf_dir_name == expected


def test_name_stays_within_200_chars_when_prefix_leaves_almost_no_room(
    make_entry: Callable[..., Entry],
) -> None:
    """接頭辞がほぼ 200 文字を占める場合も、フォルダ名は 200 文字以内になる.

    接頭辞 ``2020 - <著者> - `` が 198・199 文字のとき (KB7)、名前は
    ``<year> - <first_author>`` に切り替わる。
    """
    for author_length in (188, 189):
        entry = make_entry(
            author="A" * author_length, title="x" * 300, year="2020"
        )

        assert_valid_dir_name(entry.pdf_dir_name)
        assert entry.pdf_dir_name == "2020 - " + "A" * author_length


def test_kb7_fallback_name_has_no_trailing_period(
    make_entry: Callable[..., Entry],
) -> None:
    """KB7 の代替名 ``<year> - <first_author>`` も末尾の ``.`` を除く.

    著者名 188 文字 (末尾が ``.``) で接頭辞が 198 文字になる場合。
    """
    entry = make_entry(author="A" * 187 + ".", title="x" * 300, year="2020")

    assert_valid_dir_name(entry.pdf_dir_name)
    assert entry.pdf_dir_name == "2020 - " + "A" * 187


@ignore_deprecation
def test_get_pdf_files_is_empty_for_entry_without_title(
    make_partial_entry: Callable[..., Entry],
) -> None:
    """タイトルが欠落し、PDF フォルダもないエントリの PDF 一覧は空になる.

    PDF フォルダがないときは、旧形式の単一 PDF を探す (KB2b)。有無と件数も
    例外にならない。
    """
    entry = make_partial_entry(drop=("title",))

    assert entry.get_pdf_files() == []
    assert entry.has_pdf() is False
    assert entry.get_pdf_count() == 0


@ignore_deprecation
def test_pdf_listing_of_entry_without_title_reports_its_pdfs(
    make_partial_entry: Callable[..., Entry], data_paths: DataPaths
) -> None:
    """タイトルが欠落したエントリも、PDF フォルダの PDF の有無と件数を返す.

    フォルダ名はタイトルの既定値 ``Unknown`` で作られる (KB2b)。
    """
    entry = make_partial_entry(drop=("title",))
    folder = data_paths.pdf / "2020 - Alice Smith - Unknown"
    write_pdfs(
        folder, {"00.pdf": pdf_content("00"), "01.pdf": pdf_content("01")}
    )

    assert entry.get_pdf_files() == [folder / "00.pdf", folder / "01.pdf"]
    assert entry.has_pdf() is True
    assert entry.get_pdf_count() == 2


def test_pdf_filename_keeps_legacy_name_and_allows_missing_title(
    make_partial_entry: Callable[..., Entry],
) -> None:
    """非推奨の ``pdf_filename`` は、タイトルがなくても例外にならない (KB2b).

    タイトルがあるときの名前は、旧形式の単一 PDF のファイル名との互換の
    ために変わらない。
    """
    with pytest.warns(DeprecationWarning):
        assert make_partial_entry().pdf_filename == LEGACY_SINGLE_NAME
    with pytest.warns(DeprecationWarning):
        untitled = make_partial_entry(drop=("title",)).pdf_filename

    assert untitled.startswith("2020 - Alice Smith - ")
    assert untitled.endswith(".pdf")


# --- 修正前の規則との一致 (known-bug-fixes 要件 2.1, 2.2, 2.6) ---------------

# 年 ``2020`` のとき、接頭辞 ``2020 - <著者> - `` が 197〜200 文字になる
# 著者名の長さ.
BOUNDARY_AUTHOR_LENGTHS = range(187, 191)
BOUNDARY_TITLE_LENGTHS = range(0, 301)
# 境界のテストのタイトルを作る文字列の単位. 切り詰めた位置の ``.``・空白の
# 除去と、OS ごとの置換 (``:`` は Windows でだけ置換される) が変わる.
BOUNDARY_TITLE_UNITS = {
    "plain": "x",
    "separators": "ab. ",
    "colon": "a:b",
    "non-ascii": "あ",
}


def pre_fix_dir_name(year: str, first_author: str, title: str) -> str:
    """修正前 (KB7 の修正前) の ``pdf_dir_name`` の名前を作る.

    known-bug-fixes spec の修正前 (commit 88393fc の時点) の
    ``Entry.pdf_dir_name`` の処理を、フィールドを取り出した後の部分について
    そのまま写したもの。プロダクションコードが後方互換のために残した修正前の
    規則とは独立した基準として使い、どちらかが変わると境界のテストが失敗する
    ようにする。置換規則は ``sanitize_filename`` (差し替えた OS の規則) を
    使う。

    Returns
    -------
    str
        修正前の名前。KB7 の範囲では 200 文字を超える。
    """
    dir_name = sanitize_filename(f"{year} - {first_author} - {title}")
    if len(dir_name) > MAX_DIR_NAME_LENGTH:
        prefix = f"{year} - {first_author} - "
        max_title_length = MAX_DIR_NAME_LENGTH - len(prefix)
        if max_title_length > 0:
            truncated_title = title[: max_title_length - 3] + "..."
            dir_name = sanitize_filename(prefix + truncated_title)
        else:
            dir_name = sanitize_filename(f"{year} - {first_author}")
            if len(dir_name) > MAX_DIR_NAME_LENGTH:
                dir_name = sanitize_filename(year)
    return dir_name.rstrip(". ")


def boundary_entries(
    make_entry: Callable[..., Entry], unit: str
) -> Iterable[tuple[int, int, Entry]]:
    """境界の組み合わせ (接頭辞 197〜200 文字 × タイトル 0〜300 文字) を作る.

    Returns
    -------
    Iterable[tuple[int, int, Entry]]
        ``(著者名の長さ, タイトルの長さ, エントリ)``。タイトルは ``unit`` を
        繰り返して指定の長さに切ったもの (作成時に前後の空白は除去される)。
    """
    for author_length, title_length in product(
        BOUNDARY_AUTHOR_LENGTHS, BOUNDARY_TITLE_LENGTHS
    ):
        title = (unit * title_length)[:title_length]
        entry = make_entry(
            author="A" * author_length, title=title, year="2020"
        )
        yield author_length, title_length, entry


boundary_os = pytest.mark.parametrize("os_name", ["Linux", "Windows"])
boundary_units = pytest.mark.parametrize(
    "unit",
    list(BOUNDARY_TITLE_UNITS.values()),
    ids=list(BOUNDARY_TITLE_UNITS),
)


@boundary_os
@boundary_units
def test_pre_fix_rule_is_kept_for_backward_compatibility(
    make_entry: Callable[..., Entry],
    set_os: Callable[[str], None],
    os_name: str,
    unit: str,
) -> None:
    """後方互換のために残した修正前の規則は、修正前の名前を返す.

    KB7 の範囲 (200 文字を超える名前) も含めて、修正前と同じ名前になる
    (修正前の名前のフォルダの移行と孤立 PDF の判定に使う)。
    """
    set_os(os_name)

    for author_length, title_length, entry in boundary_entries(
        make_entry, unit
    ):
        expected = pre_fix_dir_name(
            entry["year"], entry["author"], entry["title"]
        )
        assert entry._pdf_dir_name_before_kb7_fix() == expected, (
            author_length,
            title_length,
        )


@boundary_os
@boundary_units
def test_pdf_dir_name_differs_from_pre_fix_rule_only_beyond_200_chars(
    make_entry: Callable[..., Entry],
    set_os: Callable[[str], None],
    os_name: str,
    unit: str,
) -> None:
    """修正前に 200 文字以内だった名前は、修正後も完全に同一になる.

    修正前の名前が 200 文字を超えていた入力 (KB7 の範囲) だけが、
    ``<year> - <first_author>`` (200 文字以内で末尾が ``.``・空白でない) に
    変わる。KB7 の範囲は、接頭辞が 198・199 文字 (著者名 188・189 文字) の
    ときだけで、``.``・空白を含まないタイトルでは 4 文字以上のとき。
    """
    set_os(os_name)
    kb7_window = set()

    for author_length, title_length, entry in boundary_entries(
        make_entry, unit
    ):
        before = pre_fix_dir_name(
            entry["year"], entry["author"], entry["title"]
        )
        name = entry.pdf_dir_name
        case = (author_length, title_length)
        assert before != "", case  # 前提: 空の名前は別のテストで扱う
        if len(before) <= MAX_DIR_NAME_LENGTH:
            assert name == before, case
        else:
            kb7_window.add(case)
            assert_valid_dir_name(name)
            assert name == "2020 - " + "A" * author_length, case

    assert kb7_window
    assert {author_length for author_length, _ in kb7_window} <= {188, 189}
    if unit == BOUNDARY_TITLE_UNITS["plain"]:
        assert kb7_window == set(product((188, 189), range(4, 301)))


@pytest.mark.parametrize(
    ("year", "expected"),
    [
        pytest.param("1" * 200, "1" * 200, id="200-unchanged"),
        pytest.param("1" * 201, "1" * 200, id="201"),
        pytest.param("1" * 300, "1" * 200, id="300"),
        pytest.param(
            "1" * 199 + ". " + "9" * 50, "1" * 199, id="cut-at-separator"
        ),
    ],
)
@pytest.mark.parametrize(
    "dropped", [(), ("author",)], ids=["with-author", "no-author"]
)
def test_year_longer_than_200_chars_is_cut_to_200_chars(
    make_partial_entry: Callable[..., Entry],
    year: str,
    expected: str,
    dropped: tuple,
) -> None:
    """年が 200 文字を超えても、フォルダ名は 200 文字以内になる (KB7).

    ``<year> - <first_author>`` も 200 文字を超えるので、年を 200 文字に
    切り詰め、末尾の ``.``・空白を除く。年がちょうど 200 文字の名前は、
    修正前と同じ (年だけ) になる。
    """
    entry = make_partial_entry(
        drop=dropped, author="Alice Smith", title="T", year=year
    )

    name = entry.pdf_dir_name

    assert_valid_dir_name(name)
    assert name == expected


@pytest.mark.parametrize(
    ("dropped", "fields"),
    [
        (("author",), {}),
        ((), {"author": ""}),
    ],
    ids=["no-author", "empty-author"],
)
def test_pre_fix_rule_has_no_name_without_author(
    make_partial_entry: Callable[..., Entry], dropped: tuple, fields: dict
) -> None:
    """修正前の規則は、著者がない・空のエントリでは名前を返さない.

    修正前の規則はこの場合に例外になり (KB2a)、その名前のフォルダは
    作られなかった。後方互換の処理が扱いやすいよう、例外ではなく ``None``
    を返す。
    """
    entry = make_partial_entry(drop=dropped, **fields)

    assert entry._pdf_dir_name_before_kb7_fix() is None


def _set_fields_unstripped(entry: Entry, **fields: str) -> Entry:
    """値の前後の空白を残したまま、フィールドを代入する.

    作成時と違い、代入 (``__setitem__``) は値を加工しない。
    """
    for name, value in fields.items():
        entry[name] = value
    return entry


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param(
            {"year": "", "author": "A" * 198, "title": "T"},
            id="empty-year-author-198",
        ),
        pytest.param(
            {"year": "", "author": "A" * 250, "title": "x" * 300},
            id="empty-year-author-250",
        ),
        pytest.param(
            {"year": ".", "author": "A" * 250, "title": "T"},
            id="dot-year-author-250",
        ),
        pytest.param(
            {"year": " . ", "author": ". " * 120, "title": " .. "},
            id="dots-and-spaces-only",
        ),
        pytest.param(
            {"year": "." * 300, "author": "A", "title": "T"},
            id="dot-year-300",
        ),
        pytest.param(
            {"year": "." * 200 + "1" * 50, "author": "A", "title": "T"},
            id="year-cut-to-dots",
        ),
    ],
)
@pytest.mark.parametrize("os_name", ["Linux", "Windows"])
def test_pdf_dir_name_is_unknown_when_the_name_would_be_empty(
    make_entry: Callable[..., Entry],
    set_os: Callable[[str], None],
    fields: dict,
    os_name: str,
) -> None:
    """名前が空になる入力では、フォルダ名は ``Unknown`` になる.

    空の名前は PDF ディレクトリそのものを指し、フォルダ名になりえない。
    修正前の規則でも空になっていた入力 (年が空や ``.``・空白だけで、
    ``<year> - <first_author>`` が 200 文字を超える) と、年を 200 文字に
    切り詰めると ``.`` だけが残る入力 (修正前は 200 文字超) を検証する。
    修正前の規則の名前が空のときは、後方互換の処理に名前を返さない
    (``None``)。空でなければ、修正前の名前をそのまま返す。
    """
    set_os(os_name)
    entry = _set_fields_unstripped(make_entry(), **fields)
    before = pre_fix_dir_name(*entry._pdf_dir_name_fields())
    assert before == "" or len(before) > MAX_DIR_NAME_LENGTH  # 前提

    assert entry.pdf_dir_name == "Unknown"
    assert entry._pdf_dir_name_before_kb7_fix() == (before or None)


# --- 保存先の長さに依存しないフォルダ名 (known-bug-fixes 要件 1.3) -----------


@pytest.mark.parametrize(
    "title",
    [
        pytest.param("A Study of Testing", id="short"),
        pytest.param("x" * (MAX_DIR_NAME_LENGTH - len(PREFIX)), id="200"),
        pytest.param("x" * 300, id="truncated"),
    ],
)
def test_pdf_dir_name_does_not_depend_on_storage_path_length(
    make_entry: Callable[..., Entry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    title: str,
) -> None:
    """フォルダ名は、保存先ディレクトリが短くても長くても同じになる.

    基準ディレクトリの指定と、既定の PDF ディレクトリの両方で確かめる
    (パスの計算だけで、フォルダは作らない)。
    """
    entry = make_entry(author="Alice Smith", title=title, year="2020")
    name = entry.pdf_dir_name
    short_base = tmp_path / "p"
    long_base = tmp_path.joinpath(*(c * 100 for c in "def"))
    assert len(str(long_base / name / "00.pdf")) > 260  # 前提

    for base in (short_base, long_base):
        assert entry.get_pdf_dir(base).name == name
        monkeypatch.setattr(_entry, "DIRPATH_PDF", base)
        assert entry.pdf_dir_name == name
        assert entry.get_pdf_dir().name == name


# --- 移行と警告の補助 (known-bug-fixes 要件 2.7, 8.3, 1.4) -------------------

# KB7 の範囲のエントリ (年 2020、著者名 188 文字 = 接頭辞 198 文字).
KB7_FIELDS = {"author": "A" * 188, "title": "x" * 20, "year": "2020"}
# 修正後の名前 (195 文字. ``<year> - <first_author>``).
KB7_DIR_NAME = "2020 - " + "A" * 188
# 修正前の規則の名前 (217 文字). タイトルは ``x`` 19 文字 + ``...`` に
# 切り詰められ、末尾の ``...`` が除かれる.
KB7_LEGACY_DIR_NAME = "2020 - " + "A" * 188 + " - " + "x" * 19
# 修正前の名前のフォルダを作れた長さの上限 (いずれかの OS で作成できた長さ).
MAX_LEGACY_DIR_NAME_LENGTH = 255


@pytest.fixture
def kb7_entry(make_entry: Callable[..., Entry]) -> Entry:
    """KB7 の範囲のエントリ. 修正前の名前は ``KB7_LEGACY_DIR_NAME``."""
    return make_entry(**KB7_FIELDS)


class LogRecords(logging.Handler):
    """受け取ったログのレコードを、受け取った順に ``records`` に記録する."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def entry_log() -> Iterator[LogRecords]:
    """``paper_manager.entry._entry`` のログを記録する.

    このモジュールのロガーにハンドラを直接付けるので、ライブラリの
    ルートロガーの伝播の設定 (``propagate``) によらず、各レコードを
    ちょうど 1 回ずつ記録する。テストの後に外す。
    """
    logger = _entry._logger
    handler = LogRecords()
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)


def _contains_all(message: str, texts: Iterable[str]) -> bool:
    """``message`` が ``texts`` (パスの文字列) をすべて含むか.

    修正後の名前は修正前の名前の先頭部分なので、長いものから順に、
    見つけた部分を取り除きながら探す (短いパスが長いパスの一部として
    見つかったことにしない)。
    """
    rest = message
    for text in sorted(texts, key=len, reverse=True):
        if text not in rest:
            return False
        rest = rest.replace(text, "", 1)
    return True


def mentioning(messages: Iterable[str], *paths: Path) -> list[str]:
    """``paths`` の表示用のパスをすべて含む通知・ログだけを返す."""
    shown = [to_display_path(path) for path in paths]
    return [m for m in messages if _contains_all(m, shown)]


def logged(log: LogRecords, level: int, *paths: Path) -> list[str]:
    """``level`` のログのうち、``paths`` の表示用のパスを含むものを返す."""
    return mentioning(
        (r.getMessage() for r in log.records if r.levelno == level), *paths
    )


def fail_method_of(
    monkeypatch: pytest.MonkeyPatch, name: str, target: Path
) -> None:
    """``target`` に対する ``Path.<name>`` だけを ``OSError`` で失敗させる.

    ほかのパスは本来の処理に通す。差し替えはテストの後に元へ戻る。
    権限の変更 (chmod) は環境 (root など) によって効かないので使わない。
    """
    original = getattr(Path, name)

    def failing(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self == target:
            raise PermissionError(
                errno.EACCES, "permission denied (simulated)", str(self)
            )
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, name, failing)


# --- 修正前の名前 legacy_pdf_dir_name (known-bug-fixes 要件 2.7) -------------


@pytest.mark.parametrize(
    ("author_length", "title_length"),
    [(188, 4), (188, 20), (188, 50), (189, 4), (189, 58)],
)
def test_legacy_pdf_dir_name_is_the_pre_fix_name_in_the_kb7_window(
    make_entry: Callable[..., Entry], author_length: int, title_length: int
) -> None:
    """KB7 の範囲では、修正前の規則の名前 (201〜255 文字) を返す."""
    entry = make_entry(
        author="A" * author_length, title="x" * title_length, year="2020"
    )
    expected = pre_fix_dir_name("2020", "A" * author_length, entry["title"])

    legacy = entry.legacy_pdf_dir_name

    assert legacy == expected
    assert MAX_DIR_NAME_LENGTH < len(legacy) <= MAX_LEGACY_DIR_NAME_LENGTH
    assert legacy != entry.pdf_dir_name


def test_legacy_pdf_dir_name_of_the_kb7_entry(kb7_entry: Entry) -> None:
    """移行のテストで使う KB7 のエントリの、修正前と修正後の名前."""
    assert kb7_entry.pdf_dir_name == KB7_DIR_NAME
    assert kb7_entry.legacy_pdf_dir_name == KB7_LEGACY_DIR_NAME
    assert len(KB7_LEGACY_DIR_NAME) == 217


@pytest.mark.parametrize(
    ("dropped", "fields"),
    [
        pytest.param((), {}, id="short-name"),
        pytest.param((), {"title": "x" * 300}, id="truncated-title"),
        pytest.param(("author",), {}, id="no-author"),
        pytest.param((), {"author": ""}, id="empty-author"),
        pytest.param(
            (),
            {"author": "A" * 188, "title": "x" * 59},
            id="pre-fix-name-256-chars",
        ),
        pytest.param(
            (),
            {"author": "A" * 189, "title": "x" * 300},
            id="pre-fix-name-500-chars",
        ),
        pytest.param((), {"year": "1" * 300}, id="year-300-chars"),
        pytest.param(
            (), {"year": "1" * 230, "author": ""}, id="long-year-no-author"
        ),
    ],
)
def test_legacy_pdf_dir_name_is_none_outside_the_kb7_window(
    make_partial_entry: Callable[..., Entry], dropped: tuple, fields: dict
) -> None:
    """修正後と同じ名前、255 文字を超える名前、著者のないエントリは ``None``.

    255 文字を超える名前のフォルダは、どの OS でも作れなかった。修正前の
    規則は著者のないエントリで例外になった (KB2a) ため、その名前の
    フォルダもない。
    """
    entry = make_partial_entry(drop=dropped, **fields)

    assert entry.legacy_pdf_dir_name is None


def test_legacy_pdf_dir_name_of_a_long_year_within_255_chars(
    make_entry: Callable[..., Entry],
) -> None:
    """年が 200 文字を超え 255 文字以内なら、修正前の名前 (年だけ) を返す.

    修正後は年を 200 文字に切り詰める (KB7)。
    """
    entry = make_entry(year="1" * 230)

    assert entry.pdf_dir_name == "1" * MAX_DIR_NAME_LENGTH
    assert entry.legacy_pdf_dir_name == "1" * 230


def test_legacy_pdf_dir_name_has_a_value_only_in_the_kb7_window(
    make_entry: Callable[..., Entry],
) -> None:
    """境界 (接頭辞 197〜200 文字 × タイトル 0〜300 文字) で値を持つ範囲.

    値を持つのは、接頭辞が 198・199 文字 (著者名 188・189 文字) で、
    修正前の名前が 201〜255 文字になるタイトル (4〜58 文字) のときだけ。
    値は修正前の規則の名前と一致する。
    """
    with_value = set()

    for author_length, title_length, entry in boundary_entries(
        make_entry, BOUNDARY_TITLE_UNITS["plain"]
    ):
        legacy = entry.legacy_pdf_dir_name
        if legacy is not None:
            with_value.add((author_length, title_length))
            assert legacy == pre_fix_dir_name(
                "2020", entry["author"], entry["title"]
            )

    assert with_value == set(product((188, 189), range(4, 59)))


# --- 修正前の名前のフォルダの移行 (known-bug-fixes 要件 2.7, 12.5) ----------


@ignore_deprecation
@pytest.mark.parametrize(
    "first_call", ["get_pdf_files", "has_pdf", "get_pdf_count"]
)
def test_pre_fix_folder_is_moved_to_the_current_name(
    kb7_entry: Entry,
    data_paths: DataPaths,
    st_messages: StMessages,
    first_call: str,
) -> None:
    """修正前の名前のフォルダは、一覧の取得時に現在の名前へ移る.

    最初に呼ぶのが一覧・有無・件数のどれでも移り、PDF とその他の
    ファイルは中身ごと保たれる。警告は出ない。
    """
    legacy_dir = data_paths.pdf / KB7_LEGACY_DIR_NAME
    write_pdfs(
        legacy_dir, {"00.pdf": pdf_content("00"), "01.pdf": pdf_content("01")}
    )
    (legacy_dir / "memo.txt").write_text("memo", encoding="utf-8")
    current_dir = data_paths.pdf / KB7_DIR_NAME

    getattr(kb7_entry, first_call)()

    assert snapshot(data_paths.pdf) == {
        KB7_DIR_NAME: None,
        f"{KB7_DIR_NAME}/00.pdf": pdf_content("00"),
        f"{KB7_DIR_NAME}/01.pdf": pdf_content("01"),
        f"{KB7_DIR_NAME}/memo.txt": b"memo",
    }
    assert kb7_entry.get_pdf_files() == [
        current_dir / "00.pdf",
        current_dir / "01.pdf",
    ]
    assert kb7_entry.get_pdf_count() == 2
    assert st_messages.warning == []


@ignore_deprecation
def test_pre_fix_folder_is_moved_inside_base_dir_only(
    kb7_entry: Entry, data_paths: DataPaths, alt_pdf_dir: Path
) -> None:
    """基準ディレクトリを指定すると、その中の修正前の名前のフォルダだけが移る."""
    write_pdf(alt_pdf_dir / KB7_LEGACY_DIR_NAME / "00.pdf", pdf_content("alt"))
    write_pdf(
        data_paths.pdf / KB7_LEGACY_DIR_NAME / "00.pdf", pdf_content("default")
    )
    default_before = snapshot(data_paths.pdf)

    files = kb7_entry.get_pdf_files(alt_pdf_dir)

    assert files == [alt_pdf_dir / KB7_DIR_NAME / "00.pdf"]
    assert snapshot(alt_pdf_dir) == {
        KB7_DIR_NAME: None,
        f"{KB7_DIR_NAME}/00.pdf": pdf_content("alt"),
    }
    assert snapshot(data_paths.pdf) == default_before


def _current_with_pdf(path: Path) -> list[Path]:
    """現在の名前のフォルダに PDF がある."""
    return [write_pdf(path / "00.pdf", pdf_content("current"))]


def _current_empty(path: Path) -> list[Path]:
    """現在の名前のフォルダが空である."""
    path.mkdir(parents=True)
    return []


def _current_is_a_file(path: Path) -> list[Path]:
    """現在の名前がファイルとして存在する."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a folder")
    return []


@ignore_deprecation
@pytest.mark.parametrize(
    "make_current",
    [
        pytest.param(_current_with_pdf, id="folder-with-pdf"),
        pytest.param(_current_empty, id="empty-folder"),
        pytest.param(_current_is_a_file, id="file"),
    ],
)
def test_pre_fix_folder_is_not_moved_when_the_destination_exists(
    kb7_entry: Entry,
    data_paths: DataPaths,
    st_messages: StMessages,
    entry_log: LogRecords,
    make_current: Callable[[Path], list[Path]],
) -> None:
    """現在の名前が既にあれば、修正前の名前のフォルダを移さない.

    何も上書き・移動されず、移さなかったことと両方のパス (表示用) が、
    ログ (WARNING) と画面の警告の両方に出る。一覧は現在の名前のフォルダ
    の PDF だけになる。
    """
    legacy_dir = data_paths.pdf / KB7_LEGACY_DIR_NAME
    write_pdf(legacy_dir / "00.pdf", pdf_content("legacy"))
    current_dir = data_paths.pdf / KB7_DIR_NAME
    expected_files = make_current(current_dir)
    before = snapshot(data_paths.pdf)

    files = kb7_entry.get_pdf_files()

    assert files == expected_files
    assert snapshot(data_paths.pdf) == before
    assert len(mentioning(st_messages.warning, legacy_dir, current_dir)) == 1
    assert (
        len(logged(entry_log, logging.WARNING, legacy_dir, current_dir)) == 1
    )


@ignore_deprecation
def test_pre_fix_folder_move_failure_is_reported(
    kb7_entry: Entry,
    data_paths: DataPaths,
    st_messages: StMessages,
    entry_log: LogRecords,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """フォルダの移動に失敗しても例外にならず、失敗が警告とログに出る.

    元のフォルダは中身ごと残る。警告とログ (ERROR) には、表示用のパスと
    失敗の理由が含まれる。
    """
    legacy_dir = data_paths.pdf / KB7_LEGACY_DIR_NAME
    write_pdf(legacy_dir / "00.pdf", pdf_content("legacy"))
    before = snapshot(data_paths.pdf)
    fail_method_of(monkeypatch, "rename", legacy_dir)

    assert kb7_entry.get_pdf_files() == []

    assert snapshot(data_paths.pdf) == before
    warnings = mentioning(st_messages.warning, legacy_dir)
    assert len(warnings) == 1
    assert "permission denied (simulated)" in warnings[0]
    assert len(logged(entry_log, logging.ERROR, legacy_dir)) == 1


@ignore_deprecation
def test_pre_fix_folder_moves_before_the_legacy_single_pdf_is_merged(
    kb7_entry: Entry, data_paths: DataPaths, st_messages: StMessages
) -> None:
    """修正前の名前のフォルダを移してから、旧形式の単一 PDF を統合する.

    両方があるとき、フォルダが現在の名前へ移り、旧形式の単一 PDF はその
    フォルダの空いている番号へ移る。順序が逆だと、単一 PDF の統合で
    現在の名前のフォルダが先に作られ、修正前の名前のフォルダを移せなく
    なる。警告は出ない。
    """
    single_name = "2020 - " + "A" * 188 + " - " + "x" * 20 + ".pdf"
    assert kb7_entry.pdf_filename == single_name  # 前提
    write_pdf(
        data_paths.pdf / KB7_LEGACY_DIR_NAME / "00.pdf", pdf_content("folder")
    )
    write_pdf(data_paths.pdf / single_name, pdf_content("single"))
    current_dir = data_paths.pdf / KB7_DIR_NAME

    files = kb7_entry.get_pdf_files()

    assert files == [current_dir / "00.pdf", current_dir / "01.pdf"]
    assert snapshot(data_paths.pdf) == {
        KB7_DIR_NAME: None,
        f"{KB7_DIR_NAME}/00.pdf": pdf_content("folder"),
        f"{KB7_DIR_NAME}/01.pdf": pdf_content("single"),
    }
    assert st_messages.warning == []


# --- 旧形式の単一 PDF のフォルダへの統合 (known-bug-fixes 要件 8.3) ----------


@pytest.mark.parametrize(
    ("existing_numbers", "free_number"),
    [((0,), 1), ((1,), 0), ((0, 2), 1), ((0, 1, 2), 3)],
    ids=["00", "01", "00-and-02", "00-01-02"],
)
def test_legacy_single_pdf_moves_into_the_existing_folder(
    entry: Entry,
    data_paths: DataPaths,
    pdf_dir: Path,
    st_messages: StMessages,
    existing_numbers: tuple,
    free_number: int,
) -> None:
    """フォルダが既にあっても、旧形式の単一 PDF は空いている番号へ移る.

    既存の PDF は中身も名前も変わらず、旧形式の PDF は PDF ディレクトリの
    直下から消えて、その論文の PDF として一覧に出る。
    """
    existing = {
        f"{n:02d}.pdf": pdf_content(f"existing {n:02d}")
        for n in existing_numbers
    }
    write_pdfs(pdf_dir, existing)
    write_pdf(data_paths.pdf / LEGACY_SINGLE_NAME, pdf_content("legacy"))

    files = list_pdfs_migrating(entry)

    expected = {**existing, f"{free_number:02d}.pdf": pdf_content("legacy")}
    assert files == [pdf_dir / name for name in sorted(expected)]
    assert snapshot(data_paths.pdf) == {
        ENTRY_DIR_NAME: None,
        **{f"{ENTRY_DIR_NAME}/{name}": c for name, c in expected.items()},
    }
    assert st_messages.warning == []


@ignore_deprecation
def test_legacy_single_pdf_and_legacy_named_pdf_in_folder_are_both_kept(
    entry: Entry, data_paths: DataPaths, pdf_dir: Path
) -> None:
    """旧形式の単一 PDF を移してから、フォルダ内の旧命名規則の PDF を改名する.

    どちらも既存の PDF を上書きせず、別の番号で残る。
    """
    write_pdfs(
        pdf_dir,
        {
            "00.pdf": pdf_content("existing 00"),
            f"{ENTRY_DIR_NAME}_01.pdf": pdf_content("legacy named"),
        },
    )
    write_pdf(data_paths.pdf / LEGACY_SINGLE_NAME, pdf_content("legacy"))

    files = entry.get_pdf_files()

    assert files == [pdf_dir / f"{n:02d}.pdf" for n in range(3)]
    assert snapshot(data_paths.pdf) == {
        ENTRY_DIR_NAME: None,
        f"{ENTRY_DIR_NAME}/00.pdf": pdf_content("existing 00"),
        f"{ENTRY_DIR_NAME}/01.pdf": pdf_content("legacy"),
        f"{ENTRY_DIR_NAME}/02.pdf": pdf_content("legacy named"),
    }


@ignore_deprecation
def test_legacy_single_pdf_never_overwrites_a_name_differing_in_case(
    entry: Entry, data_paths: DataPaths, pdf_dir: Path
) -> None:
    """大文字小文字だけ違う既存のファイル (``00.PDF``) も上書きしない.

    大文字小文字を区別しないファイルシステム (macOS、Windows) では
    ``00.pdf`` と ``00.PDF`` は同じファイルなので、移す前に存在を確かめて
    別の番号へ移す。区別するファイルシステムでは ``00.pdf`` へ移る。
    """
    upper = write_pdf(pdf_dir / "00.PDF", pdf_content("upper"))
    write_pdf(data_paths.pdf / LEGACY_SINGLE_NAME, pdf_content("legacy"))

    files = entry.get_pdf_files()

    assert upper.read_bytes() == pdf_content("upper")
    assert [path.read_bytes() for path in files] == [pdf_content("legacy")]
    assert not (data_paths.pdf / LEGACY_SINGLE_NAME).exists()


@ignore_deprecation
def test_numbered_pdfs_from_100_are_neither_renamed_nor_overwritten(
    entry: Entry, pdf_dir: Path
) -> None:
    """100 番以降の ``NNN.pdf`` は旧命名規則とみなさず、名前も中身も保つ.

    番号のファイル名は 100 番以降が 3 桁になる。旧命名規則の PDF は、
    既存のどの番号とも重ならない番号へ移り、もう一度一覧を取得しても
    何も変わらない。
    """
    numbered = {f"{n:02d}.pdf": pdf_content(f"{n:02d}") for n in range(102)}
    write_pdfs(
        pdf_dir, {**numbered, f"{ENTRY_DIR_NAME}.pdf": pdf_content("legacy")}
    )

    files = entry.get_pdf_files()

    expected = {**numbered, "102.pdf": pdf_content("legacy")}
    assert snapshot(pdf_dir) == expected
    assert files == sorted(pdf_dir / name for name in expected)
    assert entry.get_pdf_files() == files
    assert snapshot(pdf_dir) == expected


@ignore_deprecation
def test_legacy_single_pdf_is_listed_when_it_cannot_be_moved(
    entry: Entry,
    data_paths: DataPaths,
    pdf_dir: Path,
    st_messages: StMessages,
    entry_log: LogRecords,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """移せなかった旧形式の単一 PDF は、一覧に含まれて見えなくならない.

    移す先の番号のファイルを作れない (改名に失敗する) 場合。旧形式の PDF
    とフォルダの PDF はそのまま残り、失敗が警告とログ (ERROR) に出る。
    """
    write_pdf(pdf_dir / "00.pdf", pdf_content("existing 00"))
    legacy = write_pdf(
        data_paths.pdf / LEGACY_SINGLE_NAME, pdf_content("legacy")
    )
    before = snapshot(data_paths.pdf)
    fail_method_of(monkeypatch, "rename", legacy)

    files = entry.get_pdf_files()

    assert files == [pdf_dir / "00.pdf", legacy]
    assert entry.get_pdf_count() == 2
    assert snapshot(data_paths.pdf) == before
    warnings = mentioning(st_messages.warning, legacy)
    assert len(warnings) == 1
    assert "permission denied (simulated)" in warnings[0]
    assert len(logged(entry_log, logging.ERROR, legacy)) == 1


@ignore_deprecation
def test_legacy_single_pdf_is_listed_when_the_folder_cannot_be_created(
    entry: Entry,
    data_paths: DataPaths,
    pdf_dir: Path,
    st_messages: StMessages,
    entry_log: LogRecords,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PDF フォルダを作れなければ、旧形式の単一 PDF をそのまま一覧に含める."""
    legacy = write_pdf(
        data_paths.pdf / LEGACY_SINGLE_NAME, pdf_content("legacy")
    )
    fail_method_of(monkeypatch, "mkdir", pdf_dir)

    assert entry.get_pdf_files() == [legacy]

    assert legacy.read_bytes() == pdf_content("legacy")
    assert not pdf_dir.exists()
    assert len(mentioning(st_messages.warning, pdf_dir)) == 1
    assert len(logged(entry_log, logging.ERROR, pdf_dir)) == 1


@ignore_deprecation
def test_listing_does_not_fail_when_the_legacy_file_name_is_too_long(
    make_entry: Callable[..., Entry],
    data_paths: DataPaths,
    st_messages: StMessages,
) -> None:
    """旧形式の単一 PDF のファイル名が長すぎても、一覧・有無・件数は落ちない.

    タイトルが 300 文字の論文では、旧形式のファイル名が 255 バイトを超え、
    その名前のファイルは作れない。存在の確認はファイルシステムによって
    「名前が長すぎる」エラーになるが (Python 3.9〜3.13 の pathlib は
    これを送出する)、PDF がないものとして扱い、警告も出さない。PDF
    フォルダ (200 文字に切り詰めた名前) の PDF は一覧に出る。
    """
    entry = make_entry(title="x" * 300)
    assert len(entry.pdf_filename.encode()) > 255  # 前提
    data_paths.pdf.mkdir(exist_ok=True)  # 名前の長さの確認まで到達させる

    assert entry.get_pdf_files() == []
    assert entry.has_pdf() is False
    assert entry.get_pdf_count() == 0

    pdf = write_pdf(data_paths.pdf / entry.pdf_dir_name / "00.pdf")

    assert entry.get_pdf_files() == [pdf]
    assert entry.has_pdf() is True
    assert entry.get_pdf_count() == 1
    assert st_messages.warning == []


# --- 同じ対象の警告を繰り返さない (known-bug-fixes 要件 1.4) -----------------


@ignore_deprecation
def test_warning_about_the_same_target_is_shown_only_once(
    make_entry: Callable[..., Entry],
    data_paths: DataPaths,
    st_messages: StMessages,
    entry_log: LogRecords,
) -> None:
    """一覧・有無・件数を何度呼んでも、同じ対象の警告とログは 1 回だけ出る.

    対象が異なれば、それぞれについて 1 回ずつ出る。
    """
    entries = [
        make_entry(**KB7_FIELDS),
        make_entry(**{**KB7_FIELDS, "author": "B" * 188}),
    ]
    conflicts = []
    for entry in entries:
        legacy_dir = data_paths.pdf / entry.legacy_pdf_dir_name
        current_dir = data_paths.pdf / entry.pdf_dir_name
        write_pdf(legacy_dir / "00.pdf", pdf_content("legacy"))
        write_pdf(current_dir / "00.pdf", pdf_content("current"))
        conflicts.append((legacy_dir, current_dir))

    for _ in range(3):
        for entry in entries:
            entry.get_pdf_files()
            entry.has_pdf()
            entry.get_pdf_count()

    assert len(st_messages.warning) == len(conflicts)
    for legacy_dir, current_dir in conflicts:
        assert len(mentioning(st_messages.warning, legacy_dir)) == 1
        assert len(logged(entry_log, logging.WARNING, legacy_dir)) == 1


def seed_conflicting_kb7_folders(
    kb7_entry: Entry, data_paths: DataPaths
) -> tuple[Path, Path]:
    """修正前の名前のフォルダの移す先が既にある状態を、保存して作る.

    論文リストを ``list.json`` に保存し、修正前の名前のフォルダと現在の名前の
    フォルダの両方に PDF を置く。どちらも上書きしないので、移行は行われず、
    移せなかったことの警告が出続ける。

    Returns
    -------
    tuple[Path, Path]
        修正前の名前のフォルダと、現在の名前のフォルダ。
    """
    paper_list = PaperList({})
    key = kb7_entry.get_key(paper_list.keys())
    kb7_entry["ID"] = key
    paper_list[key] = kb7_entry
    paper_list.to_file(data_paths.list_file)
    legacy_dir = data_paths.pdf / KB7_LEGACY_DIR_NAME
    current_dir = data_paths.pdf / KB7_DIR_NAME
    write_pdf(legacy_dir / "00.pdf", pdf_content("legacy"))
    write_pdf(current_dir / "00.pdf", pdf_content("current"))
    return legacy_dir, current_dir


@pytest.mark.filterwarnings(
    "ignore:(pdf_filename|_migrate_legacy_pdf_filenames) is deprecated"
    ":DeprecationWarning"
)
def test_list_page_shows_the_warning_about_a_target_once(
    kb7_entry: Entry, data_paths: DataPaths
) -> None:
    """論文リストページの 1 回の描画で、同じ対象の警告は 1 回だけ出る.

    ページは一覧の件数・統計・孤立 PDF の判定のそれぞれで PDF の一覧を
    取得する。修正前の名前のフォルダの移す先が既にある状態で描画する。
    """
    legacy_dir, current_dir = seed_conflicting_kb7_folders(
        kb7_entry, data_paths
    )
    before = snapshot(data_paths.pdf)

    at = run_page(None)

    assert not at.exception, [e.value for e in at.exception]
    warnings = [w.value for w in at.warning]
    assert len(mentioning(warnings, legacy_dir, current_dir)) == 1, warnings
    assert snapshot(data_paths.pdf) == before


@pytest.mark.filterwarnings(
    "ignore:(pdf_filename|_migrate_legacy_pdf_filenames) is deprecated"
    ":DeprecationWarning"
)
def test_list_page_shows_an_unresolved_warning_again_after_a_rerender(
    kb7_entry: Entry, data_paths: DataPaths
) -> None:
    """再描画の後も解消していない警告は、描画のたびに 1 回ずつ出る.

    警告の抑止は、1 回の描画の中だけで効く。移せないままの対象は、利用者が
    解消するまで、再描画してもセッションの 2 回目以降に黙らず、毎回 1 回
    ずつ表示される (同じ描画の中では繰り返さない)。
    """
    legacy_dir, current_dir = seed_conflicting_kb7_folders(
        kb7_entry, data_paths
    )
    before = snapshot(data_paths.pdf)

    at = run_page(None)
    for render in range(3):
        assert not at.exception, [e.value for e in at.exception]
        warnings = [w.value for w in at.warning]
        assert len(mentioning(warnings, legacy_dir, current_dir)) == 1, (
            render,
            warnings,
        )
        at = at.run()

    assert snapshot(data_paths.pdf) == before


# --- 変換後のパス (known-bug-fixes 要件 1.1, 1.2, 12.3) ----------------------


@pytest.mark.usefixtures("windows_like_paths")
def test_get_pdf_dir_is_built_from_the_converted_pdf_root(
    entry: Entry, data_paths: DataPaths, alt_pdf_dir: Path
) -> None:
    """PDF フォルダのパスは、変換後の PDF ルート (``//`` 付き) から作られる.

    表示用のパスに戻すと、変換前のパスと一致する。
    """
    pdf_dir = entry.get_pdf_dir()

    assert pdf_dir == to_fs_path(data_paths.pdf) / ENTRY_DIR_NAME
    assert str(pdf_dir).startswith("//")
    assert pdf_dir != data_paths.pdf / ENTRY_DIR_NAME  # 前提: 別名が効く
    assert to_display_path(pdf_dir) == str(data_paths.pdf / ENTRY_DIR_NAME)
    assert entry.get_pdf_dir(alt_pdf_dir) == (
        to_fs_path(alt_pdf_dir) / ENTRY_DIR_NAME
    )


def test_get_pdf_dir_does_not_log_a_warning_for_a_long_path(
    entry: Entry, tmp_path: Path, entry_log: LogRecords
) -> None:
    """パス全体が 250 文字を超えても、PDF フォルダのパスの取得で警告しない."""
    long_base = tmp_path.joinpath(*(c * 100 for c in "def"))
    pdf_dir = entry.get_pdf_dir(long_base)
    assert len(str(pdf_dir)) > 250  # 前提

    assert pdf_dir == long_base / ENTRY_DIR_NAME
    assert [r for r in entry_log.records if r.levelno >= logging.WARNING] == []


@ignore_deprecation
def test_listing_and_migrations_work_in_the_converted_space(
    make_entry: Callable[..., Entry],
    data_paths: DataPaths,
    converted_space: ConvertedSpace,
    st_messages: StMessages,
) -> None:
    """変換後のパスの空間で、一覧と 3 種類の移行がどれも成功する.

    返るパスは変換後の PDF ルートの下にあり、そこからの相対パスを求め
    られる (変換前のパスと混在しない)。フォルダと PDF は変換後の空間の
    実体に置き、移行もそこで行われる。``windows-like`` は変換後のパスが
    別の形 (``//`` 付き) になる空間、``redirected`` は変換後のパスが別の
    ディレクトリを指す空間で、変換前のパスで読み書きする実装は何も見つけ
    られない。
    """
    pdf_root = converted_space.pdf
    root = to_fs_path(data_paths.pdf)
    assert root != data_paths.pdf  # 前提: 変換後のパスが別の形になっている
    merged = make_entry()
    write_pdf(pdf_root / ENTRY_DIR_NAME / "00.pdf", pdf_content("00"))
    write_pdf(pdf_root / LEGACY_SINGLE_NAME, pdf_content("merged"))
    write_pdf(
        pdf_root / ENTRY_DIR_NAME / f"{ENTRY_DIR_NAME}_01.pdf",
        pdf_content("renamed"),
    )
    single = make_entry(title="Another Study")
    single_name = "2020 - Alice Smith - Another Study"
    write_pdf(pdf_root / f"{single_name}.pdf", pdf_content("single"))
    moved = make_entry(**KB7_FIELDS)
    write_pdf(pdf_root / KB7_LEGACY_DIR_NAME / "00.pdf", pdf_content("kb7"))

    listings = {
        ENTRY_DIR_NAME: merged.get_pdf_files(),
        single_name: single.get_pdf_files(),
        KB7_DIR_NAME: moved.get_pdf_files(),
    }

    assert listings == {
        ENTRY_DIR_NAME: [
            root / ENTRY_DIR_NAME / f"0{n}.pdf" for n in range(3)
        ],
        single_name: [root / single_name / "00.pdf"],
        KB7_DIR_NAME: [root / KB7_DIR_NAME / "00.pdf"],
    }
    for files in listings.values():
        for path in files:
            assert path.relative_to(root).parts[0] in listings
    assert snapshot(pdf_root) == {
        ENTRY_DIR_NAME: None,
        f"{ENTRY_DIR_NAME}/00.pdf": pdf_content("00"),
        f"{ENTRY_DIR_NAME}/01.pdf": pdf_content("merged"),
        f"{ENTRY_DIR_NAME}/02.pdf": pdf_content("renamed"),
        single_name: None,
        f"{single_name}/00.pdf": pdf_content("single"),
        KB7_DIR_NAME: None,
        f"{KB7_DIR_NAME}/00.pdf": pdf_content("kb7"),
    }
    if converted_space.redirected:
        # 変換前のパスの下には、何も置かれず、何も移されない
        assert snapshot(data_paths.pdf) in (None, {})
    assert st_messages.warning == []


@ignore_deprecation
@pytest.mark.usefixtures("windows_like_paths")
def test_unmoved_legacy_single_pdf_is_listed_with_the_converted_path(
    entry: Entry, data_paths: DataPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """移せなかった旧形式の単一 PDF は、変換後のパスで一覧に含まれる.

    旧形式の単一 PDF のパスも変換後の PDF ルートから作るので、変換後の
    パスへの改名が失敗し、一覧に返るパスも ``//`` で始まる (フォルダの
    PDF と同じ空間にある)。
    """
    root = to_fs_path(data_paths.pdf)
    write_pdf(data_paths.pdf / ENTRY_DIR_NAME / "00.pdf", pdf_content("00"))
    write_pdf(data_paths.pdf / LEGACY_SINGLE_NAME, pdf_content("legacy"))
    fail_method_of(monkeypatch, "rename", root / LEGACY_SINGLE_NAME)

    files = entry.get_pdf_files()

    assert files == [
        root / ENTRY_DIR_NAME / "00.pdf",
        root / LEGACY_SINGLE_NAME,
    ]
    assert str(files[-1]).startswith("//")
    assert files[-1].read_bytes() == pdf_content("legacy")


def _destination_exists(
    make_entry: Callable[..., Entry],
    data_paths: DataPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Entry, list[Path]]:
    """移す先が既にあるため、修正前の名前のフォルダを移さない状況."""
    write_pdf(data_paths.pdf / KB7_LEGACY_DIR_NAME / "00.pdf")
    write_pdf(data_paths.pdf / KB7_DIR_NAME / "00.pdf")
    paths = [
        data_paths.pdf / KB7_LEGACY_DIR_NAME,
        data_paths.pdf / KB7_DIR_NAME,
    ]
    return make_entry(**KB7_FIELDS), paths


def _single_pdf_rename_fails(
    make_entry: Callable[..., Entry],
    data_paths: DataPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Entry, list[Path]]:
    """旧形式の単一 PDF の改名が、変換後のパスに対して失敗する状況."""
    write_pdf(data_paths.pdf / ENTRY_DIR_NAME / "00.pdf")
    write_pdf(data_paths.pdf / LEGACY_SINGLE_NAME)
    root = to_fs_path(data_paths.pdf)
    fail_method_of(monkeypatch, "rename", root / LEGACY_SINGLE_NAME)
    paths = [
        data_paths.pdf / LEGACY_SINGLE_NAME,
        data_paths.pdf / ENTRY_DIR_NAME / "01.pdf",
    ]
    return make_entry(), paths


def _folder_creation_fails(
    make_entry: Callable[..., Entry],
    data_paths: DataPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Entry, list[Path]]:
    """移行先の PDF フォルダの作成が、変換後のパスに対して失敗する状況."""
    write_pdf(data_paths.pdf / LEGACY_SINGLE_NAME)
    root = to_fs_path(data_paths.pdf)
    fail_method_of(monkeypatch, "mkdir", root / ENTRY_DIR_NAME)
    return make_entry(), [data_paths.pdf / ENTRY_DIR_NAME]


@ignore_deprecation
@pytest.mark.usefixtures("windows_like_paths")
@pytest.mark.parametrize(
    ("make_situation", "level"),
    [
        pytest.param(_destination_exists, logging.WARNING, id="not-moved"),
        pytest.param(_single_pdf_rename_fails, logging.ERROR, id="rename"),
        pytest.param(_folder_creation_fails, logging.ERROR, id="mkdir"),
    ],
)
def test_warnings_show_display_paths_without_the_prefix(
    make_entry: Callable[..., Entry],
    data_paths: DataPaths,
    st_messages: StMessages,
    entry_log: LogRecords,
    monkeypatch: pytest.MonkeyPatch,
    make_situation: Callable[..., tuple[Entry, list[Path]]],
    level: int,
) -> None:
    """警告とログには、表示用のパスを出し、変換後のパスを出さない.

    移す先が既にあるため移さなかったとき、旧形式の単一 PDF の改名に
    失敗したとき、PDF フォルダの作成に失敗したときのそれぞれで、警告と
    ログはちょうど 1 件ずつ出る。どちらも変換前のパス (表示用のパス) を
    含み、変換後のパス (別名変換の ``//``) を含まない。失敗の理由にも
    パスを含めない (``OSError`` の文字列には変換後のパスが入る)。
    """
    entry, paths = make_situation(make_entry, data_paths, monkeypatch)
    shown = [str(path) for path in paths]

    entry.get_pdf_files()

    records = [r for r in entry_log.records if r.levelno >= logging.WARNING]
    assert [r.levelno for r in records] == [level]
    assert len(st_messages.warning) == 1
    for message in st_messages.warning + [records[0].getMessage()]:
        assert _contains_all(message, shown), message
        assert "//" not in message, message
