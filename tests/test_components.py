"""components (``app.components._common``) の単体テスト.

画面から呼ばれるファイル操作と論文リスト操作を、関数を直接呼んで検証する。
このモジュールは次のセクションで構成される。

- 共通の補助: fixture と補助関数 (後続のセクションからも使う)
- PDF の保存: ``save_pdfs`` (要件 9.1, 9.2, 9.3)
- PDF の削除: ``delete_pdfs`` (要件 9.4, 9.5)
- 論文の登録・更新: ``register_entry_to_list`` / ``update_entry_in_list``
  (要件 9.6〜9.10)。このセクションだけで使う補助は、セクションの冒頭に
  ある

入力フォーム部品 (要件 9.11, 9.12) のセクションは、このあとに追記する。
追記するセクションが使う補助は「共通の補助」に加えるか、そのセクションの
冒頭に置く。

保存・削除・登録・更新は ``data_paths`` (隔離された一時ディレクトリの配下)
か、``tmp_path`` の中だけで行い、実データには触れない。

PDF の置き場所は ``Entry.get_pdf_dir`` から導出する。通知 (``st.error``)
は ``st_messages`` で記録して検証する。``st_messages`` は ``AppTest`` と
併用できないので、画面テストを追加するときは別のテスト関数にする。

旧形式の単一 PDF の削除は ``Entry.pdf_filename`` (非推奨) を参照するため、
``DeprecationWarning`` が出る。警告の確認が目的ではないので、この 2 つの
非推奨 API の警告だけを無視する (ほかの警告は隠さない)。
"""

from collections.abc import Callable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import NamedTuple, Optional

import pytest
from conftest import PDF_BYTES, DataPaths, FakeUpload, StMessages, write_pdf

from paper_manager.app.components import _common
from paper_manager.app.components._common import (
    delete_pdfs,
    register_entry_to_list,
    save_pdfs,
    update_entry_in_list,
)
from paper_manager.entry import Entry, PaperList

pytestmark = pytest.mark.filterwarnings(
    "ignore:(pdf_filename|_migrate_legacy_pdf_filenames) is deprecated"
    ":DeprecationWarning"
)

# save_pdfs が、これを超えるパスのファイルを保存しない上限 (要件 9.3).
MAX_PATH_LENGTH = 250


# --- 共通の補助 -----------------------------------------------------------


def make_uploads(count: int, start: int = 0) -> list[FakeUpload]:
    """中身が互いに異なる ``count`` 件のアップロードを作る.

    中身が異なるので、保存後の中身を比べると取り違えや上書きを検出できる。
    アップロード時のファイル名 (``upload-<番号>.pdf``) は、保存名とは無関係。

    Parameters
    ----------
    count : int
        作る件数。
    start : int, default 0
        最初の番号。中身とファイル名に使う。
    """
    return [
        FakeUpload(
            f"upload-{index:02d}.pdf",
            PDF_BYTES + f"%upload {index:02d}\n".encode("ascii"),
        )
        for index in range(start, start + count)
    ]


def make_pdfs(pdf_dir: Path, names: list[str]) -> dict[str, bytes]:
    """``pdf_dir`` に ``names`` の PDF を、互いに異なる中身で作る.

    Returns
    -------
    dict[str, bytes]
        ファイル名 → 書き込んだ中身。
    """
    contents = {
        name: PDF_BYTES + f"%existing {name}\n".encode("ascii")
        for name in names
    }
    for name, content in contents.items():
        write_pdf(pdf_dir / name, content)
    return contents


def pdf_names(pdf_dir: Path) -> list[str]:
    """``pdf_dir`` の直下にあるファイル名を、ソートして返す."""
    return sorted(path.name for path in pdf_dir.iterdir())


def padded_base_dir(
    root: Path, entry: Entry, file_name: str, target_length: int
) -> Path:
    """``file_name`` の保存パスの長さがちょうど ``target_length`` になる基点を作る.

    ``root`` の下に 1 階層の詰め物を足し、
    ``<基点>/<エントリのフォルダ>/<file_name>`` の文字数を揃える。
    基点のフォルダは作らない。

    Parameters
    ----------
    root : Path
        詰め物を足す前の基点。
    entry : Entry
        保存対象のエントリ。
    file_name : str
        長さを測る保存名 (例: ``"00.pdf"``)。
    target_length : int
        保存パス (文字列) の目標の長さ。

    Returns
    -------
    Path
        ``len(str(基点 / entry.pdf_dir_name / file_name)) == target_length``
        になる基点。
    """
    unpadded = len(str(root / entry.pdf_dir_name / file_name))
    # 詰め物を 1 階層足すと、詰め物の長さ + 区切り 1 文字だけ長くなる
    padding = target_length - unpadded - 1
    assert 1 <= padding <= 200, (
        f"cannot pad {root} to {target_length} characters (padding={padding})"
    )
    base_dir = root / ("p" * padding)
    assert (
        len(str(base_dir / entry.pdf_dir_name / file_name)) == target_length
    )
    return base_dir


@pytest.fixture
def entry(make_entry: Callable[..., Entry]) -> Entry:
    """既定値のエントリ (``2020 - Alice Smith - A Study of Testing``)."""
    return make_entry()


@pytest.fixture
def other_entry(make_entry: Callable[..., Entry]) -> Entry:
    """``entry`` とは別のフォルダになるエントリ (巻き添えの検出用)."""
    return make_entry(author="Bob Jones", title="Quantum Widgets", year="2019")


