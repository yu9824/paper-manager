"""components (``app.components._common``) の単体テスト.

画面から呼ばれるファイル操作と論文リスト操作を、関数を直接呼んで検証する。
このモジュールは次のセクションで構成される。

- 共通の補助: fixture と補助関数 (後続のセクションからも使う)
- PDF の保存: ``save_pdfs`` (要件 9.1, 9.2。9.3 は下記)
- PDF の削除: ``delete_pdfs`` (要件 9.4, 9.5)
- 論文の登録・更新: ``register_entry_to_list`` / ``update_entry_in_list``
  (要件 9.6〜9.10)。このセクションだけで使う補助は、セクションの冒頭に
  ある
- 大文字小文字だけ違うフォルダ名: 登録・更新の重複判定と、大文字小文字だけの
  改名 (known-bug-fixes 要件 3.1〜3.4)。比較キーによる判定はどのファイル
  システムでも同じ結果になる。大文字小文字を区別するファイルシステムでしか
  起きない状況のテストは、区別しない環境 (macOS・Windows) では skip する
- 論文リストの保存: ``save_paper_list`` が、書き込みの前にファイルの状態を
  確かめ、壊れていれば退避してから書くこと。退避先を画面と通知
  (``KEY_NOTICES``) に残すこと。退避できなければ書かずにエラーを出し、登録・
  更新・削除が成功として扱われないこと (known-bug-fixes 要件 10.3)
- 手動入力フォーム: ``custom_entry`` (要件 9.11)。``AppTest`` で実行する。
  このセクションだけで使う補助は、セクションの冒頭にある
- 手動入力フォームの著者・タグ・年: 著者は `` and ``、タグは ``, `` で
  選択肢に分けて連結し、変更しなければ表記が変わらない。保存されている年が
  無効なときは、警告を出して文字の入力欄に切り替え、触らなければ元の値を
  保つ (known-bug-fixes 要件 4, 7)。年の範囲の上限は今日の日付に依存する
  ので、固定した日付で確かめる
- PDF アップロード欄: ``pdf_upload_form`` (要件 9.12)

保存・削除・登録・更新は ``data_paths`` (隔離された一時ディレクトリの配下)
か、``tmp_path`` の中だけで行い、実データには触れない。

PDF の置き場所は ``Entry.get_pdf_dir`` から導出する。通知 (``st.error``)
は ``st_messages`` で記録して検証する。``st_messages`` は ``AppTest`` と
併用できないので、画面テストを追加するときは別のテスト関数にする。
手動入力フォームのテストは ``st_messages`` を使わず、``AppTest`` の要素を
見る。

旧形式の単一 PDF の削除は ``Entry.pdf_filename`` (非推奨) を参照するため、
``DeprecationWarning`` が出る。警告の確認が目的ではないので、この 2 つの
非推奨 API の警告だけを無視する (ほかの警告は隠さない)。

要件 9.3 (保存先のパスが 250 文字を超えると保存をスキップしてエラーを表示
する) は、known-bug-fixes spec の要件 1.4・1.5 が置き換えた。パス全体の長さ
を理由に保存を拒否せず、250 文字を超えても保存される。置き換えた後の 9.3 は、
フォルダの作成やファイルの書き込み・削除に失敗したとき、そのファイルについて
失敗したことと表示用のパスを通知し、残りのファイルの処理を続けることを検証
する。変換後のパスの空間 (``windows_like_paths``) でも同じ結果になることも
確かめる。
"""

import builtins
import errno
import json
import os
import re
import shutil
import stat
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any, NamedTuple, Optional, Union

import pytest
from pybtex.database import parse_string
from conftest import (
    APP_TIMEOUT_SECONDS,
    PDF_BYTES,
    DataPaths,
    FakeUpload,
    StMessages,
    Uploads,
    redirect_data_dir,
    write_pdf,
)
import streamlit as st
from streamlit.testing.v1 import AppTest

import paper_manager
from paper_manager.app._list import _delete_entry
from paper_manager.app.components import _common
from paper_manager.app.components._common import (
    delete_pdfs,
    pdf_upload_form,
    register_entry_to_list,
    save_paper_list,
    save_pdfs,
    update_entry_in_list,
)
from paper_manager.app.helper import KEY_NOTICES, MAP_REQUIRED_FIELDS
from paper_manager.app.helper._orphan_pdf import find_orphaned_pdfs
from paper_manager.entry import Entry, PaperList
from paper_manager.helper import (
    folder_name_key,
    to_display_path,
    to_fs_path,
)

pytestmark = pytest.mark.filterwarnings(
    "ignore:(pdf_filename|_migrate_legacy_pdf_filenames) is deprecated"
    ":DeprecationWarning"
)

# 保存パスの長さを変えて、保存が長さに左右されないことを確かめる.
# 250 は修正前の上限、260 は Windows の MAX_PATH. 要件 9.3 (250 文字を超える
# と保存をスキップする) は、known-bug-fixes 要件 1.5 が置き換えた.
OLD_LIMIT = 250
PATH_LENGTHS = [OLD_LIMIT, OLD_LIMIT + 1, 261, 400]


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

    ``root`` の下に詰め物の階層を足し、
    ``<基点>/<エントリのフォルダ>/<file_name>`` の文字数を揃える。詰め物の
    階層は 1 つ 100 文字以内にする (ファイル名の上限 255 文字に収める)。
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
    remaining = target_length - unpadded
    assert remaining >= 2, (
        f"cannot pad {root} to {target_length} characters "
        f"(only {remaining} characters to add)"
    )
    base_dir = root
    while remaining > 102:
        base_dir = base_dir / ("p" * 100)
        remaining -= 101
    base_dir = base_dir / ("p" * (remaining - 1))
    assert len(str(base_dir / entry.pdf_dir_name / file_name)) == target_length
    return base_dir


@contextmanager
def read_only(path: Path) -> Iterator[None]:
    """``path`` のディレクトリを、ブロックの間だけ書き込み不可にする.

    権限が効かない環境 (root で実行しているなど) では、テストを skip する。
    ブロックを抜けるとき (例外のときも) 元の権限に戻す。

    Parameters
    ----------
    path : Path
        書き込み不可にするディレクトリ。存在すること。
    """
    original = stat.S_IMODE(path.stat().st_mode)
    path.chmod(original & ~0o222)
    try:
        if os.access(path, os.W_OK):
            pytest.skip("directory permissions are not enforced here")
        yield
    finally:
        path.chmod(original)


@pytest.fixture(
    params=[False, True], ids=["plain-paths", "windows-like-paths"]
)
def path_space(request: pytest.FixtureRequest) -> bool:
    """変換前のパスだけの空間と、Windows を模した別名変換の空間を切り替える.

    ``True`` のときは ``windows_like_paths`` を有効にする。変換後のパスと
    変換前のパスを混ぜた比較が不一致になる空間で、同じテストが成功する
    ことを確かめるために使う。
    """
    if request.param:
        request.getfixturevalue("windows_like_paths")
    return request.param


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


# --- PDF の保存: save_pdfs (要件 9.1, 9.2, 9.3 → 1.4, 1.5) -----------------


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


@pytest.mark.parametrize("path_length", PATH_LENGTHS)
def test_save_pdfs_saves_whatever_the_path_length(
    entry: Entry,
    pdf_root: Path,
    st_messages: StMessages,
    path_length: int,
) -> None:
    """保存パスが 250 文字を超えても、PDF は保存される (known-bug-fixes 1.5).

    基点の長さを調整して、``00.pdf`` と ``01.pdf`` の保存パスをちょうど
    ``path_length`` 文字にする。250 文字は修正前の上限、261 文字は Windows
    の MAX_PATH (260 文字) を超える長さ。エラーは出ない。
    """
    base_dir = padded_base_dir(pdf_root, entry, "00.pdf", path_length)
    uploads = make_uploads(2)

    saved = save_pdfs(uploads, entry, base_dir)

    pdf_dir = entry.get_pdf_dir(base_dir)
    assert saved == 2
    assert len(str(pdf_dir / "00.pdf")) == path_length
    assert pdf_names(pdf_dir) == ["00.pdf", "01.pdf"]
    assert (pdf_dir / "00.pdf").read_bytes() == uploads[0].getvalue()
    assert (pdf_dir / "01.pdf").read_bytes() == uploads[1].getvalue()
    assert st_messages.error == []


def test_save_pdfs_keeps_saving_when_numbers_cross_the_old_limit(
    entry: Entry, pdf_root: Path, st_messages: StMessages
) -> None:
    """番号が増えて保存パスが 250 文字を超えても、保存が続く (1.5).

    保存名は番号が増えるほど長くなる。``99.pdf`` までは 250 文字に収まり、
    ``100.pdf`` から 251 文字になる。既存の ``00.pdf``〜``98.pdf`` がある
    状態で 3 件保存すると、``99.pdf``・``100.pdf``・``101.pdf`` の 3 件が
    すべて保存される (修正前は 2 件目以降がスキップされた)。
    """
    base_dir = padded_base_dir(pdf_root, entry, "99.pdf", OLD_LIMIT)
    pdf_dir = entry.get_pdf_dir(base_dir)
    existing = make_pdfs(pdf_dir, [f"{i:02d}.pdf" for i in range(99)])
    uploads = make_uploads(3)

    saved = save_pdfs(uploads, entry, base_dir)

    assert saved == 3
    assert len(str(pdf_dir / "100.pdf")) == OLD_LIMIT + 1
    for name, upload in zip(["99.pdf", "100.pdf", "101.pdf"], uploads):
        assert (pdf_dir / name).read_bytes() == upload.getvalue()
    assert len(pdf_names(pdf_dir)) == len(existing) + 3
    assert st_messages.error == []


def failing_open_on_call(
    failing_call: int, error: OSError
) -> Callable[..., Any]:
    """``failing_call`` 回目の呼び出しだけ ``error`` を送出する ``open`` を返す.

    ほかの呼び出しは組み込みの ``open`` に渡す。``_common`` のモジュール
    変数 ``open`` として差し替えて、``save_pdfs`` の書き込みの失敗を
    再現する (権限に頼らないので、root でも同じ結果になる)。

    Parameters
    ----------
    failing_call : int
        失敗させる呼び出しの順番 (1 から数える)。
    error : OSError
        送出する例外。
    """
    calls = 0

    def fake_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == failing_call:
            raise error
        return builtins.open(file, *args, **kwargs)

    return fake_open


