"""孤立 PDF の検出・削除 (``app.helper._orphan_pdf``) の単体テスト.

孤立 PDF とは、論文リストのどのエントリにも紐づかない PDF のこと。
次の 2 つの形式がある。

- 新形式: PDF ディレクトリ直下の、エントリに対応しないフォルダ
  (``<pdf_dir_name>``) の中の PDF
- 旧形式: PDF ディレクトリ直下の、エントリに対応しない単一の
  ``<名前>.pdf``

対象は次のとおり。

- フォルダ内の孤立 PDF と旧形式の孤立 PDF が検出され、論文リストに紐づく
  PDF は検出されないこと。PDF ディレクトリがなければ 0 件であること
  (要件 8.1, 8.2)
- 孤立 PDF に最も近いエントリの類似度が 0.0〜1.0 の範囲で、フォルダ名が
  完全に一致すれば 1.0 であること。論文リストが空なら該当なし (``None``)
  であること (要件 8.3)
- 削除したファイル数が返り、空になったフォルダが削除され、PDF ディレクトリ
  自体は残ること (要件 8.4)
- 一部の削除に失敗しても、残りの削除が続行されること (要件 8.5)
- 既知バグ KB4d: 孤立 PDF の削除にフォルダを渡すと、削除件数が 0 と報告
  されること (要件 13.1)

削除は ``data_paths.pdf`` (隔離された一時ディレクトリの配下) の中だけで
行い、実データには触れない。

``PaperList`` を作ると PDF ディレクトリが作られる (``PaperList.__init__`` が
``DIRPATH_PDF`` を作成する)。そのため「PDF ディレクトリがない」状態は、
論文リストを作ったあとでディレクトリを削除して作る。

検出 (``find_orphaned_pdfs``) は各エントリの ``get_pdf_files`` を呼び、
旧形式の移行の ``DeprecationWarning`` が出る。警告の確認が目的ではないので
このモジュールでは無視する。
"""

import errno
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pytest
from conftest import PDF_BYTES, DataPaths, SampleData, write_pdf

from paper_manager.app.helper import _orphan_pdf
from paper_manager.app.helper._orphan_pdf import (
    _calculate_similarity,
    delete_orphaned_pdfs,
    find_closest_entry_for_orphaned_pdf,
    find_orphaned_pdfs,
)
from paper_manager.entry import Entry, PaperList

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

# 論文リストに無いエントリのフォルダ名 (孤立フォルダ用).
ORPHAN_DIR_NAME = "2001 - Nobody - Unregistered Paper"
# 論文リストに無い旧形式の単一 PDF のファイル名.
ORPHAN_LEGACY_NAME = "Unregistered Legacy Paper.pdf"


# --- 補助 -----------------------------------------------------------------


def make_paper_list(*entries: Entry) -> PaperList:
    """``entries`` を識別子付きで登録した論文リストを作る."""
    paper_list = PaperList({})
    for entry in entries:
        key = entry.get_key(paper_list.keys())
        entry["ID"] = key
        paper_list[key] = entry
    return paper_list


def pdf_in_folder(root: Path, name: str) -> Path:
    """``root/<name>/00.pdf`` を作る (新形式の PDF)."""
    return write_pdf(root / name / "00.pdf")


def bare_folder(root: Path, name: str) -> Path:
    """``root/<name>`` のフォルダを作る."""
    folder = root / name
    folder.mkdir(parents=True)
    return folder


def legacy_pdf(root: Path, name: str) -> Path:
    """``root/<name>.pdf`` を作る (旧形式の単一 PDF)."""
    return write_pdf(root / f"{name}.pdf")


def fail_unlink_of(monkeypatch: pytest.MonkeyPatch, target: Path) -> None:
    """``target`` の ``Path.unlink`` だけが ``OSError`` で失敗するようにする.

    ほかのパスは本来の ``unlink`` に通す。差し替えはテストの後に元へ戻る。
    権限の変更 (chmod) は環境 (root など) によって効かないので使わない。
    """
    original = Path.unlink

    def unlink(self: Path, *args: Any, **kwargs: Any) -> None:
        if self == target:
            raise PermissionError(
                errno.EACCES, "permission denied (simulated)", str(self)
            )
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)


def pdf_contents(paths: Iterable[Path]) -> dict[Path, bytes]:
    """各ファイルの中身を取る (削除前後で比べて取り違えを検出する)."""
    return {path: path.read_bytes() for path in paths}


