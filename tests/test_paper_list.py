"""論文リスト (``PaperList``) の永続化の単体テスト.

対象は次のとおり。

- ファイルへの保存 (``to_file``) と読み込み (``from_file``) の往復で、キー・
  エントリ内容・非 ASCII 文字が保たれること (要件 3.1)
- ファイルがない場合は空の論文リストになること (要件 3.2)
- JSON が壊れている場合は空の論文リストになり、壊れている旨の警告が通知
  されること (要件 3.3)。UTF-8 として読めない、論文の一覧の形をしていない
  (値が辞書でない、フィールドの値が文字列でないなど) 場合も同じであること
  (known-bug-fixes 要件 10.1)
- 画面の状態 (``st.session_state``) への書き込み (``to_session_state``) と
  読み出し (``from_session_state``) の往復、および未設定時に空になること
  (要件 3.4)
- 読み込みの状態の判定 (``load_paper_list``) が、正常・ファイルなし・壊れて
  いるを返し、画面の処理を呼ばないこと。修正前に作られた形の正常なファイル
  を正常と判定すること (known-bug-fixes 要件 10.1、12.5)
- 壊れたファイルの退避 (``backup_broken_paper_list``) が、元のファイルを
  残したまま日時入りの名前 (衝突時は連番) へ複製すること (known-bug-fixes
  要件 10.3)

要件の番号は、注記がなければ test-suite spec のもの。

保存先は ``tmp_path`` か、隔離された ``data_paths.list_file`` を使う。画面の
状態は bare mode の ``st.session_state`` で、各テストの後に conftest が空に
戻す。警告の通知は bare mode では何も表示されないため、``st_messages`` で
記録して検証する (``AppTest`` を使うテストとは併用しない)。
"""

import json
import os
import re
import shutil
import stat
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest
import streamlit as st
from conftest import DataPaths, SampleData, StMessages

from paper_manager.entry import (
    Entry,
    PaperList,
    PaperListLoad,
    PaperListStatus,
    backup_broken_paper_list,
    load_paper_list,
)
from paper_manager.entry._entry import KEY_PAPER_LIST

# ``from_file`` が JSON の破損時に通知する警告の本文.
BROKEN_WARNING = "Broken paper list (json)"
JAPANESE_TITLE = "深層学習による画像認識の研究"
# JSON のエスケープが必要な文字 (引用符・バックスラッシュ・改行) を含む値.
ESCAPE_ABSTRACT = 'A "quoted" word, a back\\slash,\nand a new line.'
# 読み込み側の検証で手書きするファイル (保存処理を通さない UTF-8 の JSON).
HAND_WRITTEN_JSON = """{
    "山田太郎2021_0": {
        "ENTRYTYPE": "article",
        "ID": "山田太郎2021_0",
        "author": "山田 太郎",
        "title": "深層学習による画像認識の研究",
        "year": "2021"
    }
}
"""
_HAND_WRITTEN_BYTES = HAND_WRITTEN_JSON.encode("utf-8")
# 最初の多バイト文字 (``山``) の 1 バイト目までで切った UTF-8 のバイト列.
_TRUNCATED_UTF8 = _HAND_WRITTEN_BYTES[
    : _HAND_WRITTEN_BYTES.index("山".encode("utf-8")) + 1
]
# 文字列から整数への変換の桁数の上限 (既定は 4300 桁) を超える整数.
# 上限のある版 (3.11 以降、3.9.14 / 3.10.7 以降) の ``json.loads`` は
# ``ValueError`` を送出し、上限のない版は整数として読む (どちらでも
# 壊れているとみなす).
_HUGE_INTEGER = b"1" * 5000