@pytest.fixture
def pdf_root(data_paths: DataPaths) -> Path:
    """PDF ディレクトリ. ``_common`` が参照する定数と一致することを確かめる.

    保存・削除のテストは、このディレクトリの配下 (隔離された一時
    ディレクトリの中) だけを操作する。
    """
    assert _common.DIRPATH_PDF == data_paths.pdf
    return data_paths.pdf


# --- PDF の保存: save_pdfs (要件 9.1, 9.2, 9.3) ----------------------------


def test_save_pdfs_numbers_files_from_zero_and_returns_count(
    entry: Entry, pdf_root: Path, st_messages: StMessages
) -> None:
    """エントリのフォルダに 00.pdf から連番で保存され、件数が返る (要件 9.1)."""
    uploads = make_uploads(3)

    saved = save_pdfs(uploads, entry)

    pdf_dir = entry.get_pdf_dir(pdf_root)
    assert saved == 3
    assert pdf_names(pdf_dir) == ["00.pdf", "01.pdf", "02.pdf"]
    for index, upload in enumerate(uploads):
        saved_file = pdf_dir / f"{index:02d}.pdf"
        assert saved_file.read_bytes() == upload.getvalue()
    assert pdf_names(pdf_root) == [entry.pdf_dir_name]
    assert st_messages.error == []


def test_save_pdfs_saves_under_the_given_base_dir(
    entry: Entry, pdf_root: Path, tmp_path: Path, st_messages: StMessages
) -> None:
    """``base_dir`` を指定すると、その下のエントリのフォルダに保存される (要件 9.1)."""
    base_dir = tmp_path / "elsewhere"
    uploads = make_uploads(2)

    saved = save_pdfs(uploads, entry, base_dir)

    pdf_dir = entry.get_pdf_dir(base_dir)
    assert saved == 2
    assert pdf_names(pdf_dir) == ["00.pdf", "01.pdf"]
    assert (pdf_dir / "01.pdf").read_bytes() == uploads[1].getvalue()
    assert not entry.get_pdf_dir(pdf_root).exists()  # 既定の置き場所は無傷
    assert st_messages.error == []


def test_save_pdfs_continues_numbering_after_existing_files(
    entry: Entry, pdf_root: Path, st_messages: StMessages
) -> None:
    """既存の 00.pdf・01.pdf があれば 02.pdf から保存し、既存を上書きしない (要件 9.1)."""
    pdf_dir = entry.get_pdf_dir(pdf_root)
    existing = make_pdfs(pdf_dir, ["00.pdf", "01.pdf"])
    uploads = make_uploads(2)

    saved = save_pdfs(uploads, entry)

    assert saved == 2
    assert pdf_names(pdf_dir) == ["00.pdf", "01.pdf", "02.pdf", "03.pdf"]
    for name, content in existing.items():
        assert (pdf_dir / name).read_bytes() == content
    assert (pdf_dir / "02.pdf").read_bytes() == uploads[0].getvalue()
    assert (pdf_dir / "03.pdf").read_bytes() == uploads[1].getvalue()
    assert st_messages.error == []


def test_save_pdfs_skips_occupied_numbers_without_overwriting(
    entry: Entry, pdf_root: Path, st_messages: StMessages
) -> None:
    """既存の番号に欠番があれば、空いている番号を順に使う (要件 9.1)."""
    pdf_dir = entry.get_pdf_dir(pdf_root)
    existing = make_pdfs(pdf_dir, ["00.pdf", "02.pdf"])
    uploads = make_uploads(3)

    saved = save_pdfs(uploads, entry)

    assert saved == 3
    assert pdf_names(pdf_dir) == [f"{i:02d}.pdf" for i in range(5)]
    for name, content in existing.items():
        assert (pdf_dir / name).read_bytes() == content
    # 空き番号 01、03、04 に、アップロード順で保存される
    for name, upload in zip(["01.pdf", "03.pdf", "04.pdf"], uploads):
        assert (pdf_dir / name).read_bytes() == upload.getvalue()
    assert st_messages.error == []


def test_save_pdfs_called_twice_does_not_reuse_numbers(
    entry: Entry, pdf_root: Path, st_messages: StMessages
) -> None:
    """続けて保存しても、前回の番号と重複しない (要件 9.1)."""
    first = make_uploads(2)
    second = make_uploads(1, start=2)

    assert save_pdfs(first, entry) == 2
    assert save_pdfs(second, entry) == 1

    pdf_dir = entry.get_pdf_dir(pdf_root)
    assert pdf_names(pdf_dir) == ["00.pdf", "01.pdf", "02.pdf"]
    assert (pdf_dir / "00.pdf").read_bytes() == first[0].getvalue()
    assert (pdf_dir / "01.pdf").read_bytes() == first[1].getvalue()
    assert (pdf_dir / "02.pdf").read_bytes() == second[0].getvalue()
    assert st_messages.error == []


def test_save_pdfs_with_no_files_saves_nothing(
    entry: Entry, pdf_root: Path, st_messages: StMessages
) -> None:
    """PDF が 1 件もなければ、何も作られず 0 が返る (要件 9.2)."""
    assert save_pdfs([], entry) == 0

    assert not entry.get_pdf_dir(pdf_root).exists()
    assert not any(pdf_root.glob("**/*"))
    assert st_messages.error == []


