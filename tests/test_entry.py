"""論文エントリ (``Entry``) の単体テスト.

対象は次のとおり。

- キーの正規化と値の前後の空白の除去 (要件 2.1)
- 識別子 (``get_key``) の形式・重複回避・欠落時の既定値 (要件 2.2)
- PDF フォルダ名 (``pdf_dir_name``) の形式と欠落時の既定値 (要件 2.3)
- 200 文字を超えるフォルダ名の切り詰めと末尾の扱い (要件 2.4)
- ファイル名に使えない文字の置換規則 (``sanitize_filename``) を、OS の判定を
  差し替えて Windows 用とそれ以外の両方について検証する (要件 2.5)
- 既知バグ (KB2a, KB5, ほか実装中に判明した 1 件) を strict な xfail として
  記録する (要件 13.1, 13.2)

OS の判定は ``paper_manager.entry._entry`` の ``platform.system()`` で行われる。
``set_os`` fixture でそのモジュールの ``platform`` を差し替えるため、実行 OS に
依存しない (要件 14.2)。OS 固有の文字 (``:`` など) を含まないテストは、差し替え
なしでも実行 OS によらず同じ結果になる。

``Entry`` の PDF 一覧・旧形式の移行・基準ディレクトリの指定 (要件 2.6〜2.9) は、
このファイルの後半 (別タスク) で検証する。
"""

from collections.abc import Callable, Iterable
from types import SimpleNamespace

import pytest

from paper_manager.entry import Entry, PaperList, _entry
from paper_manager.entry._entry import sanitize_filename

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
    [185, 186, 187, 190, 191, 250],
    ids=lambda n: f"author-{n}",
)
def test_very_long_author_still_fits_in_200_chars(
    make_entry: Callable[..., Entry], author_length: int
) -> None:
    """接頭辞だけで 200 文字前後になる長い著者名でも 200 文字以内に収まる.

    接頭辞 (``2020 - <著者> - ``) が 195〜260 文字になる範囲を検証する。
    著者名が 188・189 文字 (接頭辞 198・199 文字) の場合は KB7 で扱う。
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


# --- 既知バグ (要件 13.1, 13.2) ---------------------------------------------


@pytest.mark.xfail(
    reason=(
        "KNOWN BUG: 著者のないエントリの pdf_dir_name が IndexError "
        "になる (KB2a). pdf_dir_name は "
        "split(self.get('author', ''), ' and ')[0] "
        "を評価するが、split('') は空のタプルを返すため [0] が失敗し、"
        "その後の `first_author == ''` による 'Unknown' への置換に到達しない. "
        "Repro: 'author' キーのない (または author='' の) Entry の "
        "pdf_dir_name を参照する (misc では著者は任意). "
        "本来は '2020 - Unknown - T' になる"
    )
)
def test_pdf_dir_name_uses_unknown_for_missing_author(
    make_partial_entry: Callable[..., Entry],
) -> None:
    """著者が欠落したエントリのフォルダ名は ``Unknown`` で作られる."""
    entry = make_partial_entry(drop=("author",), title="T", year="2020")

    assert entry.pdf_dir_name == "2020 - Unknown - T"


@pytest.mark.xfail(
    reason=(
        "KNOWN BUG: Windows 用の置換規則 r'[\\\\/:*?<>|]' に '\"' が"
        "含まれず、Windows で使えない '\"' が置換されずに残る (KB5). "
        "Repro: platform.system() が 'Windows' を返す環境で "
        "sanitize_filename('a\"b') を呼ぶ (タイトルに '\"' を含む論文の PDF "
        "フォルダを Windows で作成できない). 本来は 'a_b' になる"
    )
)
def test_windows_replaces_double_quote_with_underscore(
    set_os: Callable[[str], None],
) -> None:
    """Windows では、禁止文字の ``"`` も ``_`` に置換される."""
    set_os("Windows")

    assert sanitize_filename('a"b') == "a_b"


@pytest.mark.xfail(
    reason=(
        "KNOWN BUG: 接頭辞 '<year> - <first_author> - ' が 198 または "
        "199 文字のとき、pdf_dir_name が 200 文字を超える "
        "(KB7, 実装中に判明). "
        "残りの長さ max_title_length が 2 または 1 になり、"
        "title[:max_title_length - 3] が負のインデックスのスライス "
        "(title[:-1], title[:-2]) になってタイトルがほぼ全文残るため. "
        "Repro: year='2020'、著者名 188 または 189 文字、タイトル 300 文字 "
        "(名前は約 500 文字になる). 本来は 200 文字以内になる (要件 2.4)"
    )
)
def test_name_stays_within_200_chars_when_prefix_leaves_almost_no_room(
    make_entry: Callable[..., Entry],
) -> None:
    """接頭辞がほぼ 200 文字を占める場合も、フォルダ名は 200 文字以内になる."""
    for author_length in (188, 189):
        entry = make_entry(
            author="A" * author_length, title="x" * 300, year="2020"
        )

        assert_valid_dir_name(entry.pdf_dir_name)