# JSON として解析できないファイル.
BROKEN_JSON: list[tuple[str, bytes]] = [
    ("truncated", b'{"AliceSmith2020_0": {"title": "A St'),
    ("plain-text", b"this is not json"),
    ("empty-file", b""),
    ("trailing-comma", b'{"AliceSmith2020_0": {"title": "x"},}'),
]
# JSON の構文の破損 (``BROKEN_JSON``) 以外の壊れ方.
# 修正前の ``from_file`` は、これらで例外を送出するか (UTF-8 として読めない、
# 一覧の形をしていない)、警告を出さずに空の論文リストを返していた (``null``)。
BROKEN_BEYOND_JSON: list[tuple[str, bytes]] = [
    # UTF-8 として読めない
    ("utf8-cut-mid-character", _TRUNCATED_UTF8),
    ("shift-jis", HAND_WRITTEN_JSON.encode("shift_jis")),
    ("invalid-byte", b'{"a": {"title": "\xff"}}'),
    # トップレベルが辞書でない
    ("top-array", b"[]"),
    ("top-string", b'"x"'),
    ("top-number", b"1"),
    ("top-null", b"null"),
    ("top-boolean", b"true"),
    ("top-array-of-entries", b'[{"title": "x"}]'),
    # 値 (1 つの論文) が辞書でない
    ("entry-string", b'{"a": "str"}'),
    ("entry-array", b'{"a": ["title", "x"]}'),
    ("entry-null", b'{"a": null}'),
    ("entry-number", b'{"a": 1}'),
    # フィールドの値が文字列でない
    ("field-number", b'{"a": {"title": 1}}'),
    ("field-year-number", b'{"a": {"title": "x", "year": 2021}}'),
    ("field-null", b'{"a": {"title": null}}'),
    ("field-boolean", b'{"a": {"title": true}}'),
    ("field-array", b'{"a": {"tags": ["x", "y"]}}'),
    ("field-object", b'{"a": {"title": {"x": "y"}}}'),
    (
        "one-bad-entry-among-good",
        b'{"good": {"title": "ok"}, "bad": {"title": "x", "year": 2021}}',
    ),
    ("field-huge-integer", b'{"a": {"year": ' + _HUGE_INTEGER + b"}}"),
    # 解析で再帰の上限を超える入れ子
    ("deeply-nested", b"[" * 100_000),
]


@pytest.fixture
def mixed_paper_list(make_entry: Callable[..., Entry]) -> PaperList:
    """ASCII・日本語・記号を含む複数エントリの論文リストを作る.

    - ``AliceSmith2020_0``: ASCII だけのエントリ (DOI などの追加フィールド付き)
    - ``山田太郎2021_0``: キー・著者・タイトル・掲載誌・タグが日本語で、
      アクセント付きの文字・サロゲートペアが必要な文字 (``𠮷``・絵文字)・
      引用符・バックスラッシュ・改行を値に含むエントリ

    Returns
    -------
    PaperList
        保存前の論文リスト。
    """
    return PaperList(
        {
            "AliceSmith2020_0": make_entry(
                ID="AliceSmith2020_0",
                DOI="10.1234/abc.def",
                volume="12",
                pages="1--10",
            ),
            "山田太郎2021_0": make_entry(
                ID="山田太郎2021_0",
                author="山田 太郎 and José Müller",
                title=JAPANESE_TITLE,
                journal="情報処理学会論文誌",
                year="2021",
                tags="機械学習 and 画像認識",
                note="𠮷野家 😀 naïve café — “quoted”",
                abstract=ESCAPE_ABSTRACT,
            ),
        }
    )


def as_dicts(paper_list: PaperList) -> dict[str, dict[str, str]]:
    """論文リストを、キー → フィールドの辞書、の素の辞書にする."""
    return {key: dict(entry) for key, entry in paper_list.items()}


# --- ファイルとの往復 (要件 3.1) ------------------------------------------


@pytest.mark.parametrize("to_arg", [Path, str], ids=["path", "str"])
def test_roundtrip_preserves_keys_and_entry_contents(
    mixed_paper_list: PaperList, tmp_path: Path, to_arg: Callable
) -> None:
    """保存して読み込むと、保存前と同じキーとエントリ内容が得られる.

    保存先は ``Path`` でも ``str`` でも指定できる。
    """
    filepath = to_arg(tmp_path / "list.json")

    mixed_paper_list.to_file(filepath)
    loaded = PaperList.from_file(filepath)

    assert isinstance(loaded, PaperList)
    assert set(loaded.keys()) == {"AliceSmith2020_0", "山田太郎2021_0"}
    assert as_dicts(loaded) == as_dicts(mixed_paper_list)


def test_roundtrip_returns_entry_objects(
    mixed_paper_list: PaperList, tmp_path: Path
) -> None:
    """読み込んだ値は ``Entry`` で、キーは正規化された形で得られる."""
    filepath = tmp_path / "list.json"

    mixed_paper_list.to_file(filepath)
    loaded = PaperList.from_file(filepath)

    for entry in loaded.values():
        assert isinstance(entry, Entry)
    assert loaded["AliceSmith2020_0"]["DOI"] == "10.1234/abc.def"
    assert loaded["AliceSmith2020_0"]["ENTRYTYPE"] == "article"