def test_save_pdfs_reports_a_failed_write_and_processes_the_rest(
    entry: Entry,
    pdf_root: Path,
    st_messages: StMessages,
    path_space: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """書き込みに失敗したファイルを表示用のパスで通知し、残りを保存する (1.4).

    2 件目の書き込みだけが失敗する。1 件目と 3 件目は保存され、失敗は
    そのファイルのパス (prefix のない表示用のパス) と理由とともに通知
    される。失敗したファイルの番号は、次のファイルが使う。
    """
    error = OSError(errno.EIO, "Input/output error")
    monkeypatch.setattr(
        _common, "open", failing_open_on_call(2, error), raising=False
    )
    uploads = make_uploads(3)

    saved = save_pdfs(uploads, entry)

    pdf_dir = entry.get_pdf_dir(pdf_root)
    assert saved == 2
    assert pdf_names(pdf_dir) == ["00.pdf", "01.pdf"]
    assert (pdf_dir / "00.pdf").read_bytes() == uploads[0].getvalue()
    assert (pdf_dir / "01.pdf").read_bytes() == uploads[2].getvalue()
    assert len(st_messages.error) == 1
    message = st_messages.error[0]
    assert to_display_path(pdf_dir / "01.pdf") in message
    assert "Input/output error" in message
    if path_space:
        # 変換後のパス (別名変換では ``//`` で始まる) は、画面に出さない
        assert str(pdf_dir / "01.pdf") not in message


def test_save_pdfs_reports_every_file_that_cannot_be_written(
    entry: Entry, pdf_root: Path, st_messages: StMessages
) -> None:
    """フォルダが書き込み不可なら、ファイルごとに失敗を通知する (1.4).

    1 件の失敗で処理を止めず、すべてのファイルを試す。何も保存されない。
    """
    pdf_dir = entry.get_pdf_dir(pdf_root)
    pdf_dir.mkdir(parents=True)
    uploads = make_uploads(3)

    with read_only(pdf_dir):
        saved = save_pdfs(uploads, entry)

    assert saved == 0
    assert pdf_names(pdf_dir) == []
    assert len(st_messages.error) == len(uploads)
    for message in st_messages.error:
        assert to_display_path(pdf_dir / "00.pdf") in message
        assert "Permission denied" in message


def test_save_pdfs_reports_a_folder_that_cannot_be_created(
    entry: Entry, pdf_root: Path, st_messages: StMessages
) -> None:
    """フォルダを作れないときは、フォルダのパスと理由を通知して 0 を返す (1.4)."""
    pdf_root.mkdir(parents=True, exist_ok=True)
    pdf_dir = entry.get_pdf_dir(pdf_root)

    with read_only(pdf_root):
        saved = save_pdfs(make_uploads(2), entry)

    assert saved == 0
    assert not pdf_dir.exists()
    assert len(st_messages.error) == 1
    assert to_display_path(pdf_dir) in st_messages.error[0]
    assert "Permission denied" in st_messages.error[0]


def test_save_pdfs_saves_in_the_converted_path_space(
    entry: Entry, pdf_root: Path, st_messages: StMessages, path_space: bool
) -> None:
    """変換後のパスの空間でも、保存とフォルダの作成が成功する (1.1, 1.2)."""
    uploads = make_uploads(2)

    saved = save_pdfs(uploads, entry)

    assert saved == 2
    plain_dir = pdf_root / entry.pdf_dir_name
    assert pdf_names(plain_dir) == ["00.pdf", "01.pdf"]
    assert (plain_dir / "01.pdf").read_bytes() == uploads[1].getvalue()
    assert st_messages.error == []


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


def tree_of(root: Path) -> dict[str, Optional[bytes]]:
    """``root`` の下のすべてのファイルとフォルダを、内容付きで返す.

    Returns
    -------
    dict[str, Optional[bytes]]
        ``root`` からの相対パス → ファイルの中身 (フォルダは ``None``)。
    """
    return {
        str(path.relative_to(root)): (
            path.read_bytes() if path.is_file() else None
        )
        for path in sorted(root.rglob("*"))
    }


def test_delete_pdfs_removes_the_folder_in_both_path_spaces(
    entry: Entry,
    other_entry: Entry,
    pdf_root: Path,
    st_messages: StMessages,
    path_space: bool,
) -> None:
    """フォルダごとの削除は、変換後のパスの空間でも成功する (1.2).

    PDF ルートは削除されず、別のエントリのフォルダは残る。
    """
    pdf_dir = pdf_root / entry.pdf_dir_name
    make_pdfs(pdf_dir, ["00.pdf", "01.pdf"])
    other_dir = pdf_root / other_entry.pdf_dir_name
    other = make_pdfs(other_dir, ["00.pdf"])

    deleted = delete_pdfs(entry)

    assert deleted == 2
    assert not pdf_dir.exists()
    assert pdf_names(pdf_root) == [other_entry.pdf_dir_name]
    assert (other_dir / "00.pdf").read_bytes() == other["00.pdf"]
    assert st_messages.error == []


def test_delete_pdfs_specific_files_in_both_path_spaces(
    entry: Entry, pdf_root: Path, st_messages: StMessages, path_space: bool
) -> None:
    """指定したファイルの削除と空のフォルダの削除は、どの空間でも成功する (1.2).

    指定するパスは、変換前のパスでも変換後のパスでもよい。最後のファイルを
    消すとエントリのフォルダは消えるが、PDF ルートは消えない。
    """
    pdf_dir = pdf_root / entry.pdf_dir_name
    make_pdfs(pdf_dir, ["00.pdf", "01.pdf"])
    converted_dir = entry.get_pdf_dir(pdf_root)

    first = delete_pdfs(entry, specific_files=[pdf_dir / "00.pdf"])
    assert first == 1
    assert pdf_names(pdf_dir) == ["01.pdf"]

    second = delete_pdfs(entry, specific_files=[converted_dir / "01.pdf"])

    assert second == 1
    assert not pdf_dir.exists()
    assert pdf_root.is_dir()
    assert st_messages.error == []


def test_delete_pdfs_removes_legacy_single_pdfs_with_the_folder(
    entry: Entry,
    other_entry: Entry,
    pdf_root: Path,
    st_messages: StMessages,
    path_space: bool,
) -> None:
    """フォルダごとの削除は、移せなかった旧形式の単一 PDF も削除する (1.2).

    旧形式の単一 PDF は、PDF ルート直下の ``pdf_filename`` と
    ``<pdf_dir_name>.pdf``。削除した論文の PDF がルートに残り、孤立 PDF
    として現れないようにする。件数にはどちらも数える。別のエントリの
    ものは削除されない。
    """
    # 末尾が `.` のタイトルでは、2 つの旧形式の名前が異なる
    dotted = Entry({**entry, "title": "Study of Things."})
    assert dotted.pdf_filename != f"{dotted.pdf_dir_name}.pdf"
    pdf_dir = pdf_root / dotted.pdf_dir_name
    make_pdfs(pdf_dir, ["00.pdf", "01.pdf"])
    left_behind = write_pdf(pdf_root / dotted.pdf_filename)
    folder_named = write_pdf(pdf_root / f"{dotted.pdf_dir_name}.pdf")
    other_dir = pdf_root / other_entry.pdf_dir_name
    other = make_pdfs(other_dir, ["00.pdf"])
    other_legacy_content = PDF_BYTES + b"%other legacy\n"
    other_legacy = write_pdf(
        pdf_root / f"{other_entry.pdf_dir_name}.pdf", other_legacy_content
    )

    deleted = delete_pdfs(dotted)

    assert deleted == 4
    assert not pdf_dir.exists()
    assert not left_behind.exists()
    assert not folder_named.exists()
    assert pdf_names(pdf_root) == sorted(
        [other_entry.pdf_dir_name, f"{other_entry.pdf_dir_name}.pdf"]
    )
    assert (other_dir / "00.pdf").read_bytes() == other["00.pdf"]
    assert other_legacy.read_bytes() == other_legacy_content
    assert st_messages.error == []


def test_delete_pdfs_removes_a_legacy_single_pdf_in_both_path_spaces(
    entry: Entry, pdf_root: Path, st_messages: StMessages, path_space: bool
) -> None:
    """フォルダがなく旧形式の単一 PDF だけでも、どの空間でも削除できる (1.2)."""
    legacy = write_pdf(pdf_root / entry.pdf_filename)

    deleted = delete_pdfs(entry)

    assert deleted == 1
    assert not legacy.exists()
    assert pdf_root.is_dir()
    assert st_messages.error == []


def test_delete_pdfs_specific_files_reports_a_failure_and_continues(
    entry: Entry,
    pdf_root: Path,
    st_messages: StMessages,
    path_space: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """削除に失敗したファイルを表示用のパスで通知し、残りを削除する (1.4).

    ``01.pdf`` の削除だけが失敗する。``00.pdf`` と ``02.pdf`` は削除され、
    失敗したファイルとフォルダは残る。
    """
    pdf_dir = pdf_root / entry.pdf_dir_name
    contents = make_pdfs(pdf_dir, ["00.pdf", "01.pdf", "02.pdf"])
    real_unlink = Path.unlink

    def fake_unlink(self: Path, *args: Any, **kwargs: Any) -> None:
        if self.name == "01.pdf":
            raise PermissionError(errno.EACCES, "Permission denied", str(self))
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fake_unlink)
    converted_dir = entry.get_pdf_dir(pdf_root)

    deleted = delete_pdfs(
        entry,
        specific_files=[pdf_dir / name for name in contents],
    )

    assert deleted == 2
    assert pdf_names(pdf_dir) == ["01.pdf"]
    assert len(st_messages.error) == 1
    message = st_messages.error[0]
    assert to_display_path(converted_dir / "01.pdf") in message
    assert "Permission denied" in message
    if path_space:
        assert str(converted_dir / "01.pdf") not in message


def test_delete_pdfs_reports_a_folder_that_cannot_be_deleted(
    entry: Entry,
    pdf_root: Path,
    st_messages: StMessages,
    path_space: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """フォルダを削除できなければ通知して 0 件を数え、旧形式の削除は続ける (1.4).

    フォルダの削除に失敗しても、移せなかった旧形式の単一 PDF の削除は
    行われる。失敗したフォルダの PDF は削除済みとして数えない。
    """
    pdf_dir = pdf_root / entry.pdf_dir_name
    contents = make_pdfs(pdf_dir, ["00.pdf", "01.pdf"])
    legacy = write_pdf(pdf_root / entry.pdf_filename)

    def fake_rmtree(path: Any, *args: Any, **kwargs: Any) -> None:
        raise OSError(errno.EBUSY, "Device or resource busy", str(path))

    monkeypatch.setattr(shutil, "rmtree", fake_rmtree)
    converted_dir = entry.get_pdf_dir(pdf_root)

    deleted = delete_pdfs(entry)

    assert deleted == 1
    assert not legacy.exists()
    assert pdf_names(pdf_dir) == sorted(contents)
    assert len(st_messages.error) == 1
    message = st_messages.error[0]
    assert to_display_path(converted_dir) in message
    assert "Device or resource busy" in message
    if path_space:
        assert str(converted_dir) not in message


@pytest.mark.parametrize("mode", ["whole-folder", "specific-files"])
@pytest.mark.parametrize(
    "folder_name",
    ["", ".", "..", "../outside", "ABSOLUTE"],
    ids=["empty", "dot", "parent", "sibling-outside", "absolute"],
)
def test_delete_pdfs_never_deletes_the_pdf_root_or_anything_outside_it(
    entry: Entry,
    tmp_path: Path,
    st_messages: StMessages,
    path_space: bool,
    monkeypatch: pytest.MonkeyPatch,
    folder_name: str,
    mode: str,
) -> None:
    """フォルダ名の計算結果が何であっても、PDF ルートとその外は削除しない (1.2).

    ``pdf_dir_name`` が PDF ルートそのもの (空、``.``)、その外
    (``..``、``../outside``、絶対パス) を指すようになっても、削除の対象に
    ならない。フォルダごとの削除では何も変わらず、通知される。指定した
    ファイルの削除では、空になったフォルダを削除しない (指定したファイル
    自体は、呼び出し元の指定どおり削除される)。
    """
    data_dir = tmp_path / "data"
    base_dir = data_dir / "pdf"
    base_dir.mkdir(parents=True)
    write_pdf(data_dir / "list.json", b"{}")
    write_pdf(data_dir / "outside" / "00.pdf")
    write_pdf(data_dir / "outside.pdf")
    if folder_name == "ABSOLUTE":
        folder_name = str(tmp_path / "elsewhere")
        write_pdf(tmp_path / "elsewhere" / "00.pdf")
        write_pdf(tmp_path / "elsewhere.pdf")
    if mode == "whole-folder":
        make_pdfs(base_dir / "Other Paper", ["00.pdf"])
    monkeypatch.setattr(
        Entry, "pdf_dir_name", property(lambda self: folder_name)
    )
    # ``pdf_dir_name`` が指す先。指定したファイルを消すと空になりうる
    target_dir = base_dir / folder_name
    write_pdf(target_dir / "stray.pdf", PDF_BYTES + b"%stray\n")
    before = tree_of(tmp_path)

    if mode == "whole-folder":
        deleted = delete_pdfs(entry, base_dir)
        assert deleted == 0
        assert tree_of(tmp_path) == before
        assert len(st_messages.error) >= 1
    else:
        specified = sorted(target_dir.glob("*.pdf"))
        deleted = delete_pdfs(entry, base_dir, specific_files=specified)
        assert deleted == len(specified)
        specified_keys = {
            os.path.relpath(os.path.normpath(path), tmp_path)
            for path in specified
        }
        # 指定したファイル以外は何も変わらない (空になったフォルダも残る)
        assert tree_of(tmp_path) == {
            k: v for k, v in before.items() if k not in specified_keys
        }
    assert base_dir.is_dir()


@pytest.fixture
def redirected_pdf_root(
    monkeypatch: pytest.MonkeyPatch, data_paths: DataPaths, tmp_path: Path
) -> Path:
    """変換が別のディレクトリへ写す状態にして、写し先の PDF ルートを返す.

    変換前の PDF ルート (``data_paths.pdf``) は空のままにしておく。変換後の
    PDF ルート (戻り値) は、``DIRPATH_PDF`` から作ったパスの変換先で、
    変換後のパスでファイルを操作する実装だけがここを使う。
    """
    redirected = tmp_path / "converted"
    redirect_data_dir(monkeypatch, data_paths.root, redirected)
    return redirected / data_paths.pdf.relative_to(data_paths.root)


def test_save_pdfs_writes_into_the_converted_pdf_root(
    entry: Entry,
    pdf_root: Path,
    redirected_pdf_root: Path,
    st_messages: StMessages,
) -> None:
    """保存は、変換後の PDF ルートの下に書く (1.1, 1.2)."""
    uploads = make_uploads(2)

    saved = save_pdfs(uploads, entry)

    converted_dir = redirected_pdf_root / entry.pdf_dir_name
    assert saved == 2
    assert pdf_names(converted_dir) == ["00.pdf", "01.pdf"]
    assert (converted_dir / "01.pdf").read_bytes() == uploads[1].getvalue()
    assert list(pdf_root.glob("*")) == []  # 変換前のパスの下には書かない
    assert st_messages.error == []


def test_delete_pdfs_whole_folder_works_in_the_converted_pdf_root(
    entry: Entry,
    other_entry: Entry,
    pdf_root: Path,
    redirected_pdf_root: Path,
    st_messages: StMessages,
) -> None:
    """フォルダごとの削除は、変換後のパスの下を対象にする (1.2).

    旧形式の単一 PDF も、変換後の PDF ルート直下から削除する。変換前の
    パスの下に置いた同名の PDF (おとり) は、削除されない。
    """
    converted_dir = redirected_pdf_root / entry.pdf_dir_name
    make_pdfs(converted_dir, ["00.pdf", "01.pdf"])
    legacy = write_pdf(redirected_pdf_root / entry.pdf_filename)
    other = make_pdfs(
        redirected_pdf_root / other_entry.pdf_dir_name, ["00.pdf"]
    )
    decoy_dir = pdf_root / entry.pdf_dir_name
    decoys = make_pdfs(decoy_dir, ["00.pdf"])
    decoy_legacy = write_pdf(pdf_root / entry.pdf_filename)

    deleted = delete_pdfs(entry)

    assert deleted == 3
    assert not converted_dir.exists()
    assert not legacy.exists()
    other_file = redirected_pdf_root / other_entry.pdf_dir_name / "00.pdf"
    assert other_file.read_bytes() == other["00.pdf"]
    assert (decoy_dir / "00.pdf").read_bytes() == decoys["00.pdf"]
    assert decoy_legacy.is_file()
    assert redirected_pdf_root.is_dir()
    assert st_messages.error == []


def test_delete_pdfs_specific_files_work_in_the_converted_pdf_root(
    entry: Entry,
    pdf_root: Path,
    redirected_pdf_root: Path,
    st_messages: StMessages,
) -> None:
    """指定したファイルは変換後のパスで削除し、空のフォルダも消す (1.2).

    呼び出し元は変換前のパスで指定する。変換前のパスの下に置いた同名の
    PDF (おとり) は、削除されない。
    """
    converted_dir = redirected_pdf_root / entry.pdf_dir_name
    make_pdfs(converted_dir, ["00.pdf", "01.pdf"])
    decoy_dir = pdf_root / entry.pdf_dir_name
    decoys = make_pdfs(decoy_dir, ["00.pdf", "01.pdf"])
    logical = [decoy_dir / "00.pdf", decoy_dir / "01.pdf"]

    deleted = delete_pdfs(entry, specific_files=logical)

    assert deleted == 2
    assert not converted_dir.exists()  # 空になったので削除される
    assert redirected_pdf_root.is_dir()
    for name, content in decoys.items():
        assert (decoy_dir / name).read_bytes() == content
    assert st_messages.error == []


def test_delete_pdfs_ignores_a_legacy_name_too_long_to_exist(
    make_entry: Callable[..., Entry],
    pdf_root: Path,
    st_messages: StMessages,
    path_space: bool,
) -> None:
    """旧形式の名前が長すぎても、フォルダの削除に失敗したと通知しない (1.4).

    ``pdf_filename`` が 255 文字を超えると、その名前のファイルは作れず、
    存在の確認が ``ENAMETOOLONG`` になる。存在しないものとして扱い、
    通知しない。``pdf_dir_name`` は 200 文字以内なので、フォルダは削除される。
    """
    long_entry = make_entry(title="T" * 300)
    assert len(long_entry.pdf_filename) > 255
    assert len(long_entry.pdf_dir_name) <= 200
    pdf_dir = pdf_root / long_entry.pdf_dir_name
    make_pdfs(pdf_dir, ["00.pdf"])

    deleted = delete_pdfs(long_entry)

    assert deleted == 1
    assert not pdf_dir.exists()
    assert pdf_root.is_dir()
    assert st_messages.error == []


def test_delete_pdfs_specific_files_ignore_a_name_too_long_to_exist(
    make_entry: Callable[..., Entry],
    pdf_root: Path,
    st_messages: StMessages,
    path_space: bool,
) -> None:
    """指定したファイルの名前が長すぎても、存在しないものとして扱う (1.4)."""
    long_entry = make_entry(title="T" * 300)
    pdf_dir = pdf_root / long_entry.pdf_dir_name
    make_pdfs(pdf_dir, ["00.pdf"])
    too_long = pdf_root / long_entry.pdf_filename

    deleted = delete_pdfs(
        long_entry, specific_files=[too_long, pdf_dir / "00.pdf"]
    )

    assert deleted == 1
    assert not pdf_dir.exists()
    assert st_messages.error == []


@pytest.mark.parametrize(
    ("method", "target", "action"),
    [
        pytest.param(
            "is_file",
            "legacy",
            "Failed to delete legacy PDF file",
            id="legacy-pdf-check",
        ),
        pytest.param(
            "is_dir", "folder", "Failed to delete PDF directory", id="folder"
        ),
    ],
)
def test_delete_pdfs_reports_an_existence_check_that_fails_for_another_reason(
    entry: Entry,
    pdf_root: Path,
    st_messages: StMessages,
    path_space: bool,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    target: str,
    action: str,
) -> None:
    """存在の確認が ``ENAMETOOLONG`` 以外で失敗したら、失敗として通知する (1.4).

    ``PermissionError`` などは、存在しないことを意味しない。通知して、
    残りの処理 (フォルダまたは旧形式の PDF の削除) は続ける。
    """
    pdf_dir = pdf_root / entry.pdf_dir_name
    make_pdfs(pdf_dir, ["00.pdf"])
    legacy = write_pdf(pdf_root / entry.pdf_filename)
    failing_path = pdf_dir if target == "folder" else legacy
    real_check = getattr(Path, method)

    def fake_check(self: Path) -> bool:
        if self.name == failing_path.name:
            raise PermissionError(errno.EACCES, "Permission denied", str(self))
        return real_check(self)

    monkeypatch.setattr(Path, method, fake_check)
    converted = entry.get_pdf_dir(pdf_root)
    converted_failing = (
        converted if target == "folder" else converted.parent / legacy.name
    )

    deleted = delete_pdfs(entry)

    # 失敗した方は残り、もう一方は削除される (どちらも PDF 1 件)
    assert deleted == 1
    assert pdf_dir.exists() == (target == "folder")
    assert legacy.exists() == (target == "legacy")
    assert len(st_messages.error) == 1
    message = st_messages.error[0]
    assert action in message
    assert to_display_path(converted_failing) in message
    assert "Permission denied" in message


def test_delete_pdfs_resolves_the_default_pdf_root_once(
    entry: Entry,
    tmp_path: Path,
    st_messages: StMessages,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``base_dir`` を省略したとき、PDF ルートとフォルダを同じ値から作る.

    ``_common.DIRPATH_PDF`` だけを別の場所にしても、その下のフォルダを
    削除できる (PDF ルートの判定が、フォルダの親と食い違わない)。
    """
    elsewhere = tmp_path / "elsewhere"
    monkeypatch.setattr(_common, "DIRPATH_PDF", elsewhere)
    pdf_dir = elsewhere / entry.pdf_dir_name
    make_pdfs(pdf_dir, ["00.pdf", "01.pdf"])

    deleted = delete_pdfs(entry)

    assert deleted == 2
    assert not pdf_dir.exists()
    assert st_messages.error == []


def test_failure_reasons_never_show_the_converted_path(
    entry: Entry,
    pdf_root: Path,
    st_messages: StMessages,
    caplog: pytest.LogCaptureFixture,
    path_space: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """理由のない ``OSError`` でも、変換後のパスを画面とログに出さない (1.4).

    ``strerror`` のない ``OSError`` は、``str()`` にパス (変換後のパス) を
    含むことがある。画面とログには、表示用のパスと、パスを含まない理由
    だけを出す。
    """
    pdf_dir = pdf_root / entry.pdf_dir_name
    make_pdfs(pdf_dir, ["00.pdf"])

    def fake_rmtree(path: Any, *args: Any, **kwargs: Any) -> None:
        error = OSError()
        error.filename = path
        raise error

    monkeypatch.setattr(shutil, "rmtree", fake_rmtree)
    converted = entry.get_pdf_dir(pdf_root)

    with caplog.at_level("ERROR"):
        deleted = delete_pdfs(entry)

    assert deleted == 0
    assert len(st_messages.error) == 1
    message = st_messages.error[0]
    assert to_display_path(converted) in message
    assert "PosixPath" not in message
    assert "None" not in message
    assert to_display_path(converted) in caplog.text
    if path_space:
        assert str(converted) not in message
        assert str(converted) not in caplog.text


def test_delete_pdfs_reports_a_symlinked_folder_without_deleting_it(
    entry: Entry,
    pdf_root: Path,
    tmp_path: Path,
    st_messages: StMessages,
    caplog: pytest.LogCaptureFixture,
    path_space: bool,
) -> None:
    """PDF フォルダがシンボリックリンクなら、削除せずに失敗を通知する (1.4).

    リンク先の PDF は残る。``rmtree`` がリンクを拒否したときの例外には
    ``strerror`` がないが、画面とログに変換後のパスは出ない。
    """
    target = tmp_path / "linked"
    kept = make_pdfs(target, ["00.pdf"])
    pdf_root.mkdir(parents=True, exist_ok=True)
    link = pdf_root / entry.pdf_dir_name
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"symbolic links are not available: {e}")
    converted = entry.get_pdf_dir(pdf_root)

    with caplog.at_level("ERROR"):
        deleted = delete_pdfs(entry)

    assert deleted == 0
    assert (target / "00.pdf").read_bytes() == kept["00.pdf"]
    assert link.is_symlink()
    assert len(st_messages.error) == 1
    message = st_messages.error[0]
    assert to_display_path(converted) in message
    assert "PosixPath" not in message
    if path_space:
        assert str(converted) not in message
        assert str(converted) not in caplog.text


def test_save_pdfs_failure_without_a_reason_never_shows_the_converted_path(
    entry: Entry,
    pdf_root: Path,
    st_messages: StMessages,
    caplog: pytest.LogCaptureFixture,
    path_space: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保存の失敗も、理由のない ``OSError`` で変換後のパスを出さない (1.4)."""
    error = OSError()
    monkeypatch.setattr(
        _common, "open", failing_open_on_call(1, error), raising=False
    )
    converted = entry.get_pdf_dir(pdf_root)
    error.filename = str(converted / "00.pdf")

    with caplog.at_level("ERROR"):
        saved = save_pdfs(make_uploads(1), entry)

    assert saved == 0
    assert len(st_messages.error) == 1
    message = st_messages.error[0]
    assert to_display_path(converted / "00.pdf") in message
    assert "PosixPath" not in message
    assert "None" not in message
    if path_space:
        assert str(converted / "00.pdf") not in message
        assert str(converted / "00.pdf") not in caplog.text


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


# --- 大文字小文字だけ違うフォルダ名 (known-bug-fixes 要件 3.1〜3.4) --------
# フォルダ名の同一性は比較キー (``folder_name_key``: 大文字小文字と Unicode
# 正規化を区別しない) で判定する。ファイルシステムが大文字小文字を区別するか
# どうか (Linux と macOS・Windows) にかかわらず、同じ結果になる。このセクション
# のテストは、どのファイルシステムでも成功する。大文字小文字を区別する
# ファイルシステムでしか起きない状況 (大文字小文字だけ違う別のフォルダが実際に
# ある) のテストだけは、区別しない環境では skip する。

# (既存の論文の変更, 登録・更新後の変更). 比較キーは等しく、文字列は異なる.
# ``casefold-ss`` の ``ß`` と ``ss`` は、どのファイルシステムでも別のフォルダ
# 名だが、比較キーでは同じになる (同じとみなす安全側).
SAME_KEY_CHANGES = [
    pytest.param({}, {"title": "a study of testing"}, id="lowercase-title"),
    pytest.param(
        {},
        {"author": "ALICE SMITH", "title": "A STUDY OF TESTING"},
        id="uppercase",
    ),
    pytest.param(
        {"title": "Caf\u00e9"}, {"title": "Cafe\u0301"}, id="nfc-to-nfd"
    ),
    pytest.param(
        {"title": "Cafe\u0301"}, {"title": "Caf\u00e9"}, id="nfd-to-nfc"
    ),
    pytest.param(
        {"title": "Strasse"}, {"title": "Stra\u00dfe"}, id="casefold-ss"
    ),
    # フォルダ名が上限の 200 文字ちょうど (一時名も 255 バイトに収まる)
    pytest.param(
        {"title": "L" * 179},
        {"title": "l" + "L" * 178},
        id="longest-name",
    ),
]
TEMPORARY_PREFIX = ".rename-"
WINDOWS_RESERVED = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def assert_names_differ_only_in_form(first: Entry, second: Entry) -> None:
    """2 つのエントリのフォルダ名が、比較キーは等しく文字列は異なると確かめる."""
    assert first.pdf_dir_name != second.pdf_dir_name
    assert folder_name_key(first.pdf_dir_name) == folder_name_key(
        second.pdf_dir_name
    )


def is_case_sensitive(directory: Path) -> bool:
    """``directory`` のファイルシステムが大文字小文字を区別するかを返す."""
    probe = directory / "CaseProbe"
    probe.mkdir()
    try:
        return not (directory / "caseprobe").exists()
    finally:
        probe.rmdir()


def record_renames(
    monkeypatch: pytest.MonkeyPatch,
    fails: Callable[[Path, Path], bool] = lambda source, target: False,
) -> list[tuple[Path, Path]]:
    """``Path.rename`` の呼び出しを記録し、``fails`` が真の呼び出しを失敗させる.

    ``fails`` が偽の呼び出しは、本物の ``Path.rename`` に渡す。

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        差し替えに使う。テストの後に元へ戻る。
    fails : Callable[[Path, Path], bool], optional
        ``(改名元, 改名先)`` を受け取り、その呼び出しを ``PermissionError`` で
        失敗させるかを返す。既定は失敗させない。

    Returns
    -------
    list[tuple[Path, Path]]
        呼ばれた順の ``(改名元, 改名先)``。
    """
    calls: list[tuple[Path, Path]] = []
    original = Path.rename

    def rename(self: Path, target: Union[str, Path]) -> Any:
        calls.append((self, Path(target)))
        if fails(self, Path(target)):
            raise PermissionError(
                errno.EACCES, os.strerror(errno.EACCES), str(self)
            )
        return original(self, target)

    monkeypatch.setattr(Path, "rename", rename)
    return calls


def is_temporary(path: Path) -> bool:
    """``path`` が、一時名を経由する改名の一時名かを返す."""
    return path.name.startswith(TEMPORARY_PREFIX)


# --- 登録・更新で付く引用キー (要件 13.1) ---

# (筆頭著者, 引用キーの ``<筆頭著者の部分>``). 空白・``,``・``{``・``}`` は除かれる.
UNREADABLE_KEY_AUTHORS = [
    pytest.param("Smith, Alice", "SmithAlice", id="comma"),
    pytest.param("King, Jr., Martin", "KingJr.Martin", id="suffix"),
    pytest.param("Alice {Smith}", "AliceSmith", id="braces-and-space"),
    pytest.param(
        "Smith, Alice and Bob Jones", "SmithAlice", id="several-authors"
    ),
]


def assert_citation_key_is_readable(key: str) -> None:
    """``key`` が空白・``,``・``{``・``}`` を含まず、pybtex で読めることを確かめる."""
    assert key
    assert not any(char.isspace() or char in ",{}" for char in key)
    parsed = parse_string(f"@article{{{key}, title = {{x}}}}", "bibtex")
    assert len(parsed.entries) == 1
    assert key in parsed.entries


@pytest.mark.parametrize(("author", "stem"), UNREADABLE_KEY_AUTHORS)
def test_register_entry_gives_a_readable_citation_key(
    data_paths: DataPaths,
    make_entry: Callable[..., Entry],
    st_messages: StMessages,
    author: str,
    stem: str,
) -> None:
    """読めない文字を含む著者の論文を登録しても、読める識別子が付く (13.1).

    登録された識別子は ``list.json``・session state・エントリの ``ID`` で
    一致し、空白・``,``・``{``・``}`` を含まない。
    """
    paper_list, _ = seed_paper_list(data_paths)
    entry = make_entry(author=author)

    assert register_entry_to_list(paper_list, entry) is True

    state = read_state(paper_list, data_paths)
    key = f"{stem}2020_0"
    assert list(state.file) == [key]
    assert list(state.session) == [key]
    assert state.file[key]["ID"] == key
    assert state.file[key]["author"] == author
    assert_citation_key_is_readable(key)
    assert st_messages.error == []


@pytest.mark.parametrize(("author", "stem"), UNREADABLE_KEY_AUTHORS)
def test_update_entry_gives_a_readable_citation_key(
    data_paths: DataPaths,
    make_entry: Callable[..., Entry],
    st_messages: StMessages,
    author: str,
    stem: str,
) -> None:
    """著者を読めない文字を含む名前に更新しても、読める識別子が付く (13.1).

    更新でフォルダ名が変わると、論文は新しい識別子で登録し直される。その
    識別子は ``list.json``・session state・エントリの ``ID`` で一致し、空白・
    ``,``・``{``・``}`` を含まない。
    """
    existing = make_entry(author="Zed Original")
    paper_list, (key,) = seed_paper_list(data_paths, existing)
    assert key == "ZedOriginal2020_0"  # 前提 (新しい識別子とは別)
    updated = edited(paper_list[key], author=author)

    assert update_entry_in_list(paper_list, key, updated) is True

    state = read_state(paper_list, data_paths)
    new_key = f"{stem}2020_0"
    assert list(state.file) == [new_key]
    assert list(state.session) == [new_key]
    assert state.file[new_key]["ID"] == new_key
    assert state.file[new_key]["author"] == author
    assert_citation_key_is_readable(new_key)
    assert st_messages.error == []


# --- 登録 (要件 3.1, 3.4) ---


@pytest.mark.parametrize(("existing_changes", "new_changes"), SAME_KEY_CHANGES)
def test_register_entry_rejects_a_folder_name_that_differs_only_in_case(
    data_paths: DataPaths,
    pdf_root: Path,
    make_entry: Callable[..., Entry],
    st_messages: StMessages,
    path_space: bool,
    existing_changes: dict[str, str],
    new_changes: dict[str, str],
) -> None:
    """既存の論文と比較キーが等しいフォルダ名の登録は、重複として拒否される (3.1).

    大文字小文字だけ、または Unicode 正規化だけが違う名前も重複になる。重複
    エラーが 1 回だけ通知され、論文リストも PDF も変わらない (添付した PDF も
    保存されない)。判定は文字列だけで決まり、ファイルシステムに依存しない (3.4)。
    """
    existing = make_entry(**existing_changes)
    paper_list, _ = seed_paper_list(data_paths, existing)
    make_pdfs(existing.get_pdf_dir(pdf_root), ["00.pdf"])
    candidate = make_entry(**new_changes)
    assert_names_differ_only_in_form(existing, candidate)
    before = read_state(paper_list, data_paths)
    pdfs_before = pdf_tree(pdf_root)

    registered = register_entry_to_list(paper_list, candidate, make_uploads(2))

    assert registered is False
    assert len(st_messages.error) == 1
    assert "Duplicated" in st_messages.error[0]
    assert existing.pdf_dir_name in st_messages.error[0]
    assert st_messages.success == []
    assert read_state(paper_list, data_paths) == before
    assert pdf_tree(pdf_root) == pdfs_before


# --- 更新: 大文字小文字だけの改名 (要件 3.2, 3.4) ---


@pytest.mark.parametrize(("existing_changes", "new_changes"), SAME_KEY_CHANGES)
def test_update_accepts_a_folder_rename_that_changes_only_the_case(
    data_paths: DataPaths,
    pdf_root: Path,
    make_entry: Callable[..., Entry],
    other_entry: Entry,
    st_messages: StMessages,
    path_space: bool,
    existing_changes: dict[str, str],
    new_changes: dict[str, str],
) -> None:
    """大文字小文字だけ変わるフォルダ名の更新は受け付けられ、PDF が保たれる (3.2).

    ディスク上のフォルダの名前が新しい表記になり (古い表記も一時名も残らない)、
    中の PDF は番号も中身も保たれる。ほかの論文のフォルダは変わらない。
    論文は新しい識別子で登録し直される。
    """
    existing = make_entry(**existing_changes)
    paper_list, (key, other_key) = seed_paper_list(
        data_paths, existing, other_entry
    )
    pdfs = make_pdfs(existing.get_pdf_dir(pdf_root), ["00.pdf", "01.pdf"])
    other_pdfs = make_pdfs(other_entry.get_pdf_dir(pdf_root), ["00.pdf"])
    updated = edited(paper_list[key], **new_changes)
    assert_names_differ_only_in_form(existing, updated)
    new_dir = updated.get_pdf_dir(pdf_root)
    before = read_state(paper_list, data_paths)

    result = update_entry_in_list(paper_list, key, updated)

    assert result is True
    # ディスク上の名前そのもの (大文字小文字を区別しない環境でも表記を比べる)
    assert pdf_names(pdf_root) == sorted(
        [updated.pdf_dir_name, other_entry.pdf_dir_name]
    )
    assert pdf_names(new_dir) == ["00.pdf", "01.pdf"]
    for name, content in pdfs.items():
        assert (new_dir / name).read_bytes() == content
    other_dir = other_entry.get_pdf_dir(pdf_root)
    assert pdf_names(other_dir) == ["00.pdf"]
    assert (other_dir / "00.pdf").read_bytes() == other_pdfs["00.pdf"]
    new_key = updated["ID"]
    assert new_key != key
    expected = {
        other_key: before.file[other_key],
        new_key: {**before.file[key], **new_changes, "ID": new_key},
    }
    assert_list_is(read_state(paper_list, data_paths), expected)
    assert st_messages.error == []


def test_update_saves_new_uploads_into_the_case_renamed_folder(
    pdf_root: Path,
    entry: Entry,
    other_entry: Entry,
    stored: Stored,
    st_messages: StMessages,
) -> None:
    """大文字小文字だけ変わる更新で添付した PDF は、改名後のフォルダに追加される.

    既存の PDF は上書きされず、新しい PDF は既存の後ろの番号になる (3.2)。
    """
    updated = edited(stored.paper_list[stored.key], title="a study of testing")
    assert_names_differ_only_in_form(entry, updated)
    new_dir = updated.get_pdf_dir(pdf_root)
    uploads = make_uploads(1)

    result = update_entry_in_list(
        stored.paper_list, stored.key, updated, uploads
    )

    assert result is True
    assert pdf_names(pdf_root) == sorted(
        [updated.pdf_dir_name, other_entry.pdf_dir_name]
    )
    assert pdf_names(new_dir) == ["00.pdf", "01.pdf", "02.pdf"]
    for name, content in stored.pdfs.items():
        assert (new_dir / name).read_bytes() == content
    assert (new_dir / "02.pdf").read_bytes() == uploads[0].getvalue()
    assert st_messages.error == []


def test_update_with_a_case_only_change_works_without_a_pdf_folder(
    data_paths: DataPaths,
    pdf_root: Path,
    entry: Entry,
    other_entry: Entry,
    st_messages: StMessages,
) -> None:
    """PDF のフォルダがなくても、大文字小文字だけ変わる更新を受け付ける (3.2).

    改名するフォルダがないので何も作られず、識別子だけが新しくなる。
    """
    paper_list, (key, other_key) = seed_paper_list(
        data_paths, entry, other_entry
    )
    updated = edited(paper_list[key], title="a study of testing")
    assert not any(pdf_root.iterdir())  # 前提: PDF はどこにもない

    result = update_entry_in_list(paper_list, key, updated)

    assert result is True
    state = read_state(paper_list, data_paths)
    assert sorted(state.file) == sorted([other_key, "AliceSmith2020_1"])
    assert state.file["AliceSmith2020_1"]["title"] == "a study of testing"
    assert state.session == state.held == state.file
    assert not any(pdf_root.iterdir())
    assert st_messages.error == []


def test_update_leaves_a_duplicate_pair_alone_when_the_folder_is_unchanged(
    data_paths: DataPaths,
    pdf_root: Path,
    entry: Entry,
    make_entry: Callable[..., Entry],
    st_messages: StMessages,
) -> None:
    """フォルダ名を変えない更新は、既にある重複の組に妨げられない.

    修正前に登録された、大文字小文字だけ違う論文の組 (フォルダ名を共有しうる)
    でも、フォルダ名が変わらない更新 (タグの追加など) は受け付ける。フォルダは
    動かさず、重複を新しく作る更新ではないので拒否しない。
    """
    twin = make_entry(title=entry["title"].lower())
    paper_list, (key, twin_key) = seed_paper_list(data_paths, entry, twin)
    assert_names_differ_only_in_form(entry, twin)
    updated = edited(paper_list[key], journal="Journal of Updated Results")
    before = read_state(paper_list, data_paths)

    result = update_entry_in_list(paper_list, key, updated)

    assert result is True
    state = read_state(paper_list, data_paths)
    assert state.file[key]["journal"] == "Journal of Updated Results"
    assert state.file[twin_key] == before.file[twin_key]
    assert st_messages.error == []
    assert not any(pdf_root.iterdir())


# --- 更新: 一時名を経由する改名 (要件 3.2) ---


def test_a_case_only_rename_goes_through_a_temporary_name(
    pdf_root: Path,
    entry: Entry,
    stored: Stored,
    st_messages: StMessages,
    monkeypatch: pytest.MonkeyPatch,
    path_space: bool,
) -> None:
    """大文字小文字だけの改名は、同じ階層の一時名を経由して 2 回で行う.

    1 回目は元のフォルダを一時名へ、2 回目は一時名から新しい名前へ。
    ファイルシステムが大文字小文字を区別するかどうかにかかわらず、この
    手順は同じで、結果も同じになる (3.2, 3.4)。変換後のパスの空間にとどまる。
    """
    updated = edited(stored.paper_list[stored.key], title="a study of testing")
    calls = record_renames(monkeypatch)
    old_dir = entry.get_pdf_dir(pdf_root)
    new_dir = updated.get_pdf_dir(pdf_root)

    result = update_entry_in_list(stored.paper_list, stored.key, updated)

    assert result is True
    assert len(calls) == 2
    (first_source, first_target), (second_source, second_target) = calls
    assert first_source == old_dir
    assert second_target == new_dir
    temporary = first_target
    assert is_temporary(temporary)
    assert second_source == temporary
    assert temporary.parent == old_dir.parent
    assert pdf_names(new_dir) == ["00.pdf", "01.pdf"]
    assert not any(is_temporary(path) for path in pdf_root.iterdir())
    assert st_messages.error == []


def test_a_rename_that_changes_more_than_the_case_is_direct(
    pdf_root: Path,
    entry: Entry,
    stored: Stored,
    st_messages: StMessages,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """大文字小文字以外も変わる改名は、一時名を経由せず 1 回で行う."""
    updated = edited(
        stored.paper_list[stored.key], title="A Revised Study of Testing"
    )
    calls = record_renames(monkeypatch)

    result = update_entry_in_list(stored.paper_list, stored.key, updated)

    assert result is True
    assert calls == [
        (entry.get_pdf_dir(pdf_root), updated.get_pdf_dir(pdf_root))
    ]
    assert st_messages.error == []


def test_a_case_only_rename_is_restored_when_the_second_step_fails(
    data_paths: DataPaths,
    pdf_root: Path,
    entry: Entry,
    stored: Stored,
    st_messages: StMessages,
    monkeypatch: pytest.MonkeyPatch,
    path_space: bool,
) -> None:
    """一時名から新しい名前への改名に失敗したら、元の名前に戻してエラーを出す.

    更新は中止され (``False``)、論文リストは変わらず、PDF のフォルダは元の
    名前・元の中身に戻る。一時名のフォルダは残らない (3.2)。
    """
    updated = edited(stored.paper_list[stored.key], title="a study of testing")
    old_dir = entry.get_pdf_dir(pdf_root)
    new_dir = updated.get_pdf_dir(pdf_root)
    before = read_state(stored.paper_list, data_paths)
    pdfs_before = pdf_tree(pdf_root)
    calls = record_renames(
        monkeypatch,
        fails=lambda source, target: is_temporary(source)
        and target == new_dir,
    )

    result = update_entry_in_list(
        stored.paper_list, stored.key, updated, make_uploads(1)
    )

    assert result is False
    assert [target for _, target in calls][-1] == old_dir  # 元に戻した
    assert len(st_messages.error) == 1
    message = st_messages.error[0]
    assert "Failed to rename" in message
    assert to_display_path(old_dir) in message
    assert to_display_path(new_dir) in message
    assert "Permission denied" in message
    if path_space:
        assert str(old_dir) not in message
    assert read_state(stored.paper_list, data_paths) == before
    assert pdf_tree(pdf_root) == pdfs_before
    assert pdf_names(pdf_root) == sorted(
        [entry.pdf_dir_name, stored.paper_list[stored.other_key].pdf_dir_name]
    )


def test_a_case_only_rename_stops_when_the_first_step_fails(
    data_paths: DataPaths,
    pdf_root: Path,
    stored: Stored,
    st_messages: StMessages,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """元のフォルダを一時名へ移せなかったら、何も動かさずにエラーを出す."""
    updated = edited(stored.paper_list[stored.key], title="a study of testing")
    before = read_state(stored.paper_list, data_paths)
    pdfs_before = pdf_tree(pdf_root)
    calls = record_renames(
        monkeypatch, fails=lambda source, target: is_temporary(target)
    )

    result = update_entry_in_list(
        stored.paper_list, stored.key, updated, make_uploads(1)
    )

    assert result is False
    assert len(calls) == 1
    assert len(st_messages.error) == 1
    assert "Failed to rename" in st_messages.error[0]
    assert read_state(stored.paper_list, data_paths) == before
    assert pdf_tree(pdf_root) == pdfs_before


def test_a_case_only_rename_reports_where_the_pdfs_are_when_it_cannot_restore(
    data_paths: DataPaths,
    pdf_root: Path,
    entry: Entry,
    stored: Stored,
    st_messages: StMessages,
    monkeypatch: pytest.MonkeyPatch,
    path_space: bool,
) -> None:
    """元の名前に戻すのにも失敗したら、一時名のフォルダの場所をエラーに含める.

    PDF は一時名のフォルダに残り (失われない)、論文リストは変わらない。一時名
    は、長さと文字の制限に触れない (255 バイト以内、Windows の予約文字を含まない)。
    """
    updated = edited(stored.paper_list[stored.key], title="a study of testing")
    before = read_state(stored.paper_list, data_paths)
    record_renames(
        monkeypatch, fails=lambda source, target: is_temporary(source)
    )

    result = update_entry_in_list(stored.paper_list, stored.key, updated)

    assert result is False
    temporaries = [
        name
        for name in pdf_names(pdf_root)
        if name.startswith(TEMPORARY_PREFIX)
    ]
    assert len(temporaries) == 1
    temporary = temporaries[0]
    assert len(temporary.encode("utf-8")) <= 255
    assert not WINDOWS_RESERVED.search(temporary)
    assert temporary == temporary.rstrip(". ")
    temporary_dir = to_fs_path(pdf_root) / temporary
    assert pdf_names(temporary_dir) == ["00.pdf", "01.pdf"]
    for name, content in stored.pdfs.items():
        assert (temporary_dir / name).read_bytes() == content
    assert len(st_messages.error) == 1
    message = st_messages.error[0]
    assert to_display_path(temporary_dir) in message
    assert entry.pdf_dir_name in message
    if path_space:
        assert str(temporary_dir) not in message
    assert read_state(stored.paper_list, data_paths) == before
    # 孤立 PDF の走査が拾っても問題ない (PDF は一覧に出て、失われない)
    assert find_orphaned_pdfs(stored.paper_list) == [
        temporary_dir / "00.pdf",
        temporary_dir / "01.pdf",
    ]


def test_a_failed_direct_rename_stops_the_update(
    data_paths: DataPaths,
    pdf_root: Path,
    stored: Stored,
    st_messages: StMessages,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """通常の改名に失敗したら、更新を中止して論文リストも PDF も変えない."""
    updated = edited(
        stored.paper_list[stored.key], title="A Revised Study of Testing"
    )
    before = read_state(stored.paper_list, data_paths)
    pdfs_before = pdf_tree(pdf_root)
    record_renames(monkeypatch, fails=lambda source, target: True)

    result = update_entry_in_list(
        stored.paper_list, stored.key, updated, make_uploads(1)
    )

    assert result is False
    assert len(st_messages.error) == 1
    assert "Failed to rename" in st_messages.error[0]
    assert read_state(stored.paper_list, data_paths) == before
    assert pdf_tree(pdf_root) == pdfs_before


@pytest.mark.parametrize(
    "original",
    [
        pytest.param("2020 - Alice Smith - A Study", id="ordinary"),
        pytest.param("x" * 255, id="255-bytes"),
        pytest.param("あ" * 85, id="255-bytes-of-multibyte-characters"),
        pytest.param('a:b*c?"d<e>f|g\\h\x01i', id="reserved-characters"),
        pytest.param("ends with dots. . .", id="trailing-dots-and-spaces"),
        pytest.param("", id="empty"),
    ],
)
def test_temporary_dir_name_is_valid_on_every_file_system(
    original: str,
) -> None:
    """一時名は、長さと文字の制限に触れず、元の名前とは別の名前になる."""
    names = {_common._temporary_dir_name(original) for _ in range(20)}

    assert len(names) == 20  # 呼ぶたびに別の名前
    for name in names:
        assert name.startswith(TEMPORARY_PREFIX)
        assert len(name.encode("utf-8")) <= 255
        assert len(name) <= 255
        assert not WINDOWS_RESERVED.search(name)
        assert name == name.rstrip(". ")
        assert name != original


# --- 更新: 大文字小文字だけ違う別の論文・別のフォルダ (要件 3.3, 3.4) ---


@pytest.mark.parametrize(
    ("other_changes", "new_changes"),
    [
        pytest.param(
            {
                "author": "Bob Jones",
                "title": "Quantum Widgets",
                "year": "2019",
            },
            {
                "author": "bob jones",
                "title": "QUANTUM WIDGETS",
                "year": "2019",
            },
            id="case",
        ),
        pytest.param(
            {"title": "Caf\u00e9"}, {"title": "Cafe\u0301"}, id="nfc-vs-nfd"
        ),
        pytest.param(
            {"title": "Strasse"}, {"title": "Stra\u00dfe"}, id="casefold-ss"
        ),
    ],
)
def test_update_rejects_a_name_that_differs_only_in_form_from_another(
    data_paths: DataPaths,
    pdf_root: Path,
    entry: Entry,
    make_entry: Callable[..., Entry],
    st_messages: StMessages,
    path_space: bool,
    other_changes: dict[str, str],
    new_changes: dict[str, str],
) -> None:
    """ほかの論文と比較キーが等しくなる更新は、重複として拒否される (3.3).

    更新後のフォルダ名が、ほかの論文のフォルダ名と大文字小文字 (または Unicode
    正規化) だけ違う。重複エラーに両方の名前が示され、論文リストも PDF も変わらない
    (添付した PDF も保存されない)。判定はファイルシステムに依存しない (3.4)。
    """
    other = make_entry(**other_changes)
    paper_list, (key, _other_key) = seed_paper_list(data_paths, entry, other)
    make_pdfs(entry.get_pdf_dir(pdf_root), ["00.pdf", "01.pdf"])
    make_pdfs(other.get_pdf_dir(pdf_root), ["00.pdf"])
    updated = edited(paper_list[key], **new_changes)
    assert_names_differ_only_in_form(updated, other)
    before = read_state(paper_list, data_paths)
    pdfs_before = pdf_tree(pdf_root)

    result = update_entry_in_list(paper_list, key, updated, make_uploads(1))

    assert result is False
    assert len(st_messages.error) == 1
    message = st_messages.error[0]
    assert "Duplicated" in message
    assert updated.pdf_dir_name in message
    assert other.pdf_dir_name in message
    assert read_state(paper_list, data_paths) == before
    assert pdf_tree(pdf_root) == pdfs_before


@pytest.mark.parametrize(
    ("old_title", "new_title"),
    [
        pytest.param("A Study of Testing", "a study of testing", id="case"),
        pytest.param("Strasse", "Stra\u00dfe", id="casefold-ss"),
    ],
)
def test_update_rejects_a_case_only_rename_onto_a_different_existing_folder(
    data_paths: DataPaths,
    pdf_root: Path,
    make_entry: Callable[..., Entry],
    st_messages: StMessages,
    path_space: bool,
    old_title: str,
    new_title: str,
) -> None:
    """大文字小文字だけ違う別のフォルダが実際にあれば、改名を拒否する (3.2).

    大文字小文字を区別するファイルシステム (Linux) では、``A Study of
    Testing`` と ``a study of testing`` は別のフォルダになれる。孤立した後者
    があれば、上書き・統合せずに「既にある」エラーで拒否し、論文リストも両方の
    フォルダの中身も変えない。区別しない環境 (macOS・Windows) では 2 つの名前が
    同じフォルダを指して別のフォルダを作れないので、そのときは skip する。
    macOS の APFS は ``ß`` と ``ss`` も同じフォルダにする。
    """
    existing = make_entry(title=old_title)
    paper_list, (key,) = seed_paper_list(data_paths, existing)
    updated = edited(paper_list[key], title=new_title)
    assert_names_differ_only_in_form(existing, updated)
    own = make_pdfs(existing.get_pdf_dir(pdf_root), ["00.pdf", "01.pdf"])
    orphan_dir = updated.get_pdf_dir(pdf_root)
    orphan = make_pdfs(orphan_dir, ["00.pdf", "07.pdf"])
    if len(pdf_names(pdf_root)) != 2:
        pytest.skip("this file system treats the two names as one folder")
    before = read_state(paper_list, data_paths)
    pdfs_before = pdf_tree(pdf_root)

    result = update_entry_in_list(paper_list, key, updated, make_uploads(1))

    assert result is False
    assert len(st_messages.error) == 1
    assert "already exists" in st_messages.error[0]
    assert read_state(paper_list, data_paths) == before
    assert pdf_tree(pdf_root) == pdfs_before
    for name, content in orphan.items():
        assert (orphan_dir / name).read_bytes() == content
    own_dir = existing.get_pdf_dir(pdf_root)
    for name, content in own.items():
        assert (own_dir / name).read_bytes() == content


def test_a_link_to_the_folder_is_not_the_same_folder(tmp_path: Path) -> None:
    """元のフォルダを指すシンボリックリンクは、同じフォルダとみなさない.

    ``os.path.samefile`` はリンクをたどって同じとみなし、リンクを改名先と
    みなした改名が進んでしまう。比較は ``lstat`` の ``samestat`` で行う。
    """
    folder = tmp_path / "Folder"
    folder.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(folder, target_is_directory=True)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"symbolic links are not available: {e}")

    assert os.path.samefile(link, folder)  # たどると同じ (誤判定の原因)
    assert not _common._is_same_folder(link, folder)
    assert not _common._is_same_folder(folder, link)
    assert _common._is_same_folder(folder, folder)


def test_update_never_renames_onto_a_folder_that_is_not_the_original(
    data_paths: DataPaths,
    pdf_root: Path,
    entry: Entry,
    stored: Stored,
    st_messages: StMessages,
    monkeypatch: pytest.MonkeyPatch,
    path_space: bool,
) -> None:
    """改名先が、元のフォルダ自身でない既存のフォルダなら、何も動かさない (3.2).

    大文字小文字を区別しない環境で、改名先の名前が元のフォルダ自身を指すとき
    に、これを別のフォルダと誤判定しないことは、大文字小文字だけの改名を受け
    付けるテストが確かめる。ここでは逆に、改名先が元のフォルダ自身でない
    既存のフォルダだと判定される状況を、フォルダ自身かの判定を差し替えて
    どの環境でも再現し、改名を 1 回も試みずに拒否することを確かめる。
    """
    updated = edited(stored.paper_list[stored.key], title="a study of testing")
    assert_names_differ_only_in_form(entry, updated)
    pdf_dir = entry.get_pdf_dir(pdf_root)
    new_dir = updated.get_pdf_dir(pdf_root)
    # 区別しない環境では、新しい名前が元のフォルダ自身を指して存在する.
    # 区別する環境では、別のフォルダを作って、同じ状況にする
    if not os.path.lexists(new_dir):
        make_pdfs(new_dir, ["00.pdf"])
    monkeypatch.setattr(_common, "_is_same_folder", lambda *_: False)
    calls = record_renames(monkeypatch)
    before = read_state(stored.paper_list, data_paths)
    pdfs_before = pdf_tree(pdf_root)

    result = update_entry_in_list(
        stored.paper_list, stored.key, updated, make_uploads(1)
    )

    assert result is False
    assert calls == []
    assert len(st_messages.error) == 1
    assert "already exists" in st_messages.error[0]
    assert to_display_path(pdf_dir) in st_messages.error[0]
    assert to_display_path(new_dir) in st_messages.error[0]
    assert read_state(stored.paper_list, data_paths) == before
    assert pdf_tree(pdf_root) == pdfs_before


# --- 更新: 改名の前の移行 (要件 3.2, 2.7, 8.3) ---
# 旧形式の単一 PDF と、修正前の名前のフォルダは、PDF の一覧を取得するときに
# 現在の名前のフォルダへ移る。更新は改名の前にこの移行を済ませるので、改名後に
# 新しい名前のフォルダの PDF として見え、孤立 PDF にならない。


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"title": "a study of testing"}, id="case-only"),
        pytest.param({"title": "A Revised Study"}, id="other-name"),
    ],
)
def test_update_moves_a_legacy_single_pdf_before_renaming_the_folder(
    data_paths: DataPaths,
    pdf_root: Path,
    entry: Entry,
    st_messages: StMessages,
    changes: dict[str, str],
) -> None:
    """旧形式の単一 PDF は、改名の前にフォルダへ移り、改名後も PDF として見える.

    フォルダがまだない論文で、PDF ルート直下に旧形式の PDF がある。更新後に
    ルート直下には残らず、新しい名前のフォルダの ``00.pdf`` になり、孤立 PDF
    にならない。
    """
    paper_list, (key,) = seed_paper_list(data_paths, entry)
    legacy_pdf = write_pdf(
        pdf_root / entry.pdf_filename, PDF_BYTES + b"%legacy"
    )
    updated = edited(paper_list[key], **changes)

    result = update_entry_in_list(paper_list, key, updated)

    assert result is True
    assert pdf_names(pdf_root) == [updated.pdf_dir_name]
    assert not legacy_pdf.exists()
    assert updated.get_pdf_files() == [
        updated.get_pdf_dir(pdf_root) / "00.pdf"
    ]
    assert (updated.get_pdf_dir(pdf_root) / "00.pdf").read_bytes() == (
        PDF_BYTES + b"%legacy"
    )
    assert find_orphaned_pdfs(paper_list) == []
    assert st_messages.error == []


@pytest.mark.parametrize(
    "author",
    [
        pytest.param("a" + "A" * 187, id="case-only"),
        pytest.param("B" * 188, id="other-name"),
    ],
)
def test_update_moves_a_pre_fix_folder_before_renaming_it(
    data_paths: DataPaths,
    pdf_root: Path,
    make_entry: Callable[..., Entry],
    st_messages: StMessages,
    author: str,
) -> None:
    """修正前の名前のフォルダは、改名の前に現在の名前へ移り、改名後も見える.

    KB7 の範囲の論文で、修正前の名前のフォルダだけがある。更新後は新しい
    名前のフォルダだけが残り、中の PDF は保たれ、孤立 PDF にならない。
    """
    existing = make_entry(author="A" * 188, title="x" * 20, year="2020")
    legacy_name = existing.legacy_pdf_dir_name
    assert legacy_name is not None  # 前提: KB7 の範囲
    paper_list, (key,) = seed_paper_list(data_paths, existing)
    pdfs = make_pdfs(pdf_root / legacy_name, ["00.pdf", "01.pdf"])
    updated = edited(paper_list[key], author=author)

    result = update_entry_in_list(paper_list, key, updated)

    assert result is True
    assert pdf_names(pdf_root) == [updated.pdf_dir_name]
    new_dir = updated.get_pdf_dir(pdf_root)
    assert pdf_names(new_dir) == ["00.pdf", "01.pdf"]
    for name, content in pdfs.items():
        assert (new_dir / name).read_bytes() == content
    assert find_orphaned_pdfs(paper_list) == []
    assert st_messages.error == []


def test_update_renames_only_the_current_folder_when_the_pre_fix_one_stays(
    data_paths: DataPaths,
    pdf_root: Path,
    make_entry: Callable[..., Entry],
    st_messages: StMessages,
) -> None:
    """移す先が既にあって移されなかった修正前の名前のフォルダは、動かさない.

    現在の名前のフォルダと修正前の名前のフォルダが両方ある論文を更新すると、
    現在の名前のフォルダだけが改名される。修正前の名前のフォルダは中身ごと
    そのまま残る (PDF は失われない)。
    """
    existing = make_entry(author="A" * 188, title="x" * 20, year="2020")
    legacy_name = existing.legacy_pdf_dir_name
    assert legacy_name is not None  # 前提: KB7 の範囲
    paper_list, (key,) = seed_paper_list(data_paths, existing)
    current = make_pdfs(existing.get_pdf_dir(pdf_root), ["00.pdf"])
    legacy = make_pdfs(pdf_root / legacy_name, ["00.pdf", "01.pdf"])
    updated = edited(paper_list[key], author="B" * 188)

    result = update_entry_in_list(paper_list, key, updated)

    assert result is True
    assert pdf_names(pdf_root) == sorted([updated.pdf_dir_name, legacy_name])
    new_dir = updated.get_pdf_dir(pdf_root)
    assert (new_dir / "00.pdf").read_bytes() == current["00.pdf"]
    for name, content in legacy.items():
        assert (pdf_root / legacy_name / name).read_bytes() == content


# --- 更新: 変換後のパスの空間 (要件 1.2, 3.2) ---


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"title": "a study of testing"}, id="case-only"),
        pytest.param({"title": "A Revised Study of Testing"}, id="other-name"),
    ],
)
def test_update_renames_inside_the_converted_pdf_root(
    data_paths: DataPaths,
    entry: Entry,
    redirected_pdf_root: Path,
    st_messages: StMessages,
    changes: dict[str, str],
) -> None:
    """改名は、変換後の PDF ルートの下で行う (変換前のパスを使わない).

    変換が別のディレクトリへ写す状態で、PDF は写し先にだけある。改名 (一時名
    の経由を含む) は写し先で行われ、変換前の PDF ルートには何も作られない。
    """
    paper_list, (key,) = seed_paper_list(data_paths, entry)
    pdfs = make_pdfs(entry.get_pdf_dir(), ["00.pdf", "01.pdf"])
    assert pdf_names(redirected_pdf_root) == [entry.pdf_dir_name]  # 前提
    updated = edited(paper_list[key], **changes)

    result = update_entry_in_list(paper_list, key, updated)

    assert result is True
    assert pdf_names(redirected_pdf_root) == [updated.pdf_dir_name]
    new_dir = redirected_pdf_root / updated.pdf_dir_name
    assert pdf_names(new_dir) == ["00.pdf", "01.pdf"]
    for name, content in pdfs.items():
        assert (new_dir / name).read_bytes() == content
    assert not any(data_paths.pdf.iterdir())
    assert st_messages.error == []