def test_save_pdfs_with_no_files_leaves_existing_pdfs_untouched(
    entry: Entry, pdf_root: Path, st_messages: StMessages
) -> None:
    """既存の PDF があっても、0 件の保存は何も変えない (要件 9.2)."""
    pdf_dir = entry.get_pdf_dir(pdf_root)
    existing = make_pdfs(pdf_dir, ["00.pdf", "01.pdf"])

    assert save_pdfs([], entry) == 0

    assert pdf_names(pdf_dir) == ["00.pdf", "01.pdf"]
    for name, content in existing.items():
        assert (pdf_dir / name).read_bytes() == content
    assert st_messages.error == []


@pytest.mark.parametrize(
    ("path_length", "expected_saved"),
    [
        pytest.param(MAX_PATH_LENGTH, 2, id="250-characters-is-allowed"),
        pytest.param(MAX_PATH_LENGTH + 1, 0, id="251-characters-is-skipped"),
    ],
)
def test_save_pdfs_path_length_limit_is_250_characters(
    entry: Entry,
    pdf_root: Path,
    st_messages: StMessages,
    path_length: int,
    expected_saved: int,
) -> None:
    """保存パスが 250 文字までは保存され、251 文字では 1 件ずつ飛ばされる (要件 9.3).

    基点の長さを調整して、``00.pdf`` と ``01.pdf`` の保存パスをちょうど
    ``path_length`` 文字にする。巨大なタイトルのエントリは作らない。
    """
    base_dir = padded_base_dir(pdf_root, entry, "00.pdf", path_length)
    uploads = make_uploads(2)

    saved = save_pdfs(uploads, entry, base_dir)

    pdf_dir = entry.get_pdf_dir(base_dir)
    assert saved == expected_saved
    assert len(pdf_names(pdf_dir)) == expected_saved
    if expected_saved:
        assert st_messages.error == []
        assert (pdf_dir / "01.pdf").read_bytes() == uploads[1].getvalue()
    else:
        # 飛ばしたファイルごとに、パスと長さを示すエラーが表示される。
        # 飛ばしたファイルは保存されない (番号が埋まらない) ので、どちらも
        # 00.pdf の保存パスとして通知される
        assert len(st_messages.error) == len(uploads)
        for message in st_messages.error:
            assert "too long" in message
            assert f"({path_length} characters)" in message
            assert str(pdf_dir / "00.pdf") in message


def test_save_pdfs_skips_a_file_over_the_limit_and_keeps_processing(
    entry: Entry, pdf_root: Path, st_messages: StMessages
) -> None:
    """上限を超えたファイルは飛ばされ、その前後の処理は続く (要件 9.3).

    保存名は番号が増えるほど長くなる。``99.pdf`` までは 250 文字に収まり、
    ``100.pdf`` から 251 文字になる。既存の ``00.pdf``〜``98.pdf`` がある
    状態で 3 件保存すると、1 件目は ``99.pdf`` として保存され、2 件目以降は
    上限を超えて飛ばされる。飛ばした 2 件目で処理が中断されず、3 件目も
    処理される (エラーが 1 件ずつ記録される)。
    """
    base_dir = padded_base_dir(pdf_root, entry, "99.pdf", MAX_PATH_LENGTH)
    pdf_dir = entry.get_pdf_dir(base_dir)
    existing = make_pdfs(pdf_dir, [f"{i:02d}.pdf" for i in range(99)])
    uploads = make_uploads(3)

    saved = save_pdfs(uploads, entry, base_dir)

    assert saved == 1
    assert (pdf_dir / "99.pdf").read_bytes() == uploads[0].getvalue()
    assert not (pdf_dir / "100.pdf").exists()
    assert len(pdf_names(pdf_dir)) == len(existing) + 1
    # 2 件目と 3 件目が、それぞれ上限超過として通知される
    assert len(st_messages.error) == 2
    for message in st_messages.error:
        assert "too long" in message
        assert f"({MAX_PATH_LENGTH + 1} characters)" in message
        assert str(pdf_dir / "100.pdf") in message


# --- PDF の削除: delete_pdfs (要件 9.4, 9.5) -------------------------------


def test_delete_pdfs_specific_files_removes_only_those(
    entry: Entry, pdf_root: Path, st_messages: StMessages
) -> None:
    """指定したファイルだけが削除され、ほかの PDF とフォルダは残る (要件 9.4)."""
    pdf_dir = entry.get_pdf_dir(pdf_root)
    contents = make_pdfs(pdf_dir, ["00.pdf", "01.pdf", "02.pdf"])

    deleted = delete_pdfs(entry, specific_files=[pdf_dir / "01.pdf"])

    assert deleted == 1
    assert pdf_names(pdf_dir) == ["00.pdf", "02.pdf"]
    assert (pdf_dir / "00.pdf").read_bytes() == contents["00.pdf"]
    assert (pdf_dir / "02.pdf").read_bytes() == contents["02.pdf"]
    assert st_messages.error == []


def test_delete_pdfs_specific_files_removes_several_files(
    entry: Entry, pdf_root: Path, st_messages: StMessages
) -> None:
    """複数のファイルを指定すると、その件数が返り、その分だけ削除される (要件 9.4)."""
    pdf_dir = entry.get_pdf_dir(pdf_root)
    contents = make_pdfs(pdf_dir, ["00.pdf", "01.pdf", "02.pdf"])

    deleted = delete_pdfs(
        entry, specific_files=[pdf_dir / "00.pdf", pdf_dir / "02.pdf"]
    )

    assert deleted == 2
    assert pdf_names(pdf_dir) == ["01.pdf"]
    assert (pdf_dir / "01.pdf").read_bytes() == contents["01.pdf"]
    assert st_messages.error == []