@pytest.fixture
def pdf_root(data_paths: DataPaths) -> Path:
    """PDF ディレクトリ. 実装が参照する定数と一致することを確かめる.

    削除系のテストは、このディレクトリの配下 (隔離された一時ディレクトリの
    中) だけを操作する。
    """
    assert _orphan_pdf.DIRPATH_PDF == data_paths.pdf
    return data_paths.pdf


@pytest.fixture
def alice(make_entry: Callable[..., Entry]) -> Entry:
    """既定値のエントリ (``2020 - Alice Smith - A Study of Testing``)."""
    return make_entry()


@pytest.fixture
def bob(make_entry: Callable[..., Entry]) -> Entry:
    """``alice`` と似ていないエントリ."""
    return make_entry(author="Bob Jones", title="Quantum Widgets", year="2019")


@pytest.fixture
def paper_list(alice: Entry, bob: Entry) -> PaperList:
    """``alice`` と ``bob`` を登録した論文リスト (PDF は無い)."""
    return make_paper_list(alice, bob)


# --- 検出: find_orphaned_pdfs (要件 8.1, 8.2) -------------------------------


def test_find_returns_only_the_unlinked_pdf_of_the_sample_data(
    sample_data: SampleData,
) -> None:
    """紐づく PDF は検出されず、紐づかない PDF だけが検出される (要件 8.1)."""
    entry = sample_data.paper_list[sample_data.with_pdf_key]
    linked = entry.get_pdf_files()
    assert len(linked) == 2  # 前提: 紐づく PDF が 2 件ある

    orphans = find_orphaned_pdfs(sample_data.paper_list)

    assert orphans == [sample_data.orphan_pdf]
    assert set(linked).isdisjoint(orphans)
    assert sample_data.orphan_pdf.is_file()  # 検出しただけでは削除しない


def test_find_returns_every_pdf_in_a_folder_without_entry(
    alice: Entry, paper_list: PaperList, pdf_root: Path
) -> None:
    """エントリに対応しないフォルダ内の PDF が、すべて検出される (要件 8.1)."""
    linked = write_pdf(pdf_root / alice.pdf_dir_name / "00.pdf")
    folder = pdf_root / ORPHAN_DIR_NAME
    write_pdf(folder / "00.pdf")
    write_pdf(folder / "01.pdf")
    (folder / "notes.txt").write_text("not a pdf", encoding="utf-8")

    orphans = find_orphaned_pdfs(paper_list)

    assert sorted(orphans) == [folder / "00.pdf", folder / "01.pdf"]
    assert linked not in orphans


def test_find_returns_a_legacy_single_pdf_without_entry(
    paper_list: PaperList, pdf_root: Path
) -> None:
    """対応しない旧形式の単一 PDF が検出される (要件 8.1)."""
    legacy = write_pdf(pdf_root / ORPHAN_LEGACY_NAME)
    (pdf_root / "readme.txt").write_text("not a pdf", encoding="utf-8")

    assert find_orphaned_pdfs(paper_list) == [legacy]


def test_find_returns_folder_and_legacy_orphans_together(
    alice: Entry, paper_list: PaperList, pdf_root: Path
) -> None:
    """新形式と旧形式の孤立 PDF が、同時にあっても両方検出される (要件 8.1)."""
    write_pdf(pdf_root / alice.pdf_dir_name / "00.pdf")  # 紐づく PDF
    in_folder = write_pdf(pdf_root / ORPHAN_DIR_NAME / "00.pdf")
    legacy = write_pdf(pdf_root / ORPHAN_LEGACY_NAME)

    orphans = find_orphaned_pdfs(paper_list)

    assert sorted(orphans) == sorted([in_folder, legacy])


def test_find_does_not_return_a_legacy_pdf_linked_to_an_entry(
    alice: Entry, paper_list: PaperList, pdf_root: Path
) -> None:
    """エントリに紐づく旧形式の PDF は検出されず、失われない (要件 8.1)."""
    content = PDF_BYTES + b"%legacy linked\n"
    write_pdf(pdf_root / alice.pdf_filename, content)
    other = write_pdf(pdf_root / ORPHAN_LEGACY_NAME)

    orphans = find_orphaned_pdfs(paper_list)

    assert orphans == [other]
    # 紐づく旧形式の PDF は (移行されて) エントリのフォルダに残る
    assert [p.read_bytes() for p in alice.get_pdf_files()] == [content]