def test_roundtrip_preserves_non_ascii_characters(
    mixed_paper_list: PaperList, tmp_path: Path
) -> None:
    """日本語・アクセント付き・絵文字などの非 ASCII 文字が往復で保たれる.

    キー自体が非 ASCII (``山田太郎2021_0``) の場合も含む。
    """
    filepath = tmp_path / "list.json"

    mixed_paper_list.to_file(filepath)
    entry = PaperList.from_file(filepath)["山田太郎2021_0"]

    assert entry["title"] == JAPANESE_TITLE
    assert entry["author"] == "山田 太郎 and José Müller"
    assert entry["journal"] == "情報処理学会論文誌"
    assert entry["note"] == "𠮷野家 😀 naïve café — “quoted”"


def test_roundtrip_preserves_quotes_backslashes_and_newlines(
    mixed_paper_list: PaperList, tmp_path: Path
) -> None:
    """JSON のエスケープが必要な文字 (引用符・``\\``・改行) が往復で保たれる."""
    filepath = tmp_path / "list.json"

    mixed_paper_list.to_file(filepath)
    entry = PaperList.from_file(filepath)["山田太郎2021_0"]

    assert entry["abstract"] == ESCAPE_ABSTRACT


def test_saved_file_keeps_non_ascii_as_readable_utf8_text(
    mixed_paper_list: PaperList, tmp_path: Path
) -> None:
    """保存したファイルは、非 ASCII 文字を UTF-8 の文字のまま含む.

    ``list.json`` はバックアップの zip に入り、利用者が直接開いて確認する
    こともある。``\\uXXXX`` にエスケープされると読めなくなるため、文字
    そのものが UTF-8 で書かれていることを、ファイルのバイト列から確かめる。
    """
    filepath = tmp_path / "list.json"

    mixed_paper_list.to_file(filepath)
    text = filepath.read_bytes().decode("utf-8")

    for literal in ("山田太郎2021_0", JAPANESE_TITLE, "José Müller", "😀"):
        assert literal in text


def test_from_file_reads_utf8_file_written_by_hand(tmp_path: Path) -> None:
    """保存処理を通さない UTF-8 の JSON も、非 ASCII 文字のまま読み込める."""
    filepath = tmp_path / "list.json"
    filepath.write_bytes(HAND_WRITTEN_JSON.encode("utf-8"))

    loaded = PaperList.from_file(filepath)

    assert as_dicts(loaded) == {
        "山田太郎2021_0": {
            "ENTRYTYPE": "article",
            "ID": "山田太郎2021_0",
            "author": "山田 太郎",
            "title": JAPANESE_TITLE,
            "year": "2021",
        }
    }


def test_roundtrip_with_default_path(
    mixed_paper_list: PaperList, data_paths: DataPaths
) -> None:
    """パスを省略すると、隔離されたデータディレクトリの ``list.json`` を使う."""
    assert not data_paths.list_file.exists()

    mixed_paper_list.to_file()
    loaded = PaperList.from_file()

    assert data_paths.list_file.is_file()
    assert as_dicts(loaded) == as_dicts(mixed_paper_list)


def test_roundtrip_of_empty_paper_list(tmp_path: Path) -> None:
    """空の論文リストを保存して読み込むと、空の論文リストが得られる."""
    filepath = tmp_path / "list.json"

    PaperList({}).to_file(filepath)
    loaded = PaperList.from_file(filepath)

    assert filepath.is_file()
    assert isinstance(loaded, PaperList)
    assert as_dicts(loaded) == {}


def test_save_overwrites_previous_content(
    mixed_paper_list: PaperList, tmp_path: Path
) -> None:
    """保存し直すと前の内容は残らず、削除したエントリは読み込まれない."""
    filepath = tmp_path / "list.json"
    mixed_paper_list.to_file(filepath)

    del mixed_paper_list["AliceSmith2020_0"]
    mixed_paper_list.to_file(filepath)
    loaded = PaperList.from_file(filepath)

    assert set(loaded.keys()) == {"山田太郎2021_0"}
    assert as_dicts(loaded) == as_dicts(mixed_paper_list)


def test_sample_data_list_file_is_loaded_back(
    sample_data: SampleData,
) -> None:
    """``sample_data`` が保存した ``list.json`` を、パス省略で読み込める."""
    loaded = PaperList.from_file()

    assert as_dicts(loaded) == as_dicts(sample_data.paper_list)
    assert loaded[sample_data.with_pdf_key]["title"] == JAPANESE_TITLE
    assert sample_data.without_pdf_key in loaded


def test_valid_file_is_loaded_without_notification(
    mixed_paper_list: PaperList, tmp_path: Path, st_messages: StMessages
) -> None:
    """正常なファイルの保存と読み込みでは、警告やエラーは通知されない."""
    filepath = tmp_path / "list.json"

    mixed_paper_list.to_file(filepath)
    PaperList.from_file(filepath)

    assert st_messages.warning == []
    assert st_messages.error == []