def test_delete_pdfs_specific_files_removes_the_folder_when_empty(
    entry: Entry, pdf_root: Path, st_messages: StMessages
) -> None:
    """最後の PDF を削除してフォルダが空になると、フォルダも削除される (要件 9.4)."""
    pdf_dir = entry.get_pdf_dir(pdf_root)
    make_pdfs(pdf_dir, ["00.pdf", "01.pdf"])

    first = delete_pdfs(entry, specific_files=[pdf_dir / "00.pdf"])
    assert first == 1
    assert pdf_dir.is_dir()  # まだ PDF が残るのでフォルダは残る

    second = delete_pdfs(entry, specific_files=[pdf_dir / "01.pdf"])

    assert second == 1
    assert not pdf_dir.exists()
    assert pdf_root.is_dir()  # PDF ディレクトリ自体は消えない
    assert st_messages.error == []


def test_delete_pdfs_specific_files_keeps_a_folder_that_is_not_empty(
    entry: Entry, pdf_root: Path, st_messages: StMessages
) -> None:
    """PDF を全部消しても、ほかのファイルが残るフォルダは削除されない (要件 9.4)."""
    pdf_dir = entry.get_pdf_dir(pdf_root)
    make_pdfs(pdf_dir, ["00.pdf"])
    notes = pdf_dir / "notes.txt"
    notes.write_text("keep me", encoding="utf-8")

    deleted = delete_pdfs(entry, specific_files=[pdf_dir / "00.pdf"])

    assert deleted == 1
    assert pdf_names(pdf_dir) == ["notes.txt"]
    assert notes.read_text(encoding="utf-8") == "keep me"
    assert st_messages.error == []


def test_delete_pdfs_specific_files_ignores_files_that_do_not_exist(
    entry: Entry, pdf_root: Path, st_messages: StMessages
) -> None:
    """存在しないファイルを指定しても、数えず、エラーにもならない (要件 9.4)."""
    pdf_dir = entry.get_pdf_dir(pdf_root)
    contents = make_pdfs(pdf_dir, ["00.pdf", "01.pdf"])

    deleted = delete_pdfs(
        entry, specific_files=[pdf_dir / "05.pdf", pdf_dir / "01.pdf"]
    )

    assert deleted == 1
    assert pdf_names(pdf_dir) == ["00.pdf"]
    assert (pdf_dir / "00.pdf").read_bytes() == contents["00.pdf"]
    assert st_messages.error == []


def test_delete_pdfs_removes_the_whole_folder_and_returns_count(
    entry: Entry, other_entry: Entry, pdf_root: Path, st_messages: StMessages
) -> None:
    """全 PDF の削除では、フォルダごと削除されて件数が返る (要件 9.5).

    別のエントリのフォルダは影響を受けない。
    """
    pdf_dir = entry.get_pdf_dir(pdf_root)
    make_pdfs(pdf_dir, ["00.pdf", "01.pdf", "02.pdf"])
    other_dir = other_entry.get_pdf_dir(pdf_root)
    other = make_pdfs(other_dir, ["00.pdf"])

    deleted = delete_pdfs(entry)

    assert deleted == 3
    assert not pdf_dir.exists()
    assert pdf_names(pdf_root) == [other_entry.pdf_dir_name]
    assert (other_dir / "00.pdf").read_bytes() == other["00.pdf"]
    assert st_messages.error == []


def test_delete_pdfs_removes_the_folder_under_the_given_base_dir(
    entry: Entry, pdf_root: Path, tmp_path: Path, st_messages: StMessages
) -> None:
    """``base_dir`` を指定すると、その下のフォルダだけが削除される (要件 9.5)."""
    base_dir = tmp_path / "elsewhere"
    target_dir = entry.get_pdf_dir(base_dir)
    make_pdfs(target_dir, ["00.pdf", "01.pdf"])
    default_dir = entry.get_pdf_dir(pdf_root)
    default = make_pdfs(default_dir, ["00.pdf"])

    deleted = delete_pdfs(entry, base_dir)

    assert deleted == 2
    assert not target_dir.exists()
    assert (default_dir / "00.pdf").read_bytes() == default["00.pdf"]
    assert st_messages.error == []


def test_delete_pdfs_removes_a_legacy_single_pdf(
    entry: Entry, other_entry: Entry, pdf_root: Path, st_messages: StMessages
) -> None:
    """フォルダがなく旧形式の単一 PDF だけなら、そのファイルが削除される (要件 9.5).

    旧形式の PDF は PDF ディレクトリ直下の ``<pdf_dir_name>.pdf``。
    別のエントリの旧形式 PDF は削除されない。
    """
    legacy = write_pdf(pdf_root / f"{entry.pdf_dir_name}.pdf")
    other_legacy_content = PDF_BYTES + b"%other legacy\n"
    other_legacy = write_pdf(
        pdf_root / f"{other_entry.pdf_dir_name}.pdf", other_legacy_content
    )
    assert not entry.get_pdf_dir(pdf_root).exists()  # 前提: フォルダは無い

    deleted = delete_pdfs(entry)

    assert deleted == 1
    assert not legacy.exists()
    assert other_legacy.read_bytes() == other_legacy_content
    assert st_messages.error == []


def test_delete_pdfs_with_nothing_to_delete_returns_zero(
    entry: Entry, other_entry: Entry, pdf_root: Path, st_messages: StMessages
) -> None:
    """削除する PDF がなければ 0 が返り、何も変わらない (要件 9.5)."""
    other_dir = other_entry.get_pdf_dir(pdf_root)
    other = make_pdfs(other_dir, ["00.pdf"])

    assert delete_pdfs(entry) == 0

    assert pdf_names(pdf_root) == [other_entry.pdf_dir_name]
    assert (other_dir / "00.pdf").read_bytes() == other["00.pdf"]
    assert st_messages.error == []