def test_find_ignores_non_pdf_files_and_folders_without_pdf(
    paper_list: PaperList, pdf_root: Path
) -> None:
    """PDF 以外のファイルと、PDF を含まないフォルダは検出されない (要件 8.1)."""
    (pdf_root / "readme.txt").write_text("not a pdf", encoding="utf-8")
    (pdf_root / "Empty Folder").mkdir()
    notes_folder = pdf_root / "Notes Folder"
    notes_folder.mkdir()
    (notes_folder / "notes.txt").write_text("not a pdf", encoding="utf-8")

    assert find_orphaned_pdfs(paper_list) == []


def test_find_treats_every_pdf_as_orphan_for_an_empty_paper_list(
    sample_data: SampleData, pdf_root: Path
) -> None:
    """論文リストが空なら、PDF ディレクトリ内のすべての PDF が孤立 (要件 8.1)."""
    write_pdf(pdf_root / ORPHAN_LEGACY_NAME)
    expected = sorted(pdf_root.rglob("*.pdf"))
    assert len(expected) == 4  # 前提: サンプルの PDF 3 件 + 旧形式 1 件

    assert sorted(find_orphaned_pdfs(PaperList({}))) == expected


def test_find_returns_nothing_when_the_pdf_directory_does_not_exist(
    paper_list: PaperList, pdf_root: Path
) -> None:
    """PDF ディレクトリがなければ孤立 PDF は 0 件 (要件 8.2)."""
    candidates = (paper_list, PaperList({}))
    # PaperList を作ると PDF ディレクトリも作られるので、作ったあとで消す
    pdf_root.rmdir()
    assert not pdf_root.exists()

    for candidate in candidates:
        assert len(find_orphaned_pdfs(candidate)) == 0
        assert not pdf_root.exists()  # 検出でディレクトリを作らない


def test_find_returns_nothing_for_an_empty_pdf_directory(
    paper_list: PaperList, pdf_root: Path
) -> None:
    """PDF ディレクトリが空なら孤立 PDF は 0 件 (要件 8.2 の隣接ケース)."""
    pdf_root.mkdir(exist_ok=True)

    assert find_orphaned_pdfs(paper_list) == []


# --- 最も近いエントリ: find_closest_entry_for_orphaned_pdf (要件 8.3) --------

ORPHAN_FORMS = [
    pytest.param(pdf_in_folder, id="pdf-in-folder"),
    pytest.param(bare_folder, id="folder"),
    pytest.param(legacy_pdf, id="legacy-pdf"),
]

# (左, 右). 一致の仕方 (含む・先頭一致・共通単語・なし・空) を網羅する.
SIMILARITY_PAIRS = [
    pytest.param("abc", "abc", id="identical"),
    pytest.param("ABC", "abc", id="case-differs"),
    pytest.param("abc", "abcde", id="contained"),
    pytest.param("abcde", "abc", id="contains"),
    pytest.param("abcd", "abcde", id="contained-nearly-equal"),
    pytest.param("abcx", "abcy", id="common-prefix"),
    pytest.param("a b", "b c", id="common-word"),
    pytest.param("abc", "xyz", id="nothing-in-common"),
    pytest.param("", "abc", id="empty-vs-text"),
    pytest.param("abc", "", id="text-vs-empty"),
    pytest.param("", "", id="both-empty"),
    pytest.param(
        "2020 - Alice Smith - A Study of Testing",
        "2020 - Alice Smith - A Study of Something Else",
        id="folder-names",
    ),
]


@pytest.mark.parametrize("make_orphan", ORPHAN_FORMS)
def test_closest_entry_has_similarity_1_for_the_same_folder_name(
    make_orphan: Callable[[Path, str], Path],
    paper_list: PaperList,
    pdf_root: Path,
) -> None:
    """フォルダ名が完全に一致するエントリの類似度は 1.0 (要件 8.3).

    孤立 PDF の指し方 (フォルダ内の PDF・フォルダ・旧形式の PDF) によらない。
    """
    for entry in paper_list.values():
        orphan = make_orphan(pdf_root, entry.pdf_dir_name)

        result = find_closest_entry_for_orphaned_pdf(orphan, paper_list)

        assert result is not None
        closest, similarity = result
        assert closest is entry
        assert similarity == 1.0