# --- ファイルがない場合 (要件 3.2) ----------------------------------------


def test_missing_file_gives_empty_paper_list(
    tmp_path: Path, st_messages: StMessages
) -> None:
    """指定したファイルがなければ、空の論文リストが返る (警告は出ない)."""
    missing = tmp_path / "does-not-exist.json"
    assert not missing.exists()

    loaded = PaperList.from_file(missing)

    assert isinstance(loaded, PaperList)
    assert as_dicts(loaded) == {}
    assert st_messages.warning == []
    assert st_messages.error == []


def test_missing_default_file_gives_empty_paper_list(
    data_paths: DataPaths, st_messages: StMessages
) -> None:
    """パスを省略し、データディレクトリに ``list.json`` がなければ空になる."""
    assert not data_paths.list_file.exists()

    loaded = PaperList.from_file()

    assert isinstance(loaded, PaperList)
    assert as_dicts(loaded) == {}
    assert st_messages.warning == []
    assert st_messages.error == []


# --- JSON が壊れている場合 (要件 3.3) -------------------------------------


@pytest.mark.parametrize(
    "broken",
    [pytest.param(content, id=name) for name, content in BROKEN_JSON],
)
def test_broken_json_gives_empty_paper_list_and_warning(
    tmp_path: Path, st_messages: StMessages, broken: bytes
) -> None:
    """JSON として壊れたファイルは、空の論文リストになり警告が通知される.

    例外は送出されず、警告は 1 回だけ通知される。
    """
    filepath = tmp_path / "list.json"
    filepath.write_bytes(broken)

    loaded = PaperList.from_file(filepath)

    assert isinstance(loaded, PaperList)
    assert as_dicts(loaded) == {}
    assert st_messages.warning == [BROKEN_WARNING]
    assert st_messages.error == []


def test_broken_default_file_gives_empty_paper_list_and_warning(
    data_paths: DataPaths, st_messages: StMessages
) -> None:
    """パスを省略した場合も、壊れた ``list.json`` は空 + 警告になる."""
    data_paths.list_file.write_text("{ broken", encoding="utf-8")

    loaded = PaperList.from_file()

    assert as_dicts(loaded) == {}
    assert st_messages.warning == [BROKEN_WARNING]


# --- 文字コードや形が壊れている場合 (known-bug-fixes 要件 10.1) -----------


@pytest.mark.parametrize(
    "broken",
    [pytest.param(content, id=name) for name, content in BROKEN_BEYOND_JSON],
)
def test_from_file_treats_undecodable_or_misshapen_file_as_broken(
    tmp_path: Path, st_messages: StMessages, broken: bytes
) -> None:
    """文字コードや形が壊れたファイルも、空の論文リスト + 警告になる.

    UTF-8 として読めない、論文の一覧の形をしていない (トップレベルが
    辞書でない、値が辞書でない、フィールドの値が文字列でない) ファイルを、
    JSON の破損と同じく、例外を送出せずに従来の警告 1 回で知らせる
    (known-bug-fixes 要件 10.1)。読み込みはファイルを変えない。
    """
    filepath = tmp_path / "list.json"
    filepath.write_bytes(broken)

    loaded = PaperList.from_file(filepath)

    assert isinstance(loaded, PaperList)
    assert as_dicts(loaded) == {}
    assert st_messages.warning == [BROKEN_WARNING]
    assert st_messages.error == []
    assert filepath.read_bytes() == broken
    assert sorted(p.name for p in tmp_path.iterdir()) == ["list.json"]


def test_from_file_treats_directory_at_path_as_missing(
    tmp_path: Path, st_messages: StMessages
) -> None:
    """パスがファイルでなくディレクトリなら、ファイルがないときと同じになる.

    修正前の読み込みと同じく、空の論文リストを返し、警告は出さない。
    """
    directory = tmp_path / "list.json"
    directory.mkdir()

    loaded = PaperList.from_file(directory)

    assert as_dicts(loaded) == {}
    assert st_messages.warning == []
    assert st_messages.error == []


# --- 読み込みの状態の判定 (known-bug-fixes 要件 10.1、12.5) ---------------


def test_paper_list_status_values() -> None:
    """状態は文字列の値を持つ 3 種類 (正常・ファイルなし・壊れている)."""
    assert [(s.name, s.value) for s in PaperListStatus] == [
        ("OK", "ok"),
        ("MISSING", "missing"),
        ("BROKEN", "broken"),
    ]
    assert all(isinstance(s, str) for s in PaperListStatus)
    assert PaperListStatus("broken") is PaperListStatus.BROKEN