# --- 論文の登録・更新: 補助 -----------------------------------------------
# 登録・更新の関数は、画面が持つ論文リスト (``PaperList``) を受け取って変更し、
# ``list.json`` と session state の両方へ書き出す (永続化の単一経路)。そのため
# 結果は 3 か所 (受け取った論文リスト・``list.json``・session state) で確かめる。
# 前提となる論文リストは、画面と同じく ``list.json`` と session state に保存
# したうえで、session state から読み直した別オブジェクトを関数へ渡す。

EntryFields = dict[str, str]


class ListState(NamedTuple):
    """論文リストの保管場所ごとの内容 (``read_state`` が返す).

    ``held`` は関数に渡した論文リスト、``file`` は ``list.json`` を読み直した
    結果、``session`` は session state の論文リスト。いずれも「キー → エント
    リのフィールド」の辞書。``file_bytes`` は ``list.json`` のバイト列
    (ファイルがなければ ``None``) で、ファイルが書き直されたかどうかの確認
    に使う。
    """

    held: dict[str, EntryFields]
    file: dict[str, EntryFields]
    session: dict[str, EntryFields]
    file_bytes: Optional[bytes]


class Stored(NamedTuple):
    """``stored`` fixture が用意した、保存済みの論文リストと PDF."""

    paper_list: PaperList  # 画面が持つのと同じ、session state から読んだ論文リスト
    key: str  # ``entry`` のキー
    other_key: str  # ``other_entry`` のキー
    pdfs: dict[str, bytes]  # ``entry`` の PDF (ファイル名 → 中身)
    other_pdfs: dict[str, bytes]  # ``other_entry`` の PDF


def as_fields(paper_list: Mapping[str, Entry]) -> dict[str, EntryFields]:
    """論文リストを「キー → エントリのフィールド」の辞書にして返す."""
    return {key: dict(entry) for key, entry in paper_list.items()}


def read_state(held: PaperList, data_paths: DataPaths) -> ListState:
    """論文リストの 3 か所の現在の内容を読む.

    Parameters
    ----------
    held : PaperList
        関数に渡した論文リスト。
    data_paths : DataPaths
        ``list.json`` の場所。

    Returns
    -------
    ListState
        ``list.json`` は読み直し、session state は bare mode の
        ``st.session_state`` から読む。
    """
    list_file = data_paths.list_file
    return ListState(
        held=as_fields(held),
        file=as_fields(PaperList.from_file(list_file)),
        session=as_fields(PaperList.from_session_state()),
        file_bytes=list_file.read_bytes() if list_file.is_file() else None,
    )


def assert_list_is(state: ListState, expected: dict[str, EntryFields]) -> None:
    """論文リストの 3 か所が、すべて ``expected`` と一致することを確かめる."""
    assert state.file == expected, "list.json"
    assert state.session == expected, "session state"
    assert state.held == expected, "the paper list passed to the function"


def seed_paper_list(
    data_paths: DataPaths, *entries: Entry
) -> tuple[PaperList, tuple[str, ...]]:
    """``entries`` を保存済みにして、画面が持つ論文リストを返す.

    各エントリには ``register_entry_to_list`` と同じ規則で識別子を付け、
    ``list.json`` と session state に保存する。渡した ``entries`` 自体は
    変更しない。

    Parameters
    ----------
    data_paths : DataPaths
        ``list.json`` の保存先。
    *entries : Entry
        保存済みにするエントリ。なくてもよい。

    Returns
    -------
    tuple[PaperList, tuple[str, ...]]
        session state から読み直した論文リスト (保存した内容とは別の
        オブジェクト) と、``entries`` と同じ順のキー。
    """
    seeded = PaperList({})
    keys = []
    for entry in entries:
        key = entry.get_key(seeded.keys())
        seeded[key] = Entry({**entry, "ID": key})
        keys.append(key)
    seeded.to_file(data_paths.list_file)
    seeded.to_session_state()
    return PaperList.from_session_state(), tuple(keys)


def edited(entry: Entry, **changes: str) -> Entry:
    """``entry`` を複製して ``changes`` を反映した、更新後のエントリを返す.

    編集画面と同じく、元のエントリは変更しない。
    """
    updated = deepcopy(entry)
    updated.update(changes)
    return updated


def pdf_tree(pdf_root: Path) -> dict[str, Optional[bytes]]:
    """PDF ディレクトリの配下を「相対パス → 中身」の辞書にして返す.

    フォルダの中身は ``None``。空のフォルダも含むので、フォルダの有無と
    ファイルの中身の両方を比べられる。
    """
    return {
        path.relative_to(pdf_root).as_posix(): (
            path.read_bytes() if path.is_file() else None
        )
        for path in sorted(pdf_root.rglob("*"))
    }


@pytest.fixture
def stored(
    data_paths: DataPaths, pdf_root: Path, entry: Entry, other_entry: Entry
) -> Stored:
    """``entry`` と ``other_entry`` が保存済みの論文リストを用意する.

    キーは ``AliceSmith2020_0`` と ``BobJones2019_0``。PDF は ``entry`` が
    ``00.pdf`` と ``01.pdf``、``other_entry`` が ``00.pdf`` (中身はすべて
    異なる)。
    """
    paper_list, (key, other_key) = seed_paper_list(
        data_paths, entry, other_entry
    )
    assert (key, other_key) == ("AliceSmith2020_0", "BobJones2019_0")
    return Stored(
        paper_list=paper_list,
        key=key,
        other_key=other_key,
        pdfs=make_pdfs(entry.get_pdf_dir(pdf_root), ["00.pdf", "01.pdf"]),
        other_pdfs=make_pdfs(other_entry.get_pdf_dir(pdf_root), ["00.pdf"]),
    )