@pytest.mark.parametrize(
    "match_first", [True, False], ids=["match-first", "match-last"]
)
def test_closest_entry_is_the_most_similar_one_within_0_and_1(
    match_first: bool,
    make_entry: Callable[..., Entry],
    alice: Entry,
    bob: Entry,
    pdf_root: Path,
) -> None:
    """最も似たエントリが選ばれ、類似度は 0.0 より大きく 1.0 未満 (要件 8.3).

    候補に並べる順序によらない (最初・最後のどちらに本命があっても選ぶ)。
    """
    sibling = make_entry(title="A Study of Something Else")
    order = (alice, bob, sibling)
    paper_list = make_paper_list(*(order if match_first else order[::-1]))
    orphan = pdf_in_folder(pdf_root, f"{alice.pdf_dir_name} (copy)")

    result = find_closest_entry_for_orphaned_pdf(orphan, paper_list)

    assert result is not None
    closest, similarity = result
    assert closest is alice
    assert 0.0 < similarity < 1.0


def test_closest_entry_is_none_for_an_empty_paper_list(
    pdf_root: Path,
) -> None:
    """論文リストが空なら該当なし (``None``) が返る (要件 8.3)."""
    orphan = pdf_in_folder(pdf_root, ORPHAN_DIR_NAME)

    assert find_closest_entry_for_orphaned_pdf(orphan, PaperList({})) is None


def test_closest_entry_is_none_when_nothing_resembles_the_orphan(
    paper_list: PaperList, pdf_root: Path
) -> None:
    """どのエントリとも似ていない (類似度が 0.0) なら該当なし (要件 8.3).

    返る類似度は常に 0.0 より大きい。
    """
    orphan = pdf_in_folder(pdf_root, "zzz")

    assert find_closest_entry_for_orphaned_pdf(orphan, paper_list) is None


@pytest.mark.parametrize(("left", "right"), SIMILARITY_PAIRS)
def test_similarity_is_within_0_and_1(left: str, right: str) -> None:
    """どんな 2 つの名前でも、類似度は 0.0〜1.0 の範囲に収まる (要件 8.3)."""
    assert 0.0 <= _calculate_similarity(left, right) <= 1.0


@pytest.mark.parametrize(("left", "right"), SIMILARITY_PAIRS)
def test_similarity_is_1_only_for_names_equal_ignoring_case(
    left: str, right: str
) -> None:
    """類似度が 1.0 になるのは、大文字小文字を除いて一致するときだけ (要件 8.3)."""
    similarity = _calculate_similarity(left, right)

    if left.lower() == right.lower():
        assert similarity == 1.0
    else:
        assert similarity < 1.0


# --- 削除: delete_orphaned_pdfs (要件 8.4, 8.5) ------------------------------


def test_delete_removes_the_detected_orphan_and_keeps_linked_pdfs(
    sample_data: SampleData,
) -> None:
    """検出した孤立 PDF だけが削除され、件数が返る (要件 8.4).

    紐づく PDF は中身ごと残り、削除後に再び検出しても 0 件になる。
    """
    entry = sample_data.paper_list[sample_data.with_pdf_key]
    linked_before = pdf_contents(entry.get_pdf_files())
    assert len(linked_before) == 2  # 前提: 紐づく PDF が 2 件ある

    deleted = delete_orphaned_pdfs(find_orphaned_pdfs(sample_data.paper_list))

    assert deleted == 1
    assert not sample_data.orphan_pdf.exists()
    assert pdf_contents(entry.get_pdf_files()) == linked_before
    assert find_orphaned_pdfs(sample_data.paper_list) == []


def test_delete_returns_the_number_of_deleted_files(pdf_root: Path) -> None:
    """フォルダ内・旧形式をまとめて渡すと、削除したファイル数が返る (要件 8.4)."""
    targets = [
        write_pdf(pdf_root / ORPHAN_DIR_NAME / "00.pdf"),
        write_pdf(pdf_root / ORPHAN_DIR_NAME / "01.pdf"),
        write_pdf(pdf_root / ORPHAN_LEGACY_NAME),
    ]

    assert delete_orphaned_pdfs(targets) == 3
    assert [t for t in targets if t.exists()] == []


def test_delete_ignores_paths_that_do_not_exist(pdf_root: Path) -> None:
    """存在しないパスや空の一覧は削除件数に数えない (要件 8.4)."""
    existing = write_pdf(pdf_root / ORPHAN_DIR_NAME / "00.pdf")
    missing = pdf_root / "No Such Folder" / "00.pdf"

    assert delete_orphaned_pdfs([]) == 0
    assert delete_orphaned_pdfs([missing]) == 0
    assert delete_orphaned_pdfs([missing, existing]) == 1


def test_delete_removes_a_folder_that_became_empty(pdf_root: Path) -> None:
    """フォルダ内の唯一の PDF を削除すると、そのフォルダも削除される (要件 8.4)."""
    folder = pdf_root / ORPHAN_DIR_NAME
    only = write_pdf(folder / "00.pdf")

    assert delete_orphaned_pdfs([only]) == 1

    assert not folder.exists()


