"""components (``app.components._common``) の単体テスト.

画面から呼ばれるファイル操作と論文リスト操作を、関数を直接呼んで検証する。
このモジュールは次のセクションで構成される。

- 共通の補助: fixture と補助関数 (後続のセクションからも使う)
- PDF の保存: ``save_pdfs`` (要件 9.1, 9.2, 9.3)
- PDF の削除: ``delete_pdfs`` (要件 9.4, 9.5)

論文の登録・更新 (要件 9.6〜9.10) と入力フォーム部品 (要件 9.11, 9.12) の
セクションは、このあとに追記する。追記するセクションが使う補助は
「共通の補助」に加えるか、そのセクションの冒頭に置く。

保存・削除は ``data_paths.pdf`` (隔離された一時ディレクトリの配下) か、
``tmp_path`` の中だけで行い、実データには触れない。

PDF の置き場所は ``Entry.get_pdf_dir`` から導出する。通知 (``st.error``)
は ``st_messages`` で記録して検証する。``st_messages`` は ``AppTest`` と
併用できないので、画面テストを追加するときは別のテスト関数にする。

旧形式の単一 PDF の削除は ``Entry.pdf_filename`` (非推奨) を参照するため、
``DeprecationWarning`` が出る。警告の確認が目的ではないので、この 2 つの
非推奨 API の警告だけを無視する (ほかの警告は隠さない)。
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import PDF_BYTES, DataPaths, FakeUpload, StMessages, write_pdf

from paper_manager.app.components import _common
from paper_manager.app.components._common import delete_pdfs, save_pdfs
from paper_manager.entry import Entry

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