# --- 論文の登録: register_entry_to_list (要件 9.6, 9.7) --------------------


def test_register_entry_assigns_an_id_and_adds_it_to_the_list(
    data_paths: DataPaths, entry: Entry, st_messages: StMessages
) -> None:
    """登録すると識別子が付き、論文リストに加わる (要件 9.6).

    識別子は ``<最初の著者(空白なし)><年>_<添字>`` で、エントリの ``ID``
    フィールドであり、論文リストのキーでもある。
    """
    paper_list, _ = seed_paper_list(data_paths)

    registered = register_entry_to_list(paper_list, entry)

    assert registered is True
    assert entry["ID"] == "AliceSmith2020_0"
    assert list(paper_list) == ["AliceSmith2020_0"]
    assert paper_list["AliceSmith2020_0"] is entry
    assert st_messages.error == []
    assert len(st_messages.success) == 1  # 成功の通知が 1 回だけ出る


def test_register_entry_is_written_to_both_the_file_and_session_state(
    data_paths: DataPaths,
    entry: Entry,
    other_entry: Entry,
    st_messages: StMessages,
) -> None:
    """登録した論文は、``list.json`` と session state の両方に反映される (要件 9.6).

    すでに保存されているエントリは、どちらでもそのまま残る。
    """
    paper_list, (other_key,) = seed_paper_list(data_paths, other_entry)
    before = read_state(paper_list, data_paths)
    assert before.file == before.session == before.held  # 前提
    assert list(before.file) == [other_key]
    fields = dict(entry)

    assert register_entry_to_list(paper_list, entry) is True

    expected = {
        **before.file,
        "AliceSmith2020_0": {**fields, "ID": "AliceSmith2020_0"},
    }
    assert_list_is(read_state(paper_list, data_paths), expected)
    assert st_messages.error == []


def test_register_entry_gives_each_entry_a_unique_id(
    data_paths: DataPaths,
    make_entry: Callable[..., Entry],
    st_messages: StMessages,
) -> None:
    """著者と年が同じでもフォルダ名が違う論文は、別の識別子で登録できる (要件 9.6).

    すでに ``AliceSmith2020_0`` がある状態から 2 件続けて登録すると、
    添字が ``_1``、``_2`` と進んで、既存のキーと重複しない。
    """
    paper_list, (existing_key,) = seed_paper_list(
        data_paths, make_entry(title="An Existing Study")
    )
    assert existing_key == "AliceSmith2020_0"
    second = make_entry(title="A Second Study")
    third = make_entry(title="A Third Study")

    assert register_entry_to_list(paper_list, second) is True
    assert register_entry_to_list(paper_list, third) is True

    assert second["ID"] == "AliceSmith2020_1"
    assert third["ID"] == "AliceSmith2020_2"
    state = read_state(paper_list, data_paths)
    assert sorted(state.file) == [
        "AliceSmith2020_0",
        "AliceSmith2020_1",
        "AliceSmith2020_2",
    ]
    assert state.file["AliceSmith2020_1"]["title"] == "A Second Study"
    assert state.file["AliceSmith2020_2"]["title"] == "A Third Study"
    assert state.session == state.held == state.file
    assert st_messages.error == []


def test_register_entry_saves_the_attached_pdfs(
    data_paths: DataPaths,
    pdf_root: Path,
    entry: Entry,
    st_messages: StMessages,
) -> None:
    """添付した PDF が、エントリのフォルダに 00.pdf から連番で保存される (要件 9.6)."""
    paper_list, _ = seed_paper_list(data_paths)
    uploads = make_uploads(2)

    assert register_entry_to_list(paper_list, entry, uploads) is True

    pdf_dir = pdf_root / "2020 - Alice Smith - A Study of Testing"
    assert pdf_names(pdf_root) == [pdf_dir.name]
    assert pdf_names(pdf_dir) == ["00.pdf", "01.pdf"]
    for index, upload in enumerate(uploads):
        saved_file = pdf_dir / f"{index:02d}.pdf"
        assert saved_file.read_bytes() == upload.getvalue()
    assert entry.get_pdf_dir(pdf_root) == pdf_dir
    assert "AliceSmith2020_0" in read_state(paper_list, data_paths).file
    assert st_messages.error == []


@pytest.mark.parametrize(
    "uploads",
    [pytest.param(None, id="none"), pytest.param([], id="empty-list")],
)
def test_register_entry_without_pdfs_creates_no_pdf_folder(
    data_paths: DataPaths,
    pdf_root: Path,
    entry: Entry,
    st_messages: StMessages,
    uploads: Optional[list[FakeUpload]],
) -> None:
    """PDF を添付しない登録でも論文は登録され、PDF のフォルダは作られない (要件 9.6)."""
    paper_list, _ = seed_paper_list(data_paths)

    assert register_entry_to_list(paper_list, entry, uploads) is True

    state = read_state(paper_list, data_paths)
    assert list(state.file) == ["AliceSmith2020_0"]
    assert not any(pdf_root.iterdir())
    assert st_messages.error == []