def test_delete_keeps_a_folder_that_still_has_pdfs(pdf_root: Path) -> None:
    """フォルダに PDF が残っている間は、フォルダを削除しない (要件 8.4)."""
    folder = pdf_root / ORPHAN_DIR_NAME
    first = write_pdf(folder / "00.pdf")
    second = write_pdf(folder / "01.pdf")

    assert delete_orphaned_pdfs([first]) == 1
    assert not first.exists()
    assert second.is_file()
    assert folder.is_dir()

    assert delete_orphaned_pdfs([second]) == 1
    assert not folder.exists()


def test_delete_keeps_a_folder_that_still_has_other_files(
    pdf_root: Path,
) -> None:
    """PDF 以外のファイルが残るフォルダは、削除しない (要件 8.4)."""
    folder = pdf_root / ORPHAN_DIR_NAME
    pdf = write_pdf(folder / "00.pdf")
    note = folder / "notes.txt"
    note.write_text("keep me", encoding="utf-8")

    assert delete_orphaned_pdfs([pdf]) == 1

    assert not pdf.exists()
    assert note.read_text(encoding="utf-8") == "keep me"


@pytest.mark.parametrize(
    "make_orphan",
    [
        pytest.param(pdf_in_folder, id="pdf-in-folder"),
        pytest.param(legacy_pdf, id="legacy-pdf"),
    ],
)
def test_delete_keeps_the_pdf_directory_itself(
    make_orphan: Callable[[Path, str], Path], pdf_root: Path
) -> None:
    """孤立 PDF をすべて削除しても、PDF ディレクトリ自体は残る (要件 8.4).

    PDF ディレクトリ直下の旧形式の PDF を削除しても、
    PDF ディレクトリが空になっても同じ。
    """
    orphan = make_orphan(pdf_root, ORPHAN_DIR_NAME)
    assert any(pdf_root.iterdir())  # 前提: 孤立 PDF だけがある

    assert delete_orphaned_pdfs([orphan]) == 1

    assert pdf_root.is_dir()
    assert list(pdf_root.iterdir()) == []


@pytest.mark.parametrize("failing_index", [0, 1, 2])
def test_delete_continues_after_a_failed_deletion(
    failing_index: int, pdf_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一部の削除に失敗しても、残りを削除して成功した件数を返す (要件 8.5).

    失敗するファイルが先頭・途中・末尾のどこにあっても、ほかの 2 件は削除
    され、失敗したファイル (とそのフォルダ) は中身ごと残る。
    """
    targets = [
        write_pdf(pdf_root / f"2001 - Orphan {label} - Paper" / "00.pdf")
        for label in "ABC"
    ]
    failing = targets[failing_index]
    fail_unlink_of(monkeypatch, failing)

    deleted = delete_orphaned_pdfs(targets)

    assert deleted == 2
    assert failing.read_bytes() == PDF_BYTES
    assert failing.parent.is_dir()
    for target in targets:
        if target != failing:
            assert not target.exists()
            assert not target.parent.exists()


# --- フォルダを渡したとき ----------------------------------------------------


def test_delete_removes_a_folder_given_as_a_path(pdf_root: Path) -> None:
    """フォルダを渡すと、フォルダごと削除される (件数の報告は KB4d)."""
    folder = pdf_root / ORPHAN_DIR_NAME
    write_pdf(folder / "00.pdf")
    write_pdf(folder / "01.pdf")

    delete_orphaned_pdfs([folder])

    assert not folder.exists()
    assert pdf_root.is_dir()


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN BUG: 孤立 PDF の削除にフォルダを渡すと、フォルダは削除される"
        "のに削除件数が 0 と報告される (KB4d). Repro: PDF 2 件を含むフォルダ"
        "を delete_orphaned_pdfs に渡す (shutil.rmtree で削除したあとに "
        "glob('*.pdf') で数えるため 0 になる). 本来は削除した PDF の件数 "
        "(2) が返る"
    ),
)
def test_delete_reports_the_number_of_pdfs_when_given_a_folder(
    pdf_root: Path,
) -> None:
    """フォルダを渡したとき、フォルダ内で削除した PDF の件数が返る (KB4d)."""
    folder = pdf_root / ORPHAN_DIR_NAME
    write_pdf(folder / "00.pdf")
    write_pdf(folder / "01.pdf")

    assert delete_orphaned_pdfs([folder]) == 2
