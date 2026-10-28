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

OS の判定は ``paper_manager.entry._entry`` の ``platform.system()`` で行われる。
``set_os`` fixture でそのモジュールの ``platform`` を差し替えるため、実行 OS に
依存しない (要件 14.2)。OS 固有の文字 (``:`` など) を含まないテストは、差し替え
なしでも実行 OS によらず同じ結果になる。

旧形式の移行は非推奨の後方互換コードで、実行すると ``DeprecationWarning`` が
出る。移行を検証するテストでは警告が出ることも確かめ、それ以外のテストでは
警告を無視する。
"""

from collections.abc import Callable, Iterable, Mapping
from itertools import product
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pytest
from conftest import PDF_BYTES, DataPaths, SampleData, write_pdf

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