@pytest.mark.parametrize("to_arg", [Path, str], ids=["path", "str"])
def test_load_saved_file_is_ok(
    mixed_paper_list: PaperList,
    tmp_path: Path,
    st_messages: StMessages,
    to_arg: Callable,
) -> None:
    """保存処理で書いたファイルは正常で、保存前と同じ論文リストになる."""
    filepath = tmp_path / "list.json"
    mixed_paper_list.to_file(filepath)

    result = load_paper_list(to_arg(filepath))

    assert isinstance(result, PaperListLoad)
    paper_list, status, reason = result
    assert status is PaperListStatus.OK
    assert reason is None
    assert isinstance(paper_list, PaperList)
    assert as_dicts(paper_list) == as_dicts(mixed_paper_list)
    for entry in paper_list.values():
        assert isinstance(entry, Entry)
    assert st_messages == StMessages(error=[], warning=[], success=[])


# 修正前に作られうる正常なファイル (保存処理を通さない書式を含む) と、
# 読み込んだ論文リスト (キー → フィールドの辞書) の組 (要件 12.5).
_EXPECTED_HAND_WRITTEN = {
    "山田太郎2021_0": {
        "ENTRYTYPE": "article",
        "ID": "山田太郎2021_0",
        "author": "山田 太郎",
        "title": JAPANESE_TITLE,
        "year": "2021",
    }
}
VALID_FILES: list[tuple[str, bytes, dict[str, dict[str, str]]]] = [
    ("hand-written", _HAND_WRITTEN_BYTES, _EXPECTED_HAND_WRITTEN),
    (
        "crlf-line-endings",
        HAND_WRITTEN_JSON.replace("\n", "\r\n").encode("utf-8"),
        _EXPECTED_HAND_WRITTEN,
    ),
    (
        "compact-ascii-escaped",
        json.dumps(
            json.loads(HAND_WRITTEN_JSON), separators=(",", ":")
        ).encode("ascii"),
        _EXPECTED_HAND_WRITTEN,
    ),
    ("empty-object", b"{}", {}),
    ("entry-without-fields", b'{"a": {}}', {"a": {}}),
    (
        "keys-in-other-case-and-padded-values",
        b'{"k": {"Title": "  T  ", "entrytype": "misc", "doi": "10.1/x"}}',
        {"k": {"title": "T", "ENTRYTYPE": "misc", "DOI": "10.1/x"}},
    ),
    ("empty-string-value", b'{"k": {"note": ""}}', {"k": {"note": ""}}),
]


@pytest.mark.parametrize(
    "content, expected",
    [pytest.param(c, e, id=name) for name, c, e in VALID_FILES],
)
def test_load_valid_file_written_outside_the_app_is_ok(
    tmp_path: Path,
    st_messages: StMessages,
    content: bytes,
    expected: dict[str, dict[str, str]],
) -> None:
    """修正前に作られた形の正常なファイルは、正常として読み込める.

    フィールドの値がすべて文字列の、辞書の辞書であれば、書式 (改行・
    区切りの空白・非 ASCII 文字のエスケープ) やキーの大文字小文字に
    かかわらず正常とする (要件 12.5)。
    """
    filepath = tmp_path / "list.json"
    filepath.write_bytes(content)

    paper_list, status, reason = load_paper_list(filepath)

    assert status is PaperListStatus.OK
    assert reason is None
    assert as_dicts(paper_list) == expected
    assert filepath.read_bytes() == content
    assert st_messages == StMessages(error=[], warning=[], success=[])


def test_load_sample_data_list_file_is_ok(sample_data: SampleData) -> None:
    """``sample_data`` が保存した ``list.json`` は、パス省略で正常に読める."""
    paper_list, status, reason = load_paper_list()

    assert status is PaperListStatus.OK
    assert reason is None
    assert as_dicts(paper_list) == as_dicts(sample_data.paper_list)


def test_load_missing_file_is_missing(
    tmp_path: Path, st_messages: StMessages
) -> None:
    """ファイルがなければ、空の論文リストで状態はファイルなしになる."""
    missing = tmp_path / "does-not-exist.json"

    paper_list, status, reason = load_paper_list(missing)

    assert status is PaperListStatus.MISSING
    assert reason is None
    assert isinstance(paper_list, PaperList)
    assert as_dicts(paper_list) == {}
    assert not missing.exists()
    assert st_messages == StMessages(error=[], warning=[], success=[])