def test_register_entry_rejects_a_duplicate_folder_name(
    data_paths: DataPaths,
    pdf_root: Path,
    entry: Entry,
    make_entry: Callable[..., Entry],
    st_messages: StMessages,
) -> None:
    """PDF フォルダ名が既存のエントリと同じ登録は拒否される (要件 9.7).

    重複は識別子でなくフォルダ名 (年・最初の著者・タイトル) で判定するので、
    ``journal`` などほかのフィールドが違っていても拒否される。重複エラーが
    通知され、論文リストも PDF も変わらない (添付した PDF は保存されない)。
    """
    paper_list, (key,) = seed_paper_list(data_paths, entry)
    make_pdfs(entry.get_pdf_dir(pdf_root), ["00.pdf"])
    duplicate = make_entry(journal="A Different Journal", volume="3")
    assert duplicate.pdf_dir_name == entry.pdf_dir_name  # 前提
    assert duplicate.get_key(paper_list.keys()) != key  # キーは重複しない
    before = read_state(paper_list, data_paths)
    pdfs_before = pdf_tree(pdf_root)

    registered = register_entry_to_list(paper_list, duplicate, make_uploads(2))

    assert registered is False
    assert len(st_messages.error) == 1
    assert "Duplicated" in st_messages.error[0]
    assert st_messages.success == []
    assert read_state(paper_list, data_paths) == before
    assert pdf_tree(pdf_root) == pdfs_before


# --- 論文の更新: update_entry_in_list (要件 9.8, 9.9, 9.10) ----------------


def test_update_without_folder_change_updates_the_entry_in_place(
    data_paths: DataPaths,
    pdf_root: Path,
    entry: Entry,
    stored: Stored,
    st_messages: StMessages,
) -> None:
    """フォルダ名が変わらない更新は、同じ識別子のまま内容だけが変わる (要件 9.8).

    ``journal`` の変更と ``volume`` の追加はフォルダ名に影響しない。PDF は
    動かず、ほかのエントリも変わらない。
    """
    updated = edited(
        stored.paper_list[stored.key],
        journal="Journal of Updated Results",
        volume="7",
    )
    assert updated.pdf_dir_name == entry.pdf_dir_name  # 前提
    before = read_state(stored.paper_list, data_paths)
    pdfs_before = pdf_tree(pdf_root)

    result = update_entry_in_list(stored.paper_list, stored.key, updated)

    assert result is True
    expected = {
        **before.file,
        stored.key: {
            **before.file[stored.key],
            "journal": "Journal of Updated Results",
            "volume": "7",
        },
    }
    assert_list_is(read_state(stored.paper_list, data_paths), expected)
    assert expected[stored.key]["ID"] == stored.key
    assert pdf_tree(pdf_root) == pdfs_before
    assert st_messages.error == []


def test_update_with_folder_change_renames_the_pdf_folder(
    pdf_root: Path,
    entry: Entry,
    other_entry: Entry,
    stored: Stored,
    st_messages: StMessages,
) -> None:
    """フォルダ名が変わる更新は、PDF のフォルダを新しい名前へ改名する (要件 9.9).

    中の PDF は番号も中身もそのまま保たれ、古い名前のフォルダは残らない。
    ほかのエントリのフォルダは変わらない。
    """
    updated = edited(
        stored.paper_list[stored.key], title="A Revised Study of Testing"
    )
    old_dir = entry.get_pdf_dir(pdf_root)
    new_dir = updated.get_pdf_dir(pdf_root)
    assert new_dir.name == "2020 - Alice Smith - A Revised Study of Testing"
    assert old_dir != new_dir  # 前提

    result = update_entry_in_list(stored.paper_list, stored.key, updated)

    assert result is True
    assert not old_dir.exists()
    assert pdf_names(new_dir) == ["00.pdf", "01.pdf"]
    for name, content in stored.pdfs.items():
        assert (new_dir / name).read_bytes() == content
    assert pdf_names(pdf_root) == sorted(
        [new_dir.name, other_entry.pdf_dir_name]
    )
    other_dir = other_entry.get_pdf_dir(pdf_root)
    assert pdf_names(other_dir) == ["00.pdf"]
    assert (other_dir / "00.pdf").read_bytes() == stored.other_pdfs["00.pdf"]
    assert st_messages.error == []


@pytest.mark.parametrize(
    ("changes", "new_key"),
    [
        # 元のエントリが残っている状態で採番するので、著者と年が同じなら
        # 添字が進む (``_0`` は再利用されない)
        pytest.param(
            {"title": "A Revised Study of Testing"},
            "AliceSmith2020_1",
            id="title-only",
        ),
        pytest.param(
            {"author": "Carol White"}, "CarolWhite2020_0", id="author"
        ),
        pytest.param({"year": "2021"}, "AliceSmith2021_0", id="year"),
        # 別のエントリ (``BobJones2019_0``) が使っているキーは避ける
        pytest.param(
            {"author": "Bob Jones", "year": "2019"},
            "BobJones2019_1",
            id="key-taken-by-another-entry",
        ),
    ],
)
def test_update_with_folder_change_registers_the_entry_under_a_new_id(
    data_paths: DataPaths,
    entry: Entry,
    stored: Stored,
    st_messages: StMessages,
    changes: dict[str, str],
    new_key: str,
) -> None:
    """フォルダ名が変わる更新は、新しい識別子で登録し直す (要件 9.9).

    古いキーは論文リストから消え、``ID`` フィールドは新しいキーになる。
    ``list.json`` と session state の両方に反映され、ほかのエントリは
    変わらない。
    """
    updated = edited(stored.paper_list[stored.key], **changes)
    assert updated.pdf_dir_name != entry.pdf_dir_name  # 前提
    before = read_state(stored.paper_list, data_paths)

    result = update_entry_in_list(stored.paper_list, stored.key, updated)

    assert result is True
    assert updated["ID"] == new_key
    expected = {
        stored.other_key: before.file[stored.other_key],
        new_key: {**before.file[stored.key], **changes, "ID": new_key},
    }
    assert_list_is(read_state(stored.paper_list, data_paths), expected)
    assert stored.key not in expected
    assert st_messages.error == []


