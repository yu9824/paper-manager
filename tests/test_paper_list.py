"""論文リスト (``PaperList``) の永続化の単体テスト.

対象は次のとおり。

- ファイルへの保存 (``to_file``) と読み込み (``from_file``) の往復で、キー・
  エントリ内容・非 ASCII 文字が保たれること (要件 3.1)
- ファイルがない場合は空の論文リストになること (要件 3.2)
- JSON が壊れている場合は空の論文リストになり、壊れている旨の警告が通知
  されること (要件 3.3)
- 画面の状態 (``st.session_state``) への書き込み (``to_session_state``) と
  読み出し (``from_session_state``) の往復、および未設定時に空になること
  (要件 3.4)

保存先は ``tmp_path`` か、隔離された ``data_paths.list_file`` を使う。画面の
状態は bare mode の ``st.session_state`` で、各テストの後に conftest が空に
戻す。警告の通知は bare mode では何も表示されないため、``st_messages`` で
記録して検証する (``AppTest`` を使うテストとは併用しない)。
"""

from collections.abc import Callable
from pathlib import Path

import pytest
import streamlit as st
from conftest import DataPaths, SampleData, StMessages

from paper_manager.entry import Entry, PaperList
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
    [
        b'{"AliceSmith2020_0": {"title": "A St',
        b"this is not json",
        b"",
        b'{"AliceSmith2020_0": {"title": "x"},}',
    ],
    ids=["truncated", "plain-text", "empty-file", "trailing-comma"],
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