def test_load_missing_default_file_is_missing(data_paths: DataPaths) -> None:
    """パスを省略し、``list.json`` がなければファイルなしになり、作らない."""
    assert not data_paths.list_file.exists()

    paper_list, status, reason = load_paper_list()

    assert status is PaperListStatus.MISSING
    assert as_dicts(paper_list) == {}
    assert not data_paths.list_file.exists()


def test_load_directory_at_path_is_missing(tmp_path: Path) -> None:
    """パスがディレクトリなら、ファイルなしとする (修正前と同じ扱い)."""
    directory = tmp_path / "list.json"
    directory.mkdir()

    paper_list, status, reason = load_paper_list(directory)

    assert status is PaperListStatus.MISSING
    assert as_dicts(paper_list) == {}


@pytest.mark.parametrize(
    "broken",
    [pytest.param(content, id=name) for name, content in BROKEN_JSON]
    + [pytest.param(content, id=name) for name, content in BROKEN_BEYOND_JSON],
)
def test_load_broken_file_is_broken_without_notification(
    tmp_path: Path, st_messages: StMessages, broken: bytes
) -> None:
    """壊れ方のどのパターンでも、例外を送出せず状態は壊れているになる.

    論文リストは空 (壊れていない論文だけを読み込むことはしない) で、
    理由を返す。画面には何も出さず (通知は呼び出し元の役割)、ファイルを
    変えない (要件 10.1)。
    """
    filepath = tmp_path / "list.json"
    filepath.write_bytes(broken)

    paper_list, status, reason = load_paper_list(filepath)

    assert status is PaperListStatus.BROKEN
    assert isinstance(paper_list, PaperList)
    assert as_dicts(paper_list) == {}
    assert isinstance(reason, str)
    assert reason
    assert st_messages == StMessages(error=[], warning=[], success=[])
    assert filepath.read_bytes() == broken
    assert sorted(p.name for p in tmp_path.iterdir()) == ["list.json"]


def test_load_unreadable_file_raises_instead_of_broken(
    tmp_path: Path,
) -> None:
    """読み取り権限がないなどの ``OSError`` は、壊れているとは区別して送出する.

    中身を読めていないファイルを壊れているとみなすと、保存の前の退避で
    退避できずに上書きされうるため、呼び出し元に失敗を伝える。
    """
    filepath = tmp_path / "list.json"
    filepath.write_bytes(_HAND_WRITTEN_BYTES)
    filepath.chmod(0)
    try:
        if os.access(filepath, os.R_OK):
            pytest.skip("the file stays readable (root or no POSIX modes)")
        with pytest.raises(PermissionError):
            load_paper_list(filepath)
    finally:
        filepath.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert filepath.read_bytes() == _HAND_WRITTEN_BYTES


# --- 壊れたファイルの退避 (known-bug-fixes 要件 10.3) ---------------------

# 退避の時刻 (テストでは固定して名前を決定的にする) と、その日時の部分.
BACKUP_NOW = datetime(2026, 1, 2, 3, 4, 5)
BACKUP_STAMP = "20260102_030405"
BACKUP_NAME = f"list.json.broken-{BACKUP_STAMP}"
# 復元が既存の ``list.json`` を退避する名前 (復元のたびに上書きされる).
RESTORE_BACKUP_NAME = "list.json.backup"


def file_names(directory: Path) -> list[str]:
    """``directory`` の直下の名前を並べて返す."""
    return sorted(p.name for p in directory.iterdir())


@pytest.mark.parametrize(
    "broken",
    [
        pytest.param(b'{"a": {"title": "A St', id="truncated-json"),
        pytest.param(_TRUNCATED_UTF8, id="utf8-cut-mid-character"),
        pytest.param(b"[]", id="top-array"),
        pytest.param(b'{"a": {"title": 1}}', id="field-number"),
    ],
)
def test_backup_copies_broken_file_to_timestamped_name(
    tmp_path: Path, st_messages: StMessages, broken: bytes
) -> None:
    """壊れたファイルを、元のファイルを残したまま日時入りの名前へ複製する.

    退避先は同じディレクトリの ``list.json.broken-<YYYYmmdd_HHMMSS>`` で、
    中身は元のファイルと同じバイト列。更新時刻も元のファイルのものを
    保つ。元のファイルのバイト列と更新時刻は変わらず、画面には何も出さない。
    """
    filepath = tmp_path / "list.json"
    filepath.write_bytes(broken)
    # 複製の時刻と区別できるよう、元のファイルの更新時刻を過去にしておく
    past_ns = 1_000_000_000 * 10**9
    os.utime(filepath, ns=(past_ns, past_ns))
    original_mtime_ns = filepath.stat().st_mtime_ns

    backup = backup_broken_paper_list(filepath, now=BACKUP_NOW)

    assert backup == tmp_path / BACKUP_NAME
    assert backup.read_bytes() == broken
    assert backup.stat().st_mtime == pytest.approx(
        original_mtime_ns / 1e9, abs=1e-3
    )
    assert filepath.read_bytes() == broken
    assert filepath.stat().st_mtime_ns == original_mtime_ns
    assert file_names(tmp_path) == sorted(["list.json", BACKUP_NAME])
    assert st_messages == StMessages(error=[], warning=[], success=[])