def test_update_with_folder_change_works_without_a_pdf_folder(
    data_paths: DataPaths,
    pdf_root: Path,
    entry: Entry,
    other_entry: Entry,
    st_messages: StMessages,
) -> None:
    """PDF のフォルダがないエントリでも、フォルダ名が変わる更新ができる (要件 9.9).

    改名するフォルダがないので何も作られず、識別子だけが新しくなる。
    """
    paper_list, (key, other_key) = seed_paper_list(
        data_paths, entry, other_entry
    )
    updated = edited(paper_list[key], title="A Revised Study of Testing")
    assert not any(pdf_root.iterdir())  # 前提: PDF はどこにもない

    result = update_entry_in_list(paper_list, key, updated)

    assert result is True
    state = read_state(paper_list, data_paths)
    assert sorted(state.file) == sorted([other_key, "AliceSmith2020_1"])
    assert state.file["AliceSmith2020_1"]["title"] == updated["title"]
    assert state.session == state.held == state.file
    assert not any(pdf_root.iterdir())
    assert st_messages.error == []


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param(
            {"journal": "Journal of Updated Results"}, id="same-folder"
        ),
        pytest.param(
            {"title": "A Revised Study of Testing"}, id="renamed-folder"
        ),
    ],
)
def test_update_saves_new_uploads_after_the_existing_pdfs(
    pdf_root: Path,
    stored: Stored,
    st_messages: StMessages,
    changes: dict[str, str],
) -> None:
    """更新で添付した PDF は、更新後のフォルダの既存の PDF の後ろに保存される.

    フォルダ名が変わる場合は、改名後のフォルダに保存され、既存の PDF は
    上書きされない (要件 9.8, 9.9)。
    """
    updated = edited(stored.paper_list[stored.key], **changes)
    new_dir = updated.get_pdf_dir(pdf_root)
    uploads = make_uploads(1)

    result = update_entry_in_list(
        stored.paper_list, stored.key, updated, uploads
    )

    assert result is True
    assert pdf_names(new_dir) == ["00.pdf", "01.pdf", "02.pdf"]
    for name, content in stored.pdfs.items():
        assert (new_dir / name).read_bytes() == content
    assert (new_dir / "02.pdf").read_bytes() == uploads[0].getvalue()
    assert st_messages.error == []


def test_update_rejects_a_folder_name_used_by_another_entry(
    data_paths: DataPaths,
    pdf_root: Path,
    entry: Entry,
    other_entry: Entry,
    stored: Stored,
    st_messages: StMessages,
) -> None:
    """更新後のフォルダ名がほかのエントリと同じ更新は、拒否される (要件 9.10).

    重複エラーにフォルダ名が示され、論文リストも PDF も変わらない
    (添付した PDF も保存されない)。
    """
    updated = edited(
        stored.paper_list[stored.key],
        author="Bob Jones",
        title="Quantum Widgets",
        year="2019",
    )
    assert updated.pdf_dir_name == other_entry.pdf_dir_name  # 前提
    assert updated.pdf_dir_name != entry.pdf_dir_name  # 前提
    before = read_state(stored.paper_list, data_paths)
    pdfs_before = pdf_tree(pdf_root)

    result = update_entry_in_list(
        stored.paper_list, stored.key, updated, make_uploads(1)
    )

    assert result is False
    assert len(st_messages.error) == 1
    assert "Duplicated" in st_messages.error[0]
    assert other_entry.pdf_dir_name in st_messages.error[0]
    assert read_state(stored.paper_list, data_paths) == before
    assert pdf_tree(pdf_root) == pdfs_before


def test_update_rejects_when_the_destination_folder_already_exists(
    data_paths: DataPaths,
    pdf_root: Path,
    stored: Stored,
    st_messages: StMessages,
) -> None:
    """改名先のフォルダが既にある更新は、拒否される (要件 9.10).

    改名先の名前は、どのエントリのフォルダ名とも重複しない。ただし、
    その名前のフォルダ (どのエントリにも紐づかない PDF) が既にある。
    エラーが通知され、論文リストも元のフォルダも改名先の既存のフォルダも
    変わらない (添付した PDF も保存されない)。
    """
    updated = edited(
        stored.paper_list[stored.key], title="A Revised Study of Testing"
    )
    existing_dir = updated.get_pdf_dir(pdf_root)
    existing = make_pdfs(existing_dir, ["00.pdf"])
    registered_names = {
        registered.pdf_dir_name for registered in stored.paper_list.values()
    }
    assert updated.pdf_dir_name not in registered_names  # 前提
    before = read_state(stored.paper_list, data_paths)
    pdfs_before = pdf_tree(pdf_root)

    result = update_entry_in_list(
        stored.paper_list, stored.key, updated, make_uploads(1)
    )

    assert result is False
    assert len(st_messages.error) == 1
    assert "already exists" in st_messages.error[0]
    assert read_state(stored.paper_list, data_paths) == before
    assert pdf_tree(pdf_root) == pdfs_before
    assert (existing_dir / "00.pdf").read_bytes() == existing["00.pdf"]