# --- 論文リストの保存: save_paper_list (known-bug-fixes 要件 10.3) ---------
# 保存の直前に、ファイルの状態を確かめる。壊れていれば別名へ退避してから書き、
# 退避先を画面 (st.warning) と通知 (session state の KEY_NOTICES) に残す。
# 退避できなければ書かずにエラーを出し、False を返す (壊れたファイルを退避
# できないまま上書きすると、内容が失われるため)。ファイルがない、または正常
# (BOM 付きを含む) なら、これまでどおり書く。

# UTF-8 の BOM (古いメモ帳や PowerShell 5 が付ける)
UTF8_BOM = "\ufeff".encode("utf-8")

# 壊れた論文リストのファイルの内容. 解析できない・UTF-8 として読めない・
# 論文の一覧の形をしていない・BOM の後が壊れている、のそれぞれ.
BROKEN_LIST_BYTES = [
    pytest.param(b'{"AliceSmith2020_0": {"title": "A St', id="truncated-json"),
    pytest.param(b"", id="empty-file"),
    pytest.param(
        '{"a": {"title": "深層学習"}}'.encode("shift_jis"), id="shift-jis"
    ),
    pytest.param(b"[]", id="top-array"),
    pytest.param(b"null", id="top-null"),
    pytest.param(b'{"a": "str"}', id="entry-string"),
    pytest.param(b'{"a": {"title": 1}}', id="field-number"),
    pytest.param(UTF8_BOM + b'{"a": {"title": ', id="broken-after-bom"),
]
# 退避できない状態を作るために壊しておく内容 (どの壊れ方でもよい)
UNBACKABLE_BROKEN = b'{"AliceSmith2020_0": {"title": "A St'