@pytest.mark.parametrize(
    "taken, expected_suffix",
    [
        ([""], "-1"),
        (["", "-1"], "-2"),
        (["", "-1", "-2"], "-3"),
        (["-1"], ""),
    ],
    ids=["base-taken", "base-and-1-taken", "up-to-2-taken", "only-1-taken"],
)
def test_backup_numbers_the_name_when_it_is_taken(
    tmp_path: Path, taken: list[str], expected_suffix: str
) -> None:
    """退避先の名前が既にあれば ``-1``、``-2`` と連番を付け、既存を上書きしない."""
    filepath = tmp_path / "list.json"
    filepath.write_bytes(b"{ broken")
    existing = {
        tmp_path / f"{BACKUP_NAME}{suffix}": f"earlier {suffix!r}".encode()
        for suffix in taken
    }
    for path, content in existing.items():
        path.write_bytes(content)

    backup = backup_broken_paper_list(filepath, now=BACKUP_NOW)

    assert backup == tmp_path / f"{BACKUP_NAME}{expected_suffix}"
    assert backup.read_bytes() == b"{ broken"
    for path, content in existing.items():
        assert path.read_bytes() == content
    assert filepath.read_bytes() == b"{ broken"


def test_backup_twice_in_the_same_second_keeps_both(tmp_path: Path) -> None:
    """同じ時刻に 2 回退避すると、2 回目は連番の名前になり、1 回目を残す."""
    filepath = tmp_path / "list.json"
    filepath.write_bytes(b"{ first")
    first = backup_broken_paper_list(filepath, now=BACKUP_NOW)
    filepath.write_bytes(b"{ second")

    second = backup_broken_paper_list(filepath, now=BACKUP_NOW)

    assert first == tmp_path / BACKUP_NAME
    assert second == tmp_path / f"{BACKUP_NAME}-1"
    assert first.read_bytes() == b"{ first"
    assert second.read_bytes() == b"{ second"


def test_backup_does_not_follow_a_link_at_the_name(tmp_path: Path) -> None:
    """退避先の名前にリンク (指す先がないものも) があれば、別の名前にする.

    リンクをたどって、指す先にファイルを作ったり上書きしたりしない。
    """
    filepath = tmp_path / "list.json"
    filepath.write_bytes(b"{ broken")
    target = tmp_path / "elsewhere" / "target.json"
    link = tmp_path / BACKUP_NAME
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available")

    backup = backup_broken_paper_list(filepath, now=BACKUP_NOW)

    assert backup == tmp_path / f"{BACKUP_NAME}-1"
    assert backup.read_bytes() == b"{ broken"
    assert link.is_symlink()
    assert not os.path.lexists(target)


def test_backup_does_not_use_the_restore_backup_name(tmp_path: Path) -> None:
    """退避は、復元が使う ``list.json.backup`` を作らず、上書きもしない."""
    filepath = tmp_path / "list.json"
    filepath.write_bytes(b"{ broken")
    restore_backup = tmp_path / RESTORE_BACKUP_NAME
    restore_backup.write_bytes(b'{"kept": {}}')

    backup = backup_broken_paper_list(filepath, now=BACKUP_NOW)

    assert backup is not None
    assert backup.name != RESTORE_BACKUP_NAME
    assert restore_backup.read_bytes() == b'{"kept": {}}'
    assert file_names(tmp_path) == sorted(
        ["list.json", RESTORE_BACKUP_NAME, BACKUP_NAME]
    )


@pytest.mark.parametrize("state", ["valid", "missing", "directory"])
def test_backup_does_nothing_unless_broken(
    tmp_path: Path, mixed_paper_list: PaperList, state: str
) -> None:
    """壊れていなければ (正常・ファイルなし) 何も作らず ``None`` を返す."""
    filepath = tmp_path / "list.json"
    if state == "valid":
        mixed_paper_list.to_file(filepath)
    elif state == "directory":
        filepath.mkdir()
    before = file_names(tmp_path)
    content = filepath.read_bytes() if state == "valid" else None

    backup = backup_broken_paper_list(filepath, now=BACKUP_NOW)

    assert backup is None
    assert file_names(tmp_path) == before
    if content is not None:
        assert filepath.read_bytes() == content


def test_backup_with_default_path_and_time(data_paths: DataPaths) -> None:
    """パスと時刻を省略すると、データディレクトリの ``list.json`` を退避する.

    名前の日時の部分は ``YYYYmmdd_HHMMSS`` の形 (値は実行時刻なので、
    形だけを確かめる)。
    """
    data_paths.list_file.write_bytes(b"{ broken")

    backup = backup_broken_paper_list()

    assert backup is not None
    assert backup.parent == data_paths.root
    assert re.fullmatch(r"list\.json\.broken-\d{8}_\d{6}", backup.name)
    assert backup.read_bytes() == b"{ broken"
    assert data_paths.list_file.read_bytes() == b"{ broken"


def test_backup_failure_raises_and_leaves_no_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """複製に失敗したら例外を送出し、退避先の名前のファイルを残さない.

    退避できなかったことを ``None`` (壊れていない) と区別して呼び出し元に
    伝え、壊れたファイルを上書きさせない。元のファイルは変わらない。
    """
    filepath = tmp_path / "list.json"
    filepath.write_bytes(b"{ broken")

    def failing_copy2(*args: object, **kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(shutil, "copy2", failing_copy2)

    with pytest.raises(OSError, match="No space left on device"):
        backup_broken_paper_list(filepath, now=BACKUP_NOW)

    assert file_names(tmp_path) == ["list.json"]
    assert filepath.read_bytes() == b"{ broken"


def test_from_file_does_not_back_up_broken_file(
    tmp_path: Path, st_messages: StMessages
) -> None:
    """読み込みだけでは退避しない (退避は保存の前に呼び出し元が行う)."""
    filepath = tmp_path / "list.json"
    filepath.write_bytes(b"[]")

    PaperList.from_file(filepath)

    assert file_names(tmp_path) == ["list.json"]
    assert st_messages.warning == [BROKEN_WARNING]


# --- 画面の状態との往復 (要件 3.4) ----------------------------------------


def test_session_state_roundtrip_returns_same_content(
    mixed_paper_list: PaperList,
) -> None:
    """画面の状態へ書き込んで読み出すと、同じ内容の論文リストが得られる."""
    mixed_paper_list.to_session_state()
    loaded = PaperList.from_session_state()

    assert KEY_PAPER_LIST in st.session_state
    assert isinstance(loaded, PaperList)
    assert set(loaded.keys()) == {"AliceSmith2020_0", "山田太郎2021_0"}
    assert as_dicts(loaded) == as_dicts(mixed_paper_list)
    assert loaded["山田太郎2021_0"]["title"] == JAPANESE_TITLE


def test_session_state_unset_gives_empty_paper_list() -> None:
    """画面の状態が未設定なら、空の論文リストが得られる."""
    assert KEY_PAPER_LIST not in st.session_state

    loaded = PaperList.from_session_state()

    assert isinstance(loaded, PaperList)
    assert as_dicts(loaded) == {}


def test_session_state_write_replaces_previous_content(
    mixed_paper_list: PaperList, make_entry: Callable[..., Entry]
) -> None:
    """書き込み直すと、前の内容は残らず新しい内容だけが読み出される."""
    mixed_paper_list.to_session_state()
    replacement = PaperList({"BobJones2022_0": make_entry(author="Bob Jones")})

    replacement.to_session_state()
    loaded = PaperList.from_session_state()

    assert set(loaded.keys()) == {"BobJones2022_0"}
    assert as_dicts(loaded) == as_dicts(replacement)


def test_session_state_write_does_not_create_file(
    mixed_paper_list: PaperList, data_paths: DataPaths
) -> None:
    """画面の状態への書き込みは、``list.json`` を作らない."""
    mixed_paper_list.to_session_state()

    assert not data_paths.list_file.exists()


def test_file_save_does_not_populate_session_state(
    mixed_paper_list: PaperList, tmp_path: Path
) -> None:
    """ファイルへの保存は、画面の状態を設定しない."""
    mixed_paper_list.to_file(tmp_path / "list.json")

    assert KEY_PAPER_LIST not in st.session_state
    assert as_dicts(PaperList.from_session_state()) == {}