def broken_backups(data_paths: DataPaths) -> list[Path]:
    """壊れた論文リストの退避先 (``list.json.broken-*``) を、名前順に返す."""
    return sorted(data_paths.root.glob("list.json.broken-*"))


def stored_fields(data_paths: DataPaths) -> dict[str, EntryFields]:
    """``list.json`` を読み直して、キー → エントリのフィールドで返す."""
    return as_fields(PaperList.from_file(data_paths.list_file))


def shown_with(messages: list[str], path: Path) -> list[str]:
    """``messages`` のうち、``path`` の表示用のパスを含むものを返す."""
    return [m for m in messages if to_display_path(path) in m]


def fail_to_copy(*args: object, **kwargs: object) -> None:
    """退避の複製に失敗させる (ディスクの空きがない)."""
    raise OSError(errno.ENOSPC, "No space left on device")


@pytest.fixture(params=["copy-fails", "read-only-directory"])
def unbackable_list(
    request: pytest.FixtureRequest,
    data_paths: DataPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[bytes]:
    """壊れた ``list.json`` を置き、退避できない状態にする.

    - ``copy-fails``: 複製 (``shutil.copy2``) が ``OSError`` で失敗する
    - ``read-only-directory``: 退避先のディレクトリが書き込み不可で、退避先の
      ファイルを作れない (権限が効かない環境では skip)

    ほかの fixture で保存済みの論文リストを作る場合は、それより後に指定する
    (この fixture が ``list.json`` を壊れた内容で置き換える).

    Returns
    -------
    bytes
        置いた ``list.json`` の内容。
    """
    data_paths.list_file.write_bytes(UNBACKABLE_BROKEN)
    if request.param == "copy-fails":
        monkeypatch.setattr(shutil, "copy2", fail_to_copy)
        yield UNBACKABLE_BROKEN
    else:
        # 論文リストの生成は PDF ディレクトリを作るので、先に作っておく
        data_paths.pdf.mkdir(exist_ok=True)
        with read_only(data_paths.root):
            yield UNBACKABLE_BROKEN


def assert_nothing_saved(
    data_paths: DataPaths, broken: bytes, st_messages: StMessages
) -> None:
    """退避できずに保存を中止したときの状態を確かめる.

    ``list.json`` は壊れた内容のバイト列のまま。退避先の一部も残らない。
    エラーが 1 つだけ画面に出て、そこに ``list.json`` の表示用のパスと理由が
    ある。退避したことの警告も通知も出ない。
    """
    assert data_paths.list_file.read_bytes() == broken
    assert broken_backups(data_paths) == []
    assert len(st_messages.error) == 1, st_messages.error
    assert str(data_paths.list_file) in st_messages.error[0]
    assert st_messages.warning == []
    assert KEY_NOTICES not in st.session_state


@pytest.mark.parametrize("broken", BROKEN_LIST_BYTES)
def test_save_backs_up_a_broken_paper_list_and_shows_where(
    data_paths: DataPaths,
    entry: Entry,
    st_messages: StMessages,
    broken: bytes,
) -> None:
    """壊れた ``list.json`` は、退避してから新しい内容で書き、退避先を出す (10.3).

    退避先には壊れた内容がそのまま残り、``list.json`` は新しい論文リストに
    なり、session state も同じ内容になる。退避先は画面の警告 1 つと、
    通知 (``KEY_NOTICES``) に残る。
    """
    data_paths.list_file.write_bytes(broken)
    paper_list = PaperList({"AliceSmith2020_0": entry})

    saved = save_paper_list(paper_list)

    assert saved is True
    (backup,) = broken_backups(data_paths)
    assert backup.read_bytes() == broken
    expected = as_fields(paper_list)
    assert stored_fields(data_paths) == expected
    assert as_fields(PaperList.from_session_state()) == expected
    assert st_messages.error == []
    shown = shown_with(st_messages.warning, backup)
    assert st_messages.warning == shown
    assert len(shown) == 1
    assert st.session_state[KEY_NOTICES] == shown


def write_not_broken_list(
    state: str, data_paths: DataPaths, other_entry: Entry
) -> None:
    """壊れていない状態の ``list.json`` を置く (``missing`` は何も置かない)."""
    previous = PaperList({"BobJones2019_0": other_entry})
    if state == "saved-by-the-app":
        previous.to_file(data_paths.list_file)
    elif state == "hand-written":
        text = json.dumps(
            {k: dict(v) for k, v in previous.items()}, separators=(",", ":")
        )
        data_paths.list_file.write_text(text, encoding="utf-8")
    elif state == "with-bom":
        previous.to_file(data_paths.list_file)
        data_paths.list_file.write_bytes(
            UTF8_BOM + data_paths.list_file.read_bytes()
        )
    else:
        assert state == "missing"


@pytest.mark.parametrize(
    "state", ["missing", "saved-by-the-app", "hand-written", "with-bom"]
)
def test_save_does_not_back_up_a_paper_list_that_is_not_broken(
    data_paths: DataPaths,
    entry: Entry,
    other_entry: Entry,
    st_messages: StMessages,
    state: str,
) -> None:
    """ファイルがない・正常 (BOM 付きを含む) なら、退避せずに書く (10.3, 10.4).

    退避先も、警告も、通知も出ない。BOM 付きだったファイルは、BOM なしの
    UTF-8 で書き直される。
    """
    write_not_broken_list(state, data_paths, other_entry)
    paper_list = PaperList({"AliceSmith2020_0": entry})

    saved = save_paper_list(paper_list)

    assert saved is True
    assert broken_backups(data_paths) == []
    assert sorted(p.name for p in data_paths.root.iterdir()) == [
        "list.json",
        "pdf",
    ]
    assert not data_paths.list_file.read_bytes().startswith(UTF8_BOM)
    assert stored_fields(data_paths) == as_fields(paper_list)
    assert as_fields(PaperList.from_session_state()) == as_fields(paper_list)
    assert st_messages.warning == []
    assert st_messages.error == []
    assert KEY_NOTICES not in st.session_state


def test_each_backup_is_noticed_separately(
    data_paths: DataPaths, entry: Entry, st_messages: StMessages
) -> None:
    """続けて 2 回壊れていたら、退避先も通知も 2 つになる (10.3).

    1 回目の通知は、次の描画まで消えずに残り、2 回目の通知が後ろに加わる。
    """
    paper_list = PaperList({"AliceSmith2020_0": entry})
    for _ in range(2):
        data_paths.list_file.write_bytes(b"[]")
        assert save_paper_list(paper_list) is True

    first, second = broken_backups(data_paths)
    notices = st.session_state[KEY_NOTICES]
    assert len(notices) == 2
    assert to_display_path(first) in notices[0]
    assert to_display_path(second) in notices[1]
    assert st_messages.warning == notices


def test_save_stops_when_the_broken_file_cannot_be_backed_up(
    data_paths: DataPaths,
    entry: Entry,
    other_entry: Entry,
    st_messages: StMessages,
    unbackable_list: bytes,
) -> None:
    """壊れたファイルを退避できなければ、書かずにエラーを出して False を返す (10.3).

    壊れたファイルを退避できないまま上書きすると内容が失われるので、
    ``list.json`` は壊れた内容のバイト列のまま。session state の論文リストも
    変えない (ファイルに保存できていない内容を、保存済みのように見せない)。
    """
    previous = PaperList({"BobJones2019_0": other_entry})
    previous.to_session_state()
    paper_list = PaperList({"AliceSmith2020_0": entry})

    saved = save_paper_list(paper_list)

    assert saved is False
    assert_nothing_saved(data_paths, unbackable_list, st_messages)
    assert as_fields(PaperList.from_session_state()) == as_fields(previous)


def test_save_reports_the_reason_when_the_backup_fails(
    data_paths: DataPaths,
    entry: Entry,
    st_messages: StMessages,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """退避に失敗した理由は、画面のエラーに出る (パスは表示用のもの)."""
    data_paths.list_file.write_bytes(UNBACKABLE_BROKEN)
    monkeypatch.setattr(shutil, "copy2", fail_to_copy)

    assert save_paper_list(PaperList({"AliceSmith2020_0": entry})) is False

    assert_nothing_saved(data_paths, UNBACKABLE_BROKEN, st_messages)
    assert "No space left on device" in st_messages.error[0]


def test_register_is_not_a_success_when_the_backup_fails(
    data_paths: DataPaths,
    entry: Entry,
    st_messages: StMessages,
    unbackable_list: bytes,
) -> None:
    """退避できずに保存できなかった登録は、失敗として扱われる (10.3).

    False を返し、成功の通知を出さない。受け取った論文リストにも、
    session state にも、登録しようとした論文を残さない。
    """
    paper_list = PaperList({})

    registered = register_entry_to_list(paper_list, entry)

    assert registered is False
    assert st_messages.success == []
    assert_nothing_saved(data_paths, unbackable_list, st_messages)
    assert list(paper_list) == []
    assert list(PaperList.from_session_state()) == []


def test_update_is_not_a_success_when_the_backup_fails(
    data_paths: DataPaths,
    entry: Entry,
    stored: Stored,
    st_messages: StMessages,
    unbackable_list: bytes,
) -> None:
    """退避できずに保存できなかった更新は、失敗として扱われる (10.3).

    False を返し、受け取った論文リストを元のままにする。
    """
    before = as_fields(stored.paper_list)
    updated = edited(entry, journal="A Different Journal")

    result = update_entry_in_list(stored.paper_list, stored.key, updated)

    assert result is False
    assert_nothing_saved(data_paths, unbackable_list, st_messages)
    assert as_fields(stored.paper_list) == before
    assert as_fields(PaperList.from_session_state()) == before


def test_register_with_a_broken_list_backs_it_up_and_succeeds(
    data_paths: DataPaths, entry: Entry, st_messages: StMessages
) -> None:
    """壊れた ``list.json`` があっても、登録は成功し、退避先が出る (10.3)."""
    data_paths.list_file.write_bytes(b'{"a": {"title": 1}}')
    paper_list = PaperList({})

    registered = register_entry_to_list(paper_list, entry)

    assert registered is True
    (backup,) = broken_backups(data_paths)
    assert backup.read_bytes() == b'{"a": {"title": 1}}'
    assert list(stored_fields(data_paths)) == ["AliceSmith2020_0"]
    assert len(st_messages.success) == 1
    assert st_messages.error == []
    assert len(shown_with(st_messages.warning, backup)) == 1
    assert st.session_state[KEY_NOTICES] == st_messages.warning


def test_update_with_a_broken_list_backs_it_up_and_succeeds(
    data_paths: DataPaths,
    entry: Entry,
    stored: Stored,
    st_messages: StMessages,
) -> None:
    """``list.json`` が壊れていても、更新は成功し、退避先が出る (10.3)."""
    data_paths.list_file.write_bytes(b"[]")
    updated = edited(entry, journal="A Different Journal")

    result = update_entry_in_list(stored.paper_list, stored.key, updated)

    assert result is True
    (backup,) = broken_backups(data_paths)
    assert backup.read_bytes() == b"[]"
    assert (
        stored_fields(data_paths)[stored.key]["journal"]
        == "A Different Journal"
    )
    assert st_messages.error == []
    assert len(shown_with(st_messages.warning, backup)) == 1


def test_delete_with_a_broken_list_backs_it_up_and_reruns(
    data_paths: DataPaths,
    stored: Stored,
    st_messages: StMessages,
    rerun_calls: list[None],
) -> None:
    """``list.json`` が壊れていても、削除は保存され、再描画する (10.3).

    退避先は、再描画で消えないよう通知に残る。
    """
    data_paths.list_file.write_bytes(b"[]")

    _delete_entry(stored.paper_list, [stored.key], False)

    (backup,) = broken_backups(data_paths)
    assert backup.read_bytes() == b"[]"
    assert list(stored_fields(data_paths)) == [stored.other_key]
    assert rerun_calls == [None]
    assert len(shown_with(st_messages.warning, backup)) == 1
    assert len(st.session_state[KEY_NOTICES]) == 1


def test_delete_does_not_rerun_when_the_backup_fails(
    data_paths: DataPaths,
    stored: Stored,
    st_messages: StMessages,
    rerun_calls: list[None],
    unbackable_list: bytes,
) -> None:
    """退避できずに保存できなかった削除は、再描画しない (10.3).

    再描画するとエラーが消えて、保存できなかったことが分からなくなる。
    """
    _delete_entry(stored.paper_list, [stored.key], False)

    assert rerun_calls == []
    assert_nothing_saved(data_paths, unbackable_list, st_messages)


# --- 手動入力フォーム: 補助 ------------------------------------------------
# custom_entry は Streamlit のウィジェットを描画するので、AppTest で実行する。
# AppTest.from_function はスクリプト関数のソースを取り出して実行するため、
# スクリプト関数は外の変数を参照できない (import も関数の中で行い、引数は
# args= で渡す)。結果は session state に書き出して、テスト側から読む。

# スクリプト関数の中で使うキーと同じ値 (スクリプトは外の定数を参照できない).
ENTRY_TYPE_KEY = "entry_type"
FORM_RESULT_KEY = "form_result"
UPLOADED_KEY = "uploaded"

# 要件 8.6 が挙げる 8 種類のエントリタイプ。
ENTRY_TYPES = (
    "article",
    "book",
    "inbook",
    "inproceedings",
    "masterthesis",
    "phdthesis",
    "techreport",
    "misc",
)

# フィールドの定義ファイル。実装が使う定数は使わず、パッケージの場所から
# 導出して独立に読み込み、表示されるフィールドの期待値にする。
FIELDS_JSON = Path(paper_manager.__file__).parent / "app" / "fields.json"

FormValue = Union[str, int, list[str]]


def custom_entry_screen(
    entry_type: str, initial: dict, unstripped: dict
) -> None:
    """``custom_entry`` を、登録ページと同じ形で呼ぶ画面 (AppTest 用).

    エントリタイプの選択に応じて ``custom_entry`` を呼び、返されたエントリを
    session state に書き出す。登録ページの手動入力フォームと同じく、
    ``ENTRYTYPE`` だけを持つエントリ (編集の場合は既存のエントリ) を渡す。

    ``Entry`` は作るときに値の前後の空白を除くので、前後の空白を持つ値は
    ``unstripped`` に渡し、作った後に設定する。
    """
    import streamlit as st

    from paper_manager.app.components import custom_entry
    from paper_manager.app.helper import MAP_FIELDS
    from paper_manager.entry import Entry

    options = tuple(MAP_FIELDS.keys())
    selected = st.selectbox(
        "エントリタイプを選択",
        options=options,
        index=options.index(entry_type),
        key="entry_type",
    )
    entry = Entry({**initial, "ENTRYTYPE": selected})
    entry.update(unstripped)
    entry = custom_entry(entry)
    st.session_state["form_result"] = dict(entry)


def run_form(
    entry_type: str = "article",
    initial: Optional[Mapping[str, str]] = None,
    unstripped: Optional[Mapping[str, str]] = None,
) -> AppTest:
    """``entry_type`` を選んだ手動入力フォームを 1 回実行する.

    Parameters
    ----------
    entry_type : str, default "article"
        選んでおくエントリタイプ。
    initial : Optional[Mapping[str, str]], default None
        フォームに渡すエントリの初期値 (``ENTRYTYPE`` 以外)。編集の場合の
        既存の値に当たる。
    unstripped : Optional[Mapping[str, str]], default None
        ``initial`` と同じだが、``Entry`` が作るときに除く前後の空白を保った
        ままの初期値。
    """
    at = AppTest.from_function(
        custom_entry_screen,
        default_timeout=APP_TIMEOUT_SECONDS,
        args=(entry_type, dict(initial or {}), dict(unstripped or {})),
    )
    return at.run()


def form_result(at: AppTest) -> dict[str, str]:
    """画面が例外なく実行されたことを確かめ、``custom_entry`` の結果を返す."""
    assert not at.exception, [e.value for e in at.exception]
    return dict(at.session_state[FORM_RESULT_KEY])


def displayed_fields(at: AppTest) -> dict[str, str]:
    """表示されているフィールドの名前 → ウィジェットの種類を返す.

    エントリタイプの選択欄は含まない。フィールド名はウィジェットのキー。
    """
    kinds = {
        "text_input": at.text_input,
        "number_input": at.number_input,
        "multiselect": at.multiselect,
    }
    fields: dict[str, str] = {}
    for kind, elements in kinds.items():
        for element in elements:
            assert element.key not in fields, f"duplicated: {element.key}"
            fields[element.key] = kind
    return fields


def fill(at: AppTest, values: Mapping[str, FormValue]) -> AppTest:
    """フィールドに値を入力して、画面を再実行する.

    Parameters
    ----------
    at : AppTest
        実行済みの画面。
    values : Mapping[str, FormValue]
        フィールド名 → 入力する値。``str`` はテキスト入力、``int`` は年の
        数値入力、``list`` は複数選択 (著者・タグ) の選択内容。
    """
    for field, value in values.items():
        if isinstance(value, str):
            at.text_input(key=field).input(value)
        elif isinstance(value, int):
            at.number_input(key=field).set_value(value)
        else:
            at.multiselect(key=field).set_value(value)
    return at.run()


def fields_of(entry_type: str) -> set[str]:
    """定義ファイルから、``entry_type`` のフィールド名の集合を読む."""
    definitions = json.loads(FIELDS_JSON.read_text(encoding="utf-8"))
    return set(definitions[entry_type])


# --- 手動入力フォーム: custom_entry (要件 9.11) ----------------------------
# 年は、必須のタイプ (article など) では空のとき実行日の年が既定値になる。
# 期待値を実行日に依存させないため、必須のタイプでは年を明示的に入力するか
# 初期値に与え、既定値の年は検証しない。


@pytest.mark.parametrize("entry_type", ENTRY_TYPES)
def test_form_shows_the_fields_of_the_selected_entry_type(
    entry_type: str,
) -> None:
    """選んだエントリタイプのフィールドが、過不足なく表示される (要件 9.11).

    表示されるフィールドは、定義ファイルを独立に読み込んだ結果と一致する。
    """
    at = run_form(entry_type)

    form_result(at)  # 例外なく実行された
    assert set(displayed_fields(at)) == fields_of(entry_type)


@pytest.mark.parametrize(
    "entry_type, shown, hidden",
    [
        ("article", ["journal"], ["publisher", "editor", "booktitle"]),
        ("book", ["publisher", "editor"], ["journal", "booktitle"]),
        ("inproceedings", ["booktitle"], ["journal", "school"]),
        ("misc", ["howpublished"], ["journal", "publisher", "booktitle"]),
    ],
)
def test_form_shows_only_the_fields_that_belong_to_the_type(
    entry_type: str, shown: list[str], hidden: list[str]
) -> None:
    """エントリタイプに固有のフィールドだけが表示される (要件 9.11)."""
    at = run_form(entry_type)

    form_result(at)
    fields = displayed_fields(at)
    assert all(field in fields for field in shown)
    assert not any(field in fields for field in hidden)


def test_form_replaces_the_fields_when_the_entry_type_changes() -> None:
    """エントリタイプを選び直すと、フィールドが選んだタイプのものに変わる.

    (要件 9.11)
    """
    at = run_form("article")
    assert "journal" in displayed_fields(at)

    at.selectbox(key=ENTRY_TYPE_KEY).select("book").run()

    form_result(at)
    fields = displayed_fields(at)
    assert set(fields) == fields_of("book")
    assert "publisher" in fields
    assert "journal" not in fields


def test_form_uses_a_widget_suited_to_each_field() -> None:
    """年は数値入力、著者とタグは複数選択、ほかはテキスト入力になる.

    (要件 9.11)
    """
    at = run_form("article")

    form_result(at)
    assert displayed_fields(at) == {
        "author": "multiselect",
        "title": "text_input",
        "journal": "text_input",
        "year": "number_input",
        "volume": "text_input",
        "number": "text_input",
        "pages": "text_input",
        "month": "text_input",
        "note": "text_input",
        "url": "text_input",
        "DOI": "text_input",
        "tags": "multiselect",
    }


@pytest.mark.parametrize(
    "entry_type, values, expected",
    [
        pytest.param(
            "article",
            {
                "author": ["Alice Smith"],
                "title": "A Study of Forms",
                "journal": "Journal of Forms",
                "year": 2019,
                "volume": "12",
                "tags": ["deep learning", "vision"],
            },
            {
                "ENTRYTYPE": "article",
                "author": "Alice Smith",
                "title": "A Study of Forms",
                "journal": "Journal of Forms",
                "year": "2019",
                "volume": "12",
                "tags": "deep learning, vision",
            },
            id="article",
        ),
        pytest.param(
            "book",
            {
                "author": ["Bob Jones"],
                "title": "The Book of Forms",
                "publisher": "Widget Press",
                "editor": "Carol Lee",
                "year": 2018,
            },
            {
                "ENTRYTYPE": "book",
                "author": "Bob Jones",
                "title": "The Book of Forms",
                "publisher": "Widget Press",
                "editor": "Carol Lee",
                "year": "2018",
            },
            id="book",
        ),
        pytest.param(
            "misc",
            {"title": "A Note on Forms", "howpublished": "Web", "year": 2017},
            {
                "ENTRYTYPE": "misc",
                "title": "A Note on Forms",
                "howpublished": "Web",
                "year": "2017",
            },
            id="misc",
        ),
    ],
)
def test_form_reflects_the_entered_values_in_the_entry(
    entry_type: str,
    values: Mapping[str, FormValue],
    expected: dict[str, str],
) -> None:
    """入力した値がエントリに反映される (要件 9.11).

    年は文字列で保存される。複数のタグは ``, `` で連結される。入力しな
    かったフィールドはエントリに現れない。
    """
    at = run_form(entry_type)

    fill(at, values)

    assert form_result(at) == expected


_FILLED_ENTRY = {
    "ID": "Smith2020",
    "author": "Alice Smith",
    "title": "A Study of Forms",
    "journal": "Journal of Forms",
    "year": "2020",
    "volume": "12",
    "tags": "vision",
}


def test_form_prefills_the_widgets_and_keeps_the_entry_as_it_is() -> None:
    """既存のエントリを渡すと、値が入力欄に入り、結果は元のエントリと同じ.

    編集フォームの使い方に当たる。フォームにないキー (``ID``) も保たれる
    (要件 9.11)。
    """
    at = run_form("article", _FILLED_ENTRY)

    assert at.text_input(key="title").value == "A Study of Forms"
    assert at.text_input(key="journal").value == "Journal of Forms"
    assert at.number_input(key="year").value == 2020
    assert at.multiselect(key="author").value == ["Alice Smith"]
    assert at.multiselect(key="tags").value == ["vision"]
    assert form_result(at) == {"ENTRYTYPE": "article", **_FILLED_ENTRY}


def test_form_replaces_a_prefilled_value_with_the_input() -> None:
    """初期値のあるフィールドに入力すると、入力した値に置き換わる.

    ほかのフィールドは変わらない (要件 9.11)。
    """
    at = run_form("article", _FILLED_ENTRY)

    fill(at, {"title": "A Revised Study", "year": 2018})

    assert form_result(at) == {
        **_FILLED_ENTRY,
        "ENTRYTYPE": "article",
        "title": "A Revised Study",
        "year": "2018",
    }


@pytest.mark.parametrize(
    "field, cleared",
    [
        ("title", ""),
        ("journal", ""),
        ("volume", ""),
        ("author", []),
        ("tags", []),
    ],
)
def test_form_removes_a_field_that_is_emptied(
    field: str, cleared: FormValue
) -> None:
    """空欄にした項目は、エントリから除かれる (要件 9.11).

    空の文字列として残るのではなく、キーごと無くなる。ほかのフィールドは
    変わらない。
    """
    at = run_form("article", _FILLED_ENTRY)

    fill(at, {field: cleared})

    result = form_result(at)
    assert field not in result
    assert result == {
        "ENTRYTYPE": "article",
        **{k: v for k, v in _FILLED_ENTRY.items() if k != field},
    }


def test_form_removes_an_empty_year() -> None:
    """年が空欄の項目は、エントリから除かれる (要件 9.11).

    AppTest は数値入力を空に戻せない (``set_value(None)`` は反映されない)
    ので、最初から空の年を持つエントリで確かめる。年が必須でない ``misc``
    を使うので、既定値の年 (実行日の年) は入らない。
    """
    at = run_form("misc", {"title": "A Note on Forms", "year": ""})

    assert at.number_input(key="year").value is None
    assert form_result(at) == {"ENTRYTYPE": "misc", "title": "A Note on Forms"}


def test_form_adds_only_the_fields_that_were_filled_in() -> None:
    """何も入力していないフィールドは、エントリに加わらない (要件 9.11)."""
    at = run_form("misc")
    assert form_result(at) == {"ENTRYTYPE": "misc"}

    fill(at, {"title": "A Note on Forms"})

    assert form_result(at) == {"ENTRYTYPE": "misc", "title": "A Note on Forms"}


# --- 手動入力フォーム: 著者とタグ (known-bug-fixes 要件 4) -----------------
# 著者は `` and `` (AUTHOR_SEPARATOR) で、タグは ``, `` (TAG_SEPARATOR) で、
# 既定値を選択肢に分け、保存するときに連結する。分割と連結は往復で元の文字列
# に戻るので、触らずに保存しても表記は変わらない。


def test_form_joins_several_authors_with_and() -> None:
    """複数の著者は、BibTeX の著者区切り `` and `` で連結される (KB4a)."""
    at = run_form("article", {"year": "2020"})

    fill(at, {"author": ["Alice Smith", "Bob Jones"]})

    assert form_result(at)["author"] == "Alice Smith and Bob Jones"


def test_form_splits_the_default_tags_on_commas() -> None:
    """既定値のタグは `, ` で分割され、別々の選択肢になる (KB4b)."""
    at = run_form("article", {"year": "2020", "tags": "x, y"})
    assert not at.exception, [e.value for e in at.exception]

    tags = at.multiselect(key="tags")
    assert tags.options == ["x", "y"]
    assert tags.value == ["x", "y"]


@pytest.mark.parametrize(
    "author, options",
    [
        pytest.param("Alice Smith", ["Alice Smith"], id="one"),
        pytest.param(
            "Alice Smith and Bob Jones",
            ["Alice Smith", "Bob Jones"],
            id="two",
        ),
        pytest.param("A and B and C", ["A", "B", "C"], id="three"),
        pytest.param(
            "Smith, Alice and Jones, Bob",
            ["Smith, Alice", "Jones, Bob"],
            id="last-name-first",
        ),
        pytest.param(
            "Brand and Sand", ["Brand", "Sand"], id="and-inside-a-name"
        ),
        pytest.param("A and A", ["A", "A"], id="repeated-name"),
        pytest.param("A  and  B", ["A ", " B"], id="extra-spaces"),
        pytest.param("A and  and B", ["A", "", "B"], id="doubled-separator"),
    ],
)
def test_form_keeps_and_separated_authors_that_are_not_changed(
    author: str, options: list[str]
) -> None:
    """`` and `` 区切りの著者は、変更せずに保存しても表記が変わらない.

    1 人ずつの選択肢に分けて表示し (要件 4.1)、再結合した結果は元の文字列
    と完全に一致する (要件 4.2)。空白や重複した `` and `` を含む入力も同じ。
    """
    at = run_form("article", {"year": "2020", "author": author})

    assert form_result(at)["author"] == author
    assert at.multiselect(key="author").options == options
    assert at.multiselect(key="author").value == options


@pytest.mark.parametrize(
    "author, options",
    [
        pytest.param(
            " Alice and Bob ", [" Alice", "Bob "], id="surrounding-spaces"
        ),
        pytest.param("A and B ", ["A", "B "], id="trailing-space"),
        pytest.param(" and A", ["", "A"], id="leading-separator"),
        pytest.param("A and ", ["A", ""], id="trailing-separator"),
    ],
)
def test_form_keeps_authors_with_surrounding_spaces_as_they_are(
    author: str, options: list[str]
) -> None:
    """前後の空白や区切りを持つ著者も、変更せずに保存して変わらない (要件 4.2).

    ``Entry`` を作るときは前後の空白が除かれるので、作った後に設定した値
    で確かめる。
    """
    at = run_form("article", {"year": "2020"}, unstripped={"author": author})

    assert form_result(at)["author"] == author
    assert at.multiselect(key="author").options == options


@pytest.mark.parametrize(
    "author",
    [
        pytest.param("Alice Smith, Bob Jones", id="comma-separated"),
        pytest.param("King, Jr., Martin", id="suffix"),
        pytest.param("Smith, Alice, Jones, Bob", id="last-name-first"),
        pytest.param("Alice Smith,Bob Jones", id="no-space-after-comma"),
    ],
)
def test_form_keeps_comma_separated_authors_as_they_are(author: str) -> None:
    """既に `, ` 区切りで保存されている著者は、書き換えられない (要件 4.4).

    `` and `` で分割されず、1 つの選択肢として、そのまま保存される。
    接尾辞付きの人名 (``King, Jr., Martin``) の `, ` を区切りと誤認して
    壊すこともない。
    """
    at = run_form("article", {"year": "2020", "author": author})

    assert form_result(at)["author"] == author
    assert at.multiselect(key="author").options == [author]


def test_form_adds_an_author_to_comma_separated_authors_with_and() -> None:
    """`, ` 区切りの著者に著者を加えても、既存の部分は書き換えない.

    既存の著者は 1 つの選択肢のまま残り、加えた著者が `` and `` で続く
    (要件 4.1, 4.4)。
    """
    at = run_form(
        "article", {"year": "2020", "author": "Alice Smith, Bob Jones"}
    )

    fill(at, {"author": ["Alice Smith, Bob Jones", "Carol Lee"]})

    assert form_result(at)["author"] == "Alice Smith, Bob Jones and Carol Lee"


def test_form_joins_the_edited_authors_with_and() -> None:
    """既存の著者を増減すると、残った著者が `` and `` で連結される (要件 4.1)."""
    at = run_form(
        "article", {"year": "2020", "author": "Alice Smith and Bob Jones"}
    )

    fill(at, {"author": ["Bob Jones", "Carol Lee"]})

    assert form_result(at)["author"] == "Bob Jones and Carol Lee"


def test_form_keeps_the_pdf_folder_name_of_an_entry_with_several_authors() -> (
    None
):
    """複数の著者を持つ論文を編集して保存しても、PDF フォルダ名が変わらない.

    PDF フォルダ名は筆頭著者から作る。著者の区切りが `, ` に変わると、
    筆頭著者が全員の連結になり、フォルダ名が変わってしまう (KB4a。要件 4.2)。
    """
    original = Entry(
        {
            "ENTRYTYPE": "article",
            "ID": "Smith2020",
            "author": "Alice Smith and Bob Jones",
            "title": "A Study of Forms",
            "journal": "Journal of Forms",
            "year": "2020",
        }
    )

    at = run_form("article", dict(original))

    saved = Entry(form_result(at))
    assert saved["author"] == original["author"]
    assert saved.pdf_dir_name == original.pdf_dir_name


@pytest.mark.parametrize(
    "tags, options",
    [
        pytest.param("vision", ["vision"], id="one"),
        pytest.param("x, y", ["x", "y"], id="two"),
        pytest.param(
            "deep learning, vision, nlp",
            ["deep learning", "vision", "nlp"],
            id="three",
        ),
        pytest.param(
            "R and D, vision", ["R and D", "vision"], id="and-inside-a-tag"
        ),
        pytest.param("x, x", ["x", "x"], id="repeated-tag"),
        pytest.param("x,  y", ["x", " y"], id="extra-space"),
        pytest.param("x,y", ["x,y"], id="no-space-after-comma"),
    ],
)
def test_form_keeps_tags_that_are_not_changed(
    tags: str, options: list[str]
) -> None:
    """タグは `, ` で選択肢に分かれ、変更せずに保存しても表記が変わらない.

    (KB4b。要件 4.3)
    """
    at = run_form("article", {"year": "2020", "tags": tags})

    assert form_result(at)["tags"] == tags
    assert at.multiselect(key="tags").options == options
    assert at.multiselect(key="tags").value == options


def test_form_joins_the_edited_tags_with_commas() -> None:
    """既存のタグを増減すると、残ったタグが `, ` で連結される (要件 4.3)."""
    at = run_form("article", {"year": "2020", "tags": "x, y, z"})

    fill(at, {"tags": ["y", "z", "w"]})

    assert form_result(at)["tags"] == "y, z, w"


# --- 手動入力フォーム: 保存されている年が無効なとき (要件 7) ----------------
# 保存されている年が、整数として往復しない、または数値入力で入力できる範囲
# (1000 〜 来年) の外のときは、警告を出して文字の入力欄に切り替える。有効な
# 年や空の年は、これまでどおり数値入力のまま。

INVALID_YEARS = [
    pytest.param("n.d.", id="not-a-number"),
    pytest.param("2020a", id="trailing-letter"),
    pytest.param("999", id="below-the-range"),
    pytest.param("3000", id="above-the-range"),
    pytest.param("0", id="zero"),
    pytest.param("-2020", id="negative"),
    pytest.param("+2020", id="plus-sign"),
    pytest.param("02020", id="leading-zero"),
    pytest.param("2020.0", id="float-notation"),
    pytest.param("2020-2021", id="range-of-years"),
    pytest.param("２０２０", id="full-width-digits"),
    pytest.param("9" * 30, id="huge-number"),
]


@pytest.mark.parametrize(
    "value, expected",
    [
        pytest.param("2020", 2020, id="year"),
        pytest.param(2020, 2020, id="int"),
        pytest.param("1000", 1000, id="lower-limit"),
        pytest.param("2030", 2030, id="upper-limit"),
        pytest.param("999", None, id="below-the-lower-limit"),
        pytest.param("2031", None, id="above-the-upper-limit"),
        pytest.param("n.d.", None, id="not-a-number"),
        pytest.param("", None, id="empty"),
        pytest.param(" 2020", None, id="leading-space"),
        pytest.param("2020 ", None, id="trailing-space"),
        pytest.param("+2020", None, id="plus-sign"),
        pytest.param("02020", None, id="leading-zero"),
        pytest.param("2_020", None, id="underscore"),
        pytest.param("２０２０", None, id="full-width-digits"),
        pytest.param("٢٠٢٠", None, id="arabic-indic-digits"),
        pytest.param(2020.0, None, id="float"),
        pytest.param(True, None, id="bool"),
        pytest.param("9" * 5000, None, id="beyond-the-int-conversion-limit"),
    ],
)
def test_a_saved_year_is_valid_only_if_it_round_trips_within_the_range(
    value: object, expected: Optional[int]
) -> None:
    """保存されている年は、整数として往復し、範囲内のときだけ有効 (要件 7).

    範囲は 1000 〜 2030 (数値入力の ``min_value`` / ``max_value`` に当たる)。
    ``int()`` が受け付けても元の文字列に戻らない値 (前後の空白、符号、
    先頭の 0、全角の数字など) は無効。数値入力に渡すと、保存されている年が
    別の文字列に書き換わる。
    """
    assert _common._as_valid_year(value, 2030) == expected


@pytest.mark.parametrize("entry_type", ["article", "misc"])
@pytest.mark.parametrize("year", INVALID_YEARS)
def test_form_opens_with_an_invalid_saved_year_as_a_text_input(
    entry_type: str, year: str
) -> None:
    """無効な年の論文でも、警告を出し、文字の入力欄で編集フォームが開く.

    年は元の値のまま入力欄に入る (要件 7.1)。
    """
    at = run_form(entry_type, {"title": "A Study of Forms", "year": year})

    form_result(at)  # 例外なく実行された
    assert displayed_fields(at)["year"] == "text_input"
    assert at.text_input(key="year").value == year
    assert any(year in warning.value for warning in at.warning)


@pytest.mark.parametrize("entry_type", ["article", "misc"])
@pytest.mark.parametrize("year", INVALID_YEARS)
def test_form_keeps_an_invalid_saved_year_when_it_is_not_changed(
    entry_type: str, year: str
) -> None:
    """無効な年の論文を、年に触れずに保存しても、元の年と他の項目が保たれる.

    (要件 7.2)
    """
    initial = {"title": "A Study of Forms", "year": year}
    at = run_form(entry_type, initial)

    assert form_result(at) == {"ENTRYTYPE": entry_type, **initial}


@pytest.mark.parametrize("year", [" 2020", "2020 ", " n.d. ", "   "])
def test_form_keeps_an_invalid_saved_year_with_surrounding_spaces(
    year: str,
) -> None:
    """前後の空白を持つ年も、整数として往復しないので無効で、触らなければ保たれる.

    ``Entry`` を作るときは前後の空白が除かれるので、作った後に設定した値
    で確かめる (要件 7.1, 7.2)。
    """
    at = run_form(
        "misc", {"title": "A Note on Forms"}, unstripped={"year": year}
    )

    assert displayed_fields(at)["year"] == "text_input"
    assert at.text_input(key="year").value == year
    assert form_result(at) == {
        "ENTRYTYPE": "misc",
        "title": "A Note on Forms",
        "year": year,
    }


@pytest.mark.parametrize("entry_type", ["article", "misc"])
@pytest.mark.parametrize("year", INVALID_YEARS)
def test_form_saves_the_corrected_year(entry_type: str, year: str) -> None:
    """無効な年を入力し直すと、入力した年で保存される (要件 7.3).

    ほかの項目は変わらない。
    """
    initial = {"title": "A Study of Forms", "year": year}
    at = run_form(entry_type, initial)

    fill(at, {"year": "2019"})

    assert form_result(at) == {
        "ENTRYTYPE": entry_type,
        "title": "A Study of Forms",
        "year": "2019",
    }


def test_form_saves_a_corrected_year_without_surrounding_spaces() -> None:
    """入力し直した年の前後の空白は除かれる (``Entry`` の値の扱いと同じ)."""
    at = run_form("misc", {"year": "n.d."})

    fill(at, {"year": "  2019 "})

    assert form_result(at)["year"] == "2019"


def test_form_saves_what_is_typed_even_when_it_is_still_not_a_valid_year() -> (
    None
):
    """入力し直した値が無効な年のままでも、入力した値で保存される.

    入力欄は文字なので値を拒否しない。保存されている年が無効な間は、警告が
    出続ける (要件 7.3)。
    """
    at = run_form("misc", {"year": "n.d."})

    fill(at, {"year": "c. 2019"})

    assert form_result(at)["year"] == "c. 2019"
    assert at.warning


@pytest.mark.parametrize("cleared", ["", "   "])
def test_form_removes_the_year_that_is_emptied_in_the_text_input(
    cleared: str,
) -> None:
    """文字の入力欄で年を空欄 (空白だけを含む) にすると、年が除かれる.

    ほかの項目と同じ扱い。年が必須のタイプでは、年がないので、登録・更新の
    既存の必須項目の検証が拒否する。
    """
    initial = {**_FILLED_ENTRY, "year": "n.d."}
    at = run_form("article", initial)

    fill(at, {"year": cleared})

    result = form_result(at)
    assert "year" not in result
    assert MAP_REQUIRED_FIELDS["article"] - set(result) == {"year"}
    assert result == {
        "ENTRYTYPE": "article",
        **{k: v for k, v in _FILLED_ENTRY.items() if k != "year"},
    }


def test_form_removes_an_optional_year_that_is_emptied() -> None:
    """年が必須でないタイプでは、年を空にして保存すると年が除かれる."""
    at = run_form("misc", {"title": "A Note on Forms", "year": "n.d."})

    fill(at, {"year": ""})

    assert form_result(at) == {"ENTRYTYPE": "misc", "title": "A Note on Forms"}


@pytest.mark.parametrize("year", ["1000", "1999", "2020", "2025"])
def test_form_keeps_the_number_input_for_a_valid_saved_year(year: str) -> None:
    """有効な年 (整数として往復し、範囲内) は、警告なしの数値入力のまま.

    保存される値も変わらない (要件 7 は無効な年だけを変える)。
    """
    at = run_form("article", {**_FILLED_ENTRY, "year": year})

    assert form_result(at)["year"] == year
    assert displayed_fields(at)["year"] == "number_input"
    assert at.number_input(key="year").value == int(year)
    assert not at.warning


@pytest.fixture
def frozen_today(monkeypatch: pytest.MonkeyPatch) -> date:
    """``custom_entry`` が見る今日の日付を 2030-06-15 に固定する.

    年の範囲の上限 (来年) を、テストの実行日に依存させないため。
    """

    class FrozenDate(date):
        @classmethod
        def today(cls) -> "FrozenDate":
            return cls(2030, 6, 15)

    monkeypatch.setattr(_common, "date", FrozenDate)
    return FrozenDate.today()


@pytest.mark.parametrize(
    "year, widget",
    [
        ("999", "text_input"),
        ("1000", "number_input"),
        ("2031", "number_input"),
        ("2032", "text_input"),
    ],
)
def test_form_switches_the_year_input_at_the_edges_of_the_range(
    frozen_today: date, year: str, widget: str
) -> None:
    """数値入力で入力できる範囲 (1000 〜 来年) の端で入力欄が切り替わる.

    今日が 2030 年なら、範囲は 1000 〜 2031。範囲の中の端は数値入力、すぐ
    外は警告つきの文字の入力欄になる (要件 7.1)。
    """
    at = run_form("article", {**_FILLED_ENTRY, "year": year})

    assert form_result(at)["year"] == year
    assert displayed_fields(at)["year"] == widget
    assert bool(at.warning) == (widget == "text_input")


def year_switch_screen(initials: list) -> None:
    """選んだ論文の ``custom_entry`` を出す画面 (AppTest 用).

    編集する論文を選び直したときに、同じキー ``year`` の入力欄が数値入力と
    文字の入力欄のあいだで切り替わっても動くことを確かめるための画面。
    """
    import streamlit as st

    from paper_manager.app.components import custom_entry
    from paper_manager.entry import Entry

    index = st.selectbox("論文", options=range(len(initials)), key="which")
    entry = custom_entry(Entry({**initials[index], "ENTRYTYPE": "misc"}))
    st.session_state["form_result"] = dict(entry)


def test_form_follows_the_year_when_another_entry_is_selected() -> None:
    """選ぶ論文を変えると、その論文の年に合った入力欄に切り替わる.

    無効な年 → 有効な年 → 別の無効な年と選び直しても、例外にならず、元の
    値が保たれる (要件 7.1, 7.2)。
    """
    initials = [
        {"title": "First", "year": "n.d."},
        {"title": "Second", "year": "2020"},
        {"title": "Third", "year": "2020a"},
    ]
    at = AppTest.from_function(
        year_switch_screen,
        default_timeout=APP_TIMEOUT_SECONDS,
        args=(initials,),
    ).run()

    for index in (0, 1, 2, 1, 0):
        at.selectbox(key="which").select(index).run()

        result = form_result(at)
        expected = initials[index]["year"]
        assert result["year"] == expected
        valid = expected == "2020"
        assert displayed_fields(at)["year"] == (
            "number_input" if valid else "text_input"
        )
        assert bool(at.warning) != valid


def test_form_keeps_the_year_entered_across_reruns() -> None:
    """年のない新しいエントリで入力した年は、画面が再実行されても保たれる.

    登録ページの手動入力の使い方に当たる (数値入力のまま、要件 7 の対象外)。
    """
    at = run_form("misc")
    fill(at, {"year": 2019})

    at.run()

    assert form_result(at) == {"ENTRYTYPE": "misc", "year": "2019"}
    assert displayed_fields(at)["year"] == "number_input"
    assert not at.warning


# --- PDF アップロード欄: pdf_upload_form (要件 9.12) -----------------------
# アップロード欄は fake_file_uploader で、ラベルの接頭辞に応じて未選択・
# 単体・一覧を返す処理に差し替える。ラベルが一致しないと本来の動作になる
# ので、実装のラベルが変わるとテストが失敗する。

UPLOADER_LABEL = "PDF file(s) (.pdf)"


def pdf_upload_screen(accept_multiple: bool) -> None:
    """``pdf_upload_form`` を呼んで、返された一覧を書き出す画面 (AppTest 用)."""
    import streamlit as st

    from paper_manager.app.components import pdf_upload_form

    result = pdf_upload_form(accept_multiple=accept_multiple)
    st.session_state["uploaded"] = (type(result).__name__, len(result))


@pytest.mark.parametrize("accept_multiple", [True, False])
def test_pdf_upload_form_returns_an_empty_list_when_nothing_is_selected(
    fake_file_uploader: Callable[[Mapping[str, Uploads]], None],
    accept_multiple: bool,
) -> None:
    """ファイル未選択のときは、空の一覧が返る (要件 9.12)."""
    fake_file_uploader({UPLOADER_LABEL: None})

    result = pdf_upload_form(accept_multiple=accept_multiple)

    assert result == []
    assert isinstance(result, list)


@pytest.mark.parametrize("accept_multiple", [True, False])
def test_pdf_upload_form_returns_an_empty_list_from_the_real_widget(
    accept_multiple: bool,
) -> None:
    """本物のアップロード欄でも、ファイル未選択なら空の一覧が返る.

    アップロード欄を差し替えず、画面テストで実行する (要件 9.12)。
    """
    at = AppTest.from_function(
        pdf_upload_screen,
        default_timeout=APP_TIMEOUT_SECONDS,
        args=(accept_multiple,),
    )
    at.run()

    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state[UPLOADED_KEY] == ("list", 0)


@pytest.mark.parametrize("accept_multiple", [True, False])
def test_pdf_upload_form_returns_a_list_for_a_single_file(
    fake_file_uploader: Callable[[Mapping[str, Uploads]], None],
    accept_multiple: bool,
) -> None:
    """ファイルを 1 つだけ選んだときも、1 件の一覧として返る (要件 9.12)."""
    [upload] = make_uploads(1)
    fake_file_uploader({UPLOADER_LABEL: upload})

    result = pdf_upload_form(accept_multiple=accept_multiple)

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0] is upload


@pytest.mark.parametrize("count", [1, 3])
def test_pdf_upload_form_returns_every_selected_file_in_order(
    fake_file_uploader: Callable[[Mapping[str, Uploads]], None],
    count: int,
) -> None:
    """複数のファイルを選んだときは、選んだ順の一覧がそのまま返る.

    1 件だけの一覧も、一覧のまま返る (要件 9.12)。
    """
    uploads = make_uploads(count)
    fake_file_uploader({UPLOADER_LABEL: uploads})

    result = pdf_upload_form(accept_multiple=True)

    assert isinstance(result, list)
    assert len(result) == count
    assert all(got is sent for got, sent in zip(result, uploads))


@pytest.mark.parametrize("accept_multiple", [True, False])
def test_pdf_upload_form_asks_for_pdf_files(
    monkeypatch: pytest.MonkeyPatch, accept_multiple: bool
) -> None:
    """アップロード欄は PDF だけを受け付け、複数選択の指定を渡す (要件 9.12)."""
    calls: list[tuple[str, dict[str, Any]]] = []

    def record(label: str, *args: Any, **kwargs: Any) -> None:
        calls.append((label, kwargs))

    monkeypatch.setattr(_common.st, "file_uploader", record)

    pdf_upload_form(accept_multiple=accept_multiple)

    [(label, kwargs)] = calls
    assert label == UPLOADER_LABEL
    assert kwargs["type"] == "pdf"
    assert kwargs["accept_multiple_files"] is accept_multiple


def test_pdf_upload_form_accepts_multiple_files_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """引数を省くと、複数選択を受け付ける (要件 9.12)."""
    calls: list[dict[str, Any]] = []

    def record(label: str, *args: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(_common.st, "file_uploader", record)

    pdf_upload_form()

    assert [kwargs["accept_multiple_files"] for kwargs in calls] == [True]
