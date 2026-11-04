"""バックアップ・復元 (``app.helper._backup``) の単体テスト.

対象は次のとおり。

- バックアップ zip の内容 (バージョン情報・論文リスト・設定・すべての PDF
  のデータディレクトリからの相対パス) (要件 6.1)
- 論文リスト・設定・PDF の一部が存在しなくても、存在するものだけで作成
  できること (要件 6.2)
- 出力先を指定した場合は zip ファイルが作られてそのパスが返り、指定しな
  かった場合はファイルを作らずにメモリ上の zip が返ること (要件 6.3)
- バックアップを空のデータディレクトリへ復元すると、論文リスト・設定・
  PDF の構成とバイト列が元と一致すること (要件 6.4)
- 復元先に既存の論文リストまたは設定があるとき、復元前の内容が
  ``.json.backup`` として退避されること (要件 6.5)
- 復元の成功時に、成功を示す結果と復元した PDF の件数を含むメッセージが
  返ること (要件 6.6)
- zip として不正なファイルの復元が失敗を返し、データディレクトリが変更
  されないこと (要件 6.7)
- バックアップ情報の取得 (要件 6.8)
- 復元のメンバー名の検証 (known-bug-fixes の要件 9.1, 9.2。既知バグ KB3 の
  修正): 絶対パス・ドライブ指定・``..`` の成分・空の名前を 1 つでも含む
  zip は、何も書かずに中止して失敗を返す。正当なバックアップ (日本語名、
  ``\\`` を含むフォルダ名、旧形式の ``pdf/<名前>.pdf``) は受け付ける
- バックアップの作成と復元が、変換後のパス (``to_fs_path``) を起点に行われる
  こと。変換がデータディレクトリを別のディレクトリへ写す状態での読み書き
  先と、Windows のパスの扱いを模した環境 (``windows_like_paths``) での
  zip のメンバー名 (prefix が入らない) と復元 (known-bug-fixes の要件 1.2)
- 画面からの復元 (``pages/_restore.py``) が、共通の復元処理を通ること
  (known-bug-fixes の要件 9.3, 9.1, 9.2, 1.2): 不正なメンバー名を含む zip は
  エラーを表示して中止し、データディレクトリの中身もその外も変わらない。
  正当なバックアップ (独立に作った zip と ``create_backup_zip`` の zip) は
  復元され、セッションの論文リストも更新される。無効な zip はプレビューの
  段階でエラーになる。復元先は変換後のパスである
- バックアップのページ (``pages/_backup.py``) の PDF の件数が、変換後の
  パス (``to_fs_path(DIRPATH_PDF)``) から数えられること (known-bug-fixes の
  要件 1.2)
- 画面の流れ (保存 → バックアップのページの件数 → 全削除 → 復元のページ →
  件数と一覧) が、変換後のパスが別の空間へ写る 2 通りの状態
  (``converted_space``) で成功すること (known-bug-fixes の要件 1.2, 12.3)

画面のテストは、本物のページのコードを ``conftest.run_page`` で実行する
(ファイルアップロード欄だけを ``fake_file_uploader`` で差し替える)。

往復の検証 (要件 6.4) の期待値 (サンプルのバイト列と相対パス) は、バック
アップを作る前にディスクから別に記録し、復元結果同士の比較にはしない。
バックアップ zip の出力先は ``tmp_path`` で、``tmp_path`` は各テストの作業
ディレクトリでもある。出力先を省略するテストも、作業ディレクトリは
``tmp_path`` のままで検証する (リポジトリの作業ツリーには何も作られない)。

"""

import json
import os
import re
import shutil
import zipfile
from collections.abc import Callable, Iterator, Mapping
from datetime import datetime
from io import BytesIO
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import NamedTuple, Optional, Union

import pytest
from conftest import (
    _FORBIDDEN_PATHS,
    _TEMP_ROOT,
    PDF_BYTES,
    ConvertedSpace,
    DataPaths,
    FakeUpload,
    SampleData,
    Uploads,
    check_isolation,
    redirect_data_dir,
    run_page,
    write_pdf,
)
from streamlit.testing.v1 import AppTest

from paper_manager import __version__
from paper_manager.app.components._common import delete_pdfs, save_pdfs
from paper_manager.app.helper import _backup as backup_module
from paper_manager.app.helper._backup import (
    create_backup_zip,
    find_unsafe_member_names,
    get_backup_info,
    restore_from_zip,
)
from paper_manager.entry import Entry, PaperList
from paper_manager.entry._entry import KEY_PAPER_LIST
from paper_manager.helper import to_fs_path

VERSION_NAME = "version.json"
LIST_NAME = "list.json"
CONFIG_NAME = "config.json"
# 要件 6.7 が求める、zip として不正なファイルの復元時のメッセージ.
BAD_ZIP_MESSAGE = "無効なzipファイルです。"
# 復元先に元からあったファイルの中身 (復元は JSON として解釈しない).
OLD_LIST = b'{"previous": "list"}\n'
OLD_CONFIG = b'{"previous": "config"}\n'
# KB3 の再現に使う zip のメンバー名. データディレクトリ
# ``<temp_root>/data`` からは ``<temp_root>/escaped.pdf`` に到達する.
TRAVERSAL_ENTRY = "pdf/../../escaped.pdf"

ZipSource = Union[Path, str, BytesIO]


class Snapshot(NamedTuple):
    """バックアップ前にディスクから記録した、サンプルデータの中身."""

    list_json: bytes
    config_json: bytes
    # データディレクトリからの相対パス (``/`` 区切り) -> 中身
    pdfs: dict[str, bytes]


class MemberNames(NamedTuple):
    """サンプルデータをバックアップしたときに入るはずのメンバー名."""

    list_json: frozenset[str]
    config_json: frozenset[str]
    pdfs: frozenset[str]

    def all_members(self) -> set[str]:
        """``version.json`` を含む、すべてのメンバー名を返す."""
        return {VERSION_NAME, *self.list_json, *self.config_json, *self.pdfs}


def read_pdfs(data_root: Path) -> dict[str, bytes]:
    """``data_root`` 配下の PDF を、相対パスと中身の対応で返す.

    Parameters
    ----------
    data_root : Path
        データディレクトリ。

    Returns
    -------
    dict[str, bytes]
        ``data_root`` からの相対パス (``/`` 区切り) -> 中身。
    """
    return {
        path.relative_to(data_root).as_posix(): path.read_bytes()
        for path in data_root.rglob("*.pdf")
    }


def snapshot_tree(root: Path) -> dict[str, Optional[bytes]]:
    """``root`` 配下のすべてのファイルとディレクトリの状態を返す.

    Parameters
    ----------
    root : Path
        調べるディレクトリ。

    Returns
    -------
    dict[str, Optional[bytes]]
        ``root`` からの相対パス (``/`` 区切り) -> ファイルは中身、
        ディレクトリは ``None``。
    """
    return {
        path.relative_to(root).as_posix(): (
            path.read_bytes() if path.is_file() else None
        )
        for path in sorted(root.rglob("*"))
    }


def empty_dir(directory: Path) -> None:
    """``directory`` 自体は残して、中身をすべて消す.

    隔離されたデータディレクトリ以外は消さないよう、先に隔離を検査する。

    Parameters
    ----------
    directory : Path
        中身を消すディレクトリ。セッション専用の一時ディレクトリの配下で
        なければならない。

    Raises
    ------
    IsolationError
        ``directory`` が一時ディレクトリの配下にない、または禁止パスと
        重なる場合。このとき何も消さない。
    """
    check_isolation(directory, _TEMP_ROOT, _FORBIDDEN_PATHS)
    for child in directory.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def read_members(source: ZipSource) -> dict[str, bytes]:
    """zip のすべてのメンバーを、名前と中身の対応で返す.

    Parameters
    ----------
    source : Union[Path, str, BytesIO]
        zip ファイルのパス、またはメモリ上の zip。

    Returns
    -------
    dict[str, bytes]
        メンバー名 -> 中身。
    """
    with zipfile.ZipFile(source) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def write_zip(path: Path, members: Mapping[str, bytes]) -> Path:
    """``members`` だけを持つ zip を ``path`` に作る.

    ``create_backup_zip`` を使わずに、入力となる zip を独立に作る。

    Parameters
    ----------
    path : Path
        作る zip のパス。
    members : Mapping[str, bytes]
        メンバー名 -> 中身。名前はそのまま zip に書かれる。

    Returns
    -------
    Path
        作った zip のパス (``path`` と同じ)。
    """
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return path


@pytest.fixture
def snapshot(sample_data: SampleData, data_paths: DataPaths) -> Snapshot:
    """サンプルデータの中身を、バックアップ前にディスクから記録する."""
    return Snapshot(
        list_json=data_paths.list_file.read_bytes(),
        config_json=data_paths.config_file.read_bytes(),
        pdfs=read_pdfs(data_paths.root),
    )


@pytest.fixture
def member_names(sample_data: SampleData) -> MemberNames:
    """バックアップに入るはずのメンバー名を、サンプルの構成から導く.

    PDF の相対パスは、エントリの ``pdf_dir_name`` と孤立 PDF のフォルダ名
    から作り、ディスクの走査には頼らない。
    """
    with_pdf = sample_data.paper_list[sample_data.with_pdf_key]
    orphan_dir = sample_data.orphan_pdf.parent.name
    return MemberNames(
        list_json=frozenset({LIST_NAME}),
        config_json=frozenset({CONFIG_NAME}),
        pdfs=frozenset(
            {
                f"pdf/{with_pdf.pdf_dir_name}/00.pdf",
                f"pdf/{with_pdf.pdf_dir_name}/01.pdf",
                f"pdf/{orphan_dir}/00.pdf",
            }
        ),
    )


# --- 要件 6.1: バックアップの内容 -------------------------------------------


def test_backup_contains_version_list_config_and_all_pdfs(
    snapshot: Snapshot, member_names: MemberNames
) -> None:
    """バージョン情報・論文リスト・設定・すべての PDF が入る (要件 6.1)."""
    members = read_members(create_backup_zip())

    assert set(members) == member_names.all_members()
    assert members[LIST_NAME] == snapshot.list_json
    assert members[CONFIG_NAME] == snapshot.config_json
    assert len(member_names.pdfs) == 3 == len(snapshot.pdfs)
    for name in member_names.pdfs:
        assert members[name] == snapshot.pdfs[name]


def test_backup_version_info_has_version_and_iso_date() -> None:
    """バージョン情報にはバージョンと作成日時 (ISO 形式) が入る (要件 6.1)."""
    before = datetime.now()
    members = read_members(create_backup_zip())
    after = datetime.now()

    info = json.loads(members[VERSION_NAME].decode("utf-8"))

    assert info["version"] == __version__
    assert before <= datetime.fromisoformat(info["backup_date"]) <= after


def test_backup_member_names_are_relative_to_data_dir(
    member_names: MemberNames,
) -> None:
    """メンバー名は相対パスで、PDF は ``pdf/`` から始まる (要件 6.1)."""
    names = list(read_members(create_backup_zip()))

    for name in names:
        parts = PurePosixPath(name).parts
        assert not PurePosixPath(name).is_absolute(), name
        assert ".." not in parts, name
    pdf_names = [name for name in names if name.endswith(".pdf")]
    assert set(pdf_names) == member_names.pdfs
    assert all(name.startswith("pdf/") for name in pdf_names)


# --- 要件 6.2: 一部が存在しない状態での作成 ---------------------------------


@pytest.mark.parametrize(
    "missing",
    [
        pytest.param(("config",), id="no-config"),
        pytest.param(("list",), id="no-list"),
        pytest.param(("pdf",), id="no-pdf-dir"),
        pytest.param(("list", "config"), id="no-list-no-config"),
        pytest.param(("list", "pdf"), id="no-list-no-pdf"),
        pytest.param(("config", "pdf"), id="no-config-no-pdf"),
        pytest.param(("list", "config", "pdf"), id="nothing-but-empty-dir"),
    ],
)
def test_backup_contains_only_existing_parts(
    missing: tuple[str, ...],
    snapshot: Snapshot,
    member_names: MemberNames,
    data_paths: DataPaths,
) -> None:
    """存在しないものを除いて作成でき、存在するものだけが入る (要件 6.2)."""
    expected = {VERSION_NAME}
    parts = {
        "list": (data_paths.list_file, member_names.list_json),
        "config": (data_paths.config_file, member_names.config_json),
    }
    for part, (path, names) in parts.items():
        if part in missing:
            path.unlink()
        else:
            expected |= names
    if "pdf" in missing:
        shutil.rmtree(data_paths.pdf)
        assert not data_paths.pdf.exists()
    else:
        expected |= member_names.pdfs

    members = read_members(create_backup_zip())

    assert set(members) == expected
    if "list" not in missing:
        assert members[LIST_NAME] == snapshot.list_json
    if "config" not in missing:
        assert members[CONFIG_NAME] == snapshot.config_json


def test_backup_of_existing_but_empty_pdf_dir_has_no_pdf(
    sample_data: SampleData, data_paths: DataPaths
) -> None:
    """PDF ディレクトリが空でも作成でき、PDF は入らない (要件 6.2)."""
    empty_dir(data_paths.pdf)
    assert data_paths.pdf.is_dir()

    members = read_members(create_backup_zip())

    assert set(members) == {VERSION_NAME, LIST_NAME, CONFIG_NAME}


# --- 要件 6.3: 出力先の指定の有無 -------------------------------------------


@pytest.mark.parametrize("as_str", [False, True], ids=["Path", "str"])
def test_backup_with_output_path_creates_zip_and_returns_path(
    as_str: bool,
    tmp_path: Path,
    snapshot: Snapshot,
    member_names: MemberNames,
) -> None:
    """出力先を指定すると、そこに zip ができて ``Path`` が返る (要件 6.3)."""
    output = tmp_path / "out" / "backup.zip"
    output.parent.mkdir()

    result = create_backup_zip(str(output) if as_str else output)

    assert isinstance(result, Path)
    assert result == output
    assert output.is_file()
    assert zipfile.is_zipfile(output)
    members = read_members(output)
    assert set(members) == member_names.all_members()
    assert members[LIST_NAME] == snapshot.list_json


def test_backup_without_output_path_returns_memory_zip_and_no_file(
    tmp_path: Path, sample_data: SampleData, data_paths: DataPaths
) -> None:
    """出力先を省略すると、ファイルを作らずメモリ上の zip が返る (要件 6.3)."""
    assert Path.cwd().resolve() == tmp_path.resolve()
    tree_before = snapshot_tree(data_paths.root)

    result = create_backup_zip()

    assert isinstance(result, BytesIO)
    assert result.tell() == 0
    assert zipfile.is_zipfile(result)
    assert VERSION_NAME in read_members(result)
    # 作業ディレクトリ (tmp_path) にも、データディレクトリにも何も増えない
    assert list(tmp_path.iterdir()) == []
    assert snapshot_tree(data_paths.root) == tree_before


# --- 要件 6.4: 往復 ---------------------------------------------------------


@pytest.mark.parametrize("kind", ["path", "str", "memory"])
def test_roundtrip_restores_list_config_and_pdfs_into_empty_data_dir(
    kind: str,
    tmp_path: Path,
    snapshot: Snapshot,
    sample_data: SampleData,
    data_paths: DataPaths,
) -> None:
    """空のデータディレクトリへ復元すると、構成と中身が一致する (要件 6.4)."""
    if kind == "memory":
        source: ZipSource = create_backup_zip()
    else:
        zip_path = create_backup_zip(tmp_path / "backup.zip")
        source = str(zip_path) if kind == "str" else zip_path
    expected_files = {
        LIST_NAME: snapshot.list_json,
        CONFIG_NAME: snapshot.config_json,
        **snapshot.pdfs,
    }
    empty_dir(data_paths.root)
    assert data_paths.root.is_dir()
    assert list(data_paths.root.iterdir()) == []

    success, message = restore_from_zip(source)

    assert success, message
    restored_files = {
        path.relative_to(data_paths.root).as_posix(): path.read_bytes()
        for path in data_paths.root.rglob("*")
        if path.is_file()
    }
    # ファイルの構成 (相対パスの集合) と、バイト列が一致する。
    # 余分なファイル (``.backup`` など) も増えていない。
    assert restored_files == expected_files
    assert len(snapshot.pdfs) == 3
    assert read_pdfs(data_paths.root) == snapshot.pdfs
    assert data_paths.list_file.read_bytes() == snapshot.list_json
    assert data_paths.config_file.read_bytes() == snapshot.config_json
    # 復元した論文リストは、バックアップ元の論文リストとして読み込める
    assert PaperList.from_file() == sample_data.paper_list


# --- 要件 6.5: 既存ファイルの退避 -------------------------------------------


@pytest.mark.parametrize(
    ("has_list", "has_config"),
    [
        pytest.param(True, True, id="both"),
        pytest.param(True, False, id="list-only"),
        pytest.param(False, True, id="config-only"),
    ],
)
def test_restore_keeps_previous_json_files_as_json_backup(
    has_list: bool,
    has_config: bool,
    tmp_path: Path,
    snapshot: Snapshot,
    data_paths: DataPaths,
) -> None:
    """既存の論文リスト・設定は ``.json.backup`` へ退避される (要件 6.5)."""
    backup = create_backup_zip(tmp_path / "backup.zip")
    empty_dir(data_paths.root)
    if has_list:
        data_paths.list_file.write_bytes(OLD_LIST)
    if has_config:
        data_paths.config_file.write_bytes(OLD_CONFIG)

    success, message = restore_from_zip(backup)

    assert success, message
    # 復元後の本体はバックアップの内容になる
    assert data_paths.list_file.read_bytes() == snapshot.list_json
    assert data_paths.config_file.read_bytes() == snapshot.config_json
    # 復元前に存在した分だけ、元の内容が ``.json.backup`` として残る
    list_backup = data_paths.root / "list.json.backup"
    config_backup = data_paths.root / "config.json.backup"
    if has_list:
        assert list_backup.read_bytes() == OLD_LIST
    else:
        assert not list_backup.exists()
    if has_config:
        assert config_backup.read_bytes() == OLD_CONFIG
    else:
        assert not config_backup.exists()


# --- 要件 6.6: 成功時のメッセージ -------------------------------------------


@pytest.mark.parametrize("count", [0, 1, 3])
def test_restore_success_message_contains_restored_pdf_count(
    count: int, tmp_path: Path, data_paths: DataPaths
) -> None:
    """成功の結果と、復元した PDF の件数を含むメッセージが返る (要件 6.6)."""
    members = {LIST_NAME: b"{}"}
    members.update(
        {f"pdf/2020 - A - T{n}/00.pdf": PDF_BYTES for n in range(count)}
    )
    backup = write_zip(tmp_path / "backup.zip", members)

    success, message = restore_from_zip(backup)

    assert success is True
    assert isinstance(message, str)
    assert len(read_pdfs(data_paths.root)) == count
    # "PDF" と件数の間には数字がなく、件数の直後も数字ではない
    assert re.search(rf"PDF\D*{count}(?!\d)", message), message


def test_restore_of_real_backup_reports_three_pdfs(
    tmp_path: Path, sample_data: SampleData, data_paths: DataPaths
) -> None:
    """サンプルデータのバックアップは、PDF 3 件と報告される (要件 6.6)."""
    backup = create_backup_zip(tmp_path / "backup.zip")
    empty_dir(data_paths.root)

    success, message = restore_from_zip(backup)

    assert success is True
    assert re.search(r"PDF\D*3(?!\d)", message), message


# --- 要件 6.7: 不正な zip ---------------------------------------------------


def make_invalid_source(kind: str, tmp_path: Path) -> ZipSource:
    """zip として不正な入力を作る.

    Parameters
    ----------
    kind : str
        ``"garbage-path"``・``"garbage-str"``・``"garbage-memory"``・
        ``"empty-file"``・``"truncated"`` のいずれか。
    tmp_path : Path
        ファイルを作る場所。

    Returns
    -------
    Union[Path, str, BytesIO]
        ``restore_from_zip`` に渡す入力。
    """
    garbage = b"this is not a zip file at all"
    if kind == "garbage-memory":
        return BytesIO(garbage)
    path = tmp_path / "invalid.zip"
    if kind == "empty-file":
        path.write_bytes(b"")
    elif kind == "truncated":
        # 末尾 (中央ディレクトリ) が欠けた、本物のバックアップ
        valid = create_backup_zip().getvalue()
        path.write_bytes(valid[: len(valid) // 2])
    else:
        path.write_bytes(garbage)
    return str(path) if kind == "garbage-str" else path


@pytest.mark.parametrize(
    "kind",
    [
        "garbage-path",
        "garbage-str",
        "garbage-memory",
        "empty-file",
        "truncated",
    ],
)
def test_restore_invalid_zip_fails_and_leaves_data_unchanged(
    kind: str,
    tmp_path: Path,
    sample_data: SampleData,
    data_paths: DataPaths,
) -> None:
    """不正な zip の復元は失敗を返し、データは変更されない (要件 6.7)."""
    source = make_invalid_source(kind, tmp_path)
    tree_before = snapshot_tree(data_paths.root)
    assert tree_before  # サンプルデータがあり、変更があれば検出できる

    success, message = restore_from_zip(source)

    assert success is False
    assert message == BAD_ZIP_MESSAGE
    assert snapshot_tree(data_paths.root) == tree_before


# --- 要件 6.8: バックアップ情報の取得 ---------------------------------------


@pytest.mark.parametrize("kind", ["path", "memory"])
def test_backup_info_has_version_and_date_of_real_backup(
    kind: str, tmp_path: Path, sample_data: SampleData
) -> None:
    """バージョンと作成日時が得られる (要件 6.8)."""
    before = datetime.now()
    if kind == "memory":
        source: ZipSource = create_backup_zip()
    else:
        source = create_backup_zip(tmp_path / "backup.zip")
    after = datetime.now()

    info = get_backup_info(source)

    assert info is not None
    assert info["version"] == __version__
    assert before <= datetime.fromisoformat(info["backup_date"]) <= after


def test_backup_info_returns_stored_version_json(tmp_path: Path) -> None:
    """``version.json`` の内容がそのまま返る (要件 6.8)."""
    stored = {"version": "9.8.7", "backup_date": "2020-01-02T03:04:05"}
    backup = write_zip(
        tmp_path / "backup.zip",
        {
            VERSION_NAME: json.dumps(stored).encode("utf-8"),
            LIST_NAME: b"{}",
        },
    )

    assert get_backup_info(backup) == stored
    assert get_backup_info(str(backup)) == stored


def test_backup_info_is_none_without_version_json(tmp_path: Path) -> None:
    """バージョン情報のないバックアップからは、情報なしが返る (要件 6.8)."""
    backup = write_zip(
        tmp_path / "backup.zip", {LIST_NAME: b"{}", CONFIG_NAME: b"{}"}
    )

    assert get_backup_info(backup) is None


@pytest.mark.parametrize(
    "kind",
    ["garbage-path", "garbage-str", "garbage-memory", "empty-file"],
)
def test_backup_info_is_none_for_invalid_file(
    kind: str, tmp_path: Path
) -> None:
    """不正なファイルからは、例外にならず情報なしが返る (要件 6.8)."""
    source = make_invalid_source(kind, tmp_path)

    assert get_backup_info(source) is None


# --- 要件 9.1, 9.2: 復元のメンバー名の検証 (KB3) ---------------------------
# 要件の番号は known-bug-fixes spec のもの。


def outside_target_problems(target: Path, data_paths: DataPaths) -> list[str]:
    """書き込みの有無を調べる、データディレクトリの外のパスの安全を検査する.

    検査は、到達先がセッション専用の一時ディレクトリの中にあり、データ
    ディレクトリの外で、保護されたパス (実データなど) の配下ではなく、
    まだ存在しないこと。実データ・リポジトリ・システムの一時領域には
    書き込まない。

    Parameters
    ----------
    target : Path
        データディレクトリの外のパス。
    data_paths : DataPaths
        データの保存先。

    Returns
    -------
    list[str]
        問題の説明。安全なら空。
    """
    resolved = target.resolve()
    problems = []
    if not resolved.is_relative_to(_TEMP_ROOT.resolve()):
        problems.append(f"{target} is outside the session temporary directory")
    if resolved.is_relative_to(data_paths.root.resolve()):
        problems.append(f"{target} is inside the data directory")
    if any(resolved.is_relative_to(p.resolve()) for p in _FORBIDDEN_PATHS):
        problems.append(f"{target} is under a protected path")
    if target.exists() or target.is_symlink():
        problems.append(f"{target} already exists")
    return problems


@pytest.fixture
def escaped_target(data_paths: DataPaths) -> Iterator[Path]:
    """KB3 の再現で zip の外へ書き込まれうるファイルのパス.

    ``../../`` の到達先がセッション専用の一時ディレクトリの中に収まる
    ことを確かめてから渡す (実データ・リポジトリ・システムの一時領域
    には書き込まない)。テストの後は、成否にかかわらず到達先を削除する。

    Yields
    ------
    Path
        ``<temp_root>/escaped.pdf`` (``data_paths.root`` の親)。
    """
    target = data_paths.root.parent / "escaped.pdf"
    reached = Path(os.path.normpath(data_paths.root / TRAVERSAL_ENTRY))
    problems = outside_target_problems(target, data_paths)
    if reached != target:
        problems.append(f"{TRAVERSAL_ENTRY} reaches {reached}, not {target}")
    if problems:
        pytest.fail("unsafe KB3 repro target: " + "; ".join(problems))
    try:
        yield target
    finally:
        target.unlink(missing_ok=True)


@pytest.fixture
def sentinel(data_paths: DataPaths) -> Iterator[Path]:
    """データディレクトリの外 (親) に置いた、書き換えられてはならない PDF.

    絶対パスや ``..`` を含むメンバー名の到達先として使い、中身の変化で
    外への書き込みを検出する。テストの後は、成否にかかわらず削除する。

    Yields
    ------
    Path
        ``<temp_root>/sentinel.pdf`` (``data_paths.root`` の親)。中身は
        ``SENTINEL_BYTES``。
    """
    path = data_paths.root.parent / "sentinel.pdf"
    problems = outside_target_problems(path, data_paths)
    if problems:
        pytest.fail("unsafe sentinel: " + "; ".join(problems))
    path.write_bytes(SENTINEL_BYTES)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


@pytest.fixture
def traversal_zip(tmp_path: Path, escaped_target: Path) -> Path:
    """``pdf/../../escaped.pdf`` を含む zip を ``tmp_path`` に作る.

    zip に書いたメンバー名が正規化されずに残っていることも確かめる
    (確かめられなければ再現にならないため、テストを失敗させる)。
    """
    path = write_zip(
        tmp_path / "evil.zip", {LIST_NAME: b"{}", TRAVERSAL_ENTRY: PDF_BYTES}
    )
    with zipfile.ZipFile(path) as zf:
        if TRAVERSAL_ENTRY not in zf.namelist():
            pytest.fail(f"{TRAVERSAL_ENTRY} was not kept in {zf.namelist()}")
    return path


def test_restore_does_not_write_outside_data_dir(
    traversal_zip: Path, escaped_target: Path
) -> None:
    """'..' を含むメンバーがあっても、データディレクトリの外へ書き込まない."""
    # 結果 (拒否する / 無害な場所に書く のいずれでもよい) は問わない。
    restore_from_zip(traversal_zip)

    assert not escaped_target.exists(), (
        f"restore wrote outside the data directory: {escaped_target}"
    )


posix_only = pytest.mark.skipif(
    os.name == "nt",
    reason="a backslash is a path separator on Windows, not a file name "
    "character",
)

# ``find_unsafe_member_names`` が受け付ける、正当なメンバー名. アプリが作る
# 名前 (日本語、POSIX で作った ``\`` を含むフォルダ名、旧形式のフラットな
# ``pdf/<名前>.pdf``) と、ディレクトリのエントリ、無害な ``.`` の成分.
SAFE_NAMES = [
    pytest.param("list.json", id="list"),
    pytest.param("config.json", id="config"),
    pytest.param("version.json", id="version"),
    pytest.param("pdf/2020 - Smith - A Title/00.pdf", id="folder"),
    pytest.param(
        "pdf/2021 - 山田 太郎 - 深層学習による画像認識/00.pdf", id="ja"
    ),
    pytest.param("pdf/2020 - A\\B - T/00.pdf", id="backslash-in-folder"),
    pytest.param("pdf/2020 - A\\B\\C - T/01.pdf", id="two-backslashes"),
    pytest.param("pdf/2020 - Smith - Old Format.pdf", id="legacy-flat"),
    pytest.param("pdf/2020 - A - What is... - Wow/00.pdf", id="dots-in-name"),
    pytest.param("pdf/2020 - A - ..hidden/00.pdf", id="dotdot-prefix-name"),
    pytest.param("pdf/2020 - A: B - T/00.pdf", id="colon-in-folder"),
    pytest.param("pdf/2020 - T: sub: title/00.pdf", id="colons-in-folder"),
    pytest.param("pdf/2020 - A - T: sub.pdf", id="colon-in-legacy-flat"),
    pytest.param("pdf/", id="pdf-directory-entry"),
    pytest.param("pdf/2020 - A - T/", id="folder-directory-entry"),
    pytest.param("./", id="data-directory-entry"),
    pytest.param("./list.json", id="dot-prefix"),
    pytest.param("pdf/./a.pdf", id="dot-component"),
]

# ``find_unsafe_member_names`` が拒否する名前
UNSAFE_NAMES = [
    pytest.param("pdf/../../sentinel.pdf", id="dotdot-from-pdf"),
    pytest.param("../sentinel.pdf", id="dotdot-leading"),
    pytest.param("pdf/a/../b.pdf", id="dotdot-staying-inside"),
    pytest.param("pdf/..", id="dotdot-last"),
    pytest.param("pdf\\..\\..\\sentinel.pdf", id="backslash-dotdot"),
    pytest.param("..\\sentinel.pdf", id="backslash-dotdot-leading"),
    pytest.param("pdf/a\\..\\..\\sentinel.pdf", id="mixed-dotdot"),
    pytest.param("/sentinel.pdf", id="absolute"),
    pytest.param("//server/share/x.pdf", id="unc-slash"),
    pytest.param("\\sentinel.pdf", id="root-backslash"),
    pytest.param("\\\\server\\share\\x.pdf", id="unc-backslash"),
    pytest.param("C:/sentinel.pdf", id="drive-slash"),
    pytest.param("C:\\sentinel.pdf", id="drive-backslash"),
    pytest.param("C:sentinel.pdf", id="drive-relative"),
    # 成分の途中のドライブ指定: Windows の Python 3.9〜3.11 の pathlib は、
    # 結合のときにこれを再解析してルートを置き換える
    pytest.param("pdf/C:x.pdf", id="drive-in-component"),
    pytest.param("pdf/C:/x.pdf", id="drive-in-component-slash"),
    pytest.param("pdf/x/D:/y.pdf", id="drive-in-nested-component"),
    pytest.param("pdf/a:b.pdf", id="drive-like-component"),
    pytest.param("pdf\\C:x.pdf", id="drive-in-component-backslash"),
    pytest.param("", id="empty"),
]
# NUL を含む名前は ``ZipInfo`` が NUL で切り詰めるため、zip を経由する復元の
# テストには使えない (関数への入力としてだけ検証する)
NUL_NAME = "pdf/a\x00.pdf"
# 復元のテストでは、データディレクトリの外の PDF (``sentinel`` fixture) を指す
# 絶対パスも使う. ``<sentinel>`` は、テストで fixture のパスに置き換える
UNSAFE_MEMBERS = [
    *UNSAFE_NAMES,
    pytest.param("<sentinel>", id="absolute-to-sentinel"),
]

# 復元の対象になる、正当なバックアップのメンバー (不正な名前と一緒に入れる)
NEW_LIST = b'{"restored": "list"}\n'
NEW_CONFIG = b'{"restored": "config"}\n'
VALID_BACKUP_MEMBERS = {
    LIST_NAME: NEW_LIST,
    CONFIG_NAME: NEW_CONFIG,
    "pdf/2021 - A - T/00.pdf": PDF_BYTES,
}
SENTINEL_BYTES = b"%PDF-1.4 sentinel: must never be overwritten\n"
# メッセージに載せる不正なメンバー名の上限 (design: 一覧表示は短く保つ)
MAX_LISTED_NAMES = 10


def test_find_unsafe_member_names_accepts_legitimate_names() -> None:
    """正当なメンバー名はすべて受け付ける (空の結果, 要件 9.1)."""
    names = [param.values[0] for param in SAFE_NAMES]

    assert find_unsafe_member_names(names) == []


@pytest.mark.parametrize("name", SAFE_NAMES)
def test_find_unsafe_member_names_accepts_each_legitimate_name(
    name: str,
) -> None:
    """正当なメンバー名は 1 つずつでも受け付ける (要件 9.1)."""
    assert find_unsafe_member_names([name]) == []


@pytest.mark.parametrize("name", UNSAFE_NAMES)
def test_find_unsafe_member_names_flags_each_unsafe_name(name: str) -> None:
    """絶対パス・ドライブ・``..``・空・NUL を含む名前は、不正と判定する."""
    assert find_unsafe_member_names([name]) == [name]


def test_find_unsafe_member_names_flags_a_name_with_nul() -> None:
    """NUL 文字を含む名前は、不正と判定する (要件 9.1)."""
    assert find_unsafe_member_names([NUL_NAME]) == [NUL_NAME]


def test_find_unsafe_member_names_returns_only_unsafe_names_in_order() -> None:
    """正当な名前は除き、不正な名前だけを、与えた順序で返す (要件 9.1)."""
    names = ["list.json", "../a", "pdf/x/00.pdf", "/b", "pdf/x/", "C:c"]

    assert find_unsafe_member_names(names) == ["../a", "/b", "C:c"]


def test_find_unsafe_member_names_accepts_any_iterable() -> None:
    """リスト以外の反復可能なオブジェクトも受け付ける (要件 9.1)."""
    names = (name for name in ["list.json", "../a", "pdf/b.pdf"])

    assert find_unsafe_member_names(names) == ["../a"]


@pytest.mark.parametrize("member", UNSAFE_MEMBERS)
def test_restore_aborts_without_writing_on_unsafe_member(
    member: str,
    tmp_path: Path,
    sample_data: SampleData,
    data_paths: DataPaths,
    sentinel: Path,
) -> None:
    """不正なメンバーが 1 つでもあれば、何も書かずに失敗を返す (要件 9.1, 9.2).

    不正なメンバーは最後に置く (先頭から順に検証して書く実装は、その前の
    ``list.json`` などを書いてしまうため)。データディレクトリ
    (``list.json`` と退避ファイルを含む) も、その外 (親ディレクトリ) も
    変わらない。
    """
    name = member.replace("<sentinel>", str(sentinel))
    backup = write_zip(
        tmp_path / "unsafe.zip", {**VALID_BACKUP_MEMBERS, name: PDF_BYTES}
    )
    with zipfile.ZipFile(backup) as zf:
        # zip に書いたメンバー名が変わっていなければ、再現になる
        assert name in zf.namelist()
    outside = data_paths.root.parent
    tree_before = snapshot_tree(outside)
    assert data_paths.list_file.is_file()  # 書き換えられれば検出できる

    success, message = restore_from_zip(backup)

    assert success is False
    assert message != BAD_ZIP_MESSAGE
    if name:
        assert name in message
    assert "pdf/2021 - A - T/00.pdf" not in message
    assert snapshot_tree(outside) == tree_before
    assert sentinel.read_bytes() == SENTINEL_BYTES
    assert not data_paths.list_file.with_suffix(".json.backup").exists()


def test_restore_reports_every_unsafe_member(
    tmp_path: Path, sample_data: SampleData, data_paths: DataPaths
) -> None:
    """不正なメンバーが複数あれば、すべてをメッセージに載せる (要件 9.2)."""
    unsafe = ["../a.pdf", "/b.pdf", "C:/c.pdf"]
    backup = write_zip(
        tmp_path / "unsafe.zip",
        {**VALID_BACKUP_MEMBERS, **dict.fromkeys(unsafe, PDF_BYTES)},
    )
    tree_before = snapshot_tree(data_paths.root)

    success, message = restore_from_zip(backup)

    assert success is False
    for name in unsafe:
        assert name in message
    assert snapshot_tree(data_paths.root) == tree_before


def test_restore_keeps_the_listed_unsafe_names_short(
    tmp_path: Path, sample_data: SampleData, data_paths: DataPaths
) -> None:
    """不正なメンバーが多くても、載せる名前は上限までに抑える (要件 9.2)."""
    unsafe = [f"../unsafe{n:03d}.pdf" for n in range(MAX_LISTED_NAMES + 5)]
    backup = write_zip(
        tmp_path / "unsafe.zip", dict.fromkeys(unsafe, PDF_BYTES)
    )
    tree_before = snapshot_tree(data_paths.root)

    success, message = restore_from_zip(backup)

    assert success is False
    assert message.count("../unsafe") == MAX_LISTED_NAMES
    assert "../unsafe000.pdf" in message
    assert unsafe[-1] not in message
    assert "ほか 5 件" in message
    assert snapshot_tree(data_paths.root) == tree_before


@pytest.mark.parametrize(
    "member",
    [
        pytest.param(
            "pdf/2021 - 山田 太郎 - 深層学習による画像認識/00.pdf", id="ja"
        ),
        pytest.param("pdf/2020 - Smith - Old Format Title.pdf", id="legacy"),
        pytest.param(
            "pdf/2020 - Smith - What is... - Wow/01.pdf", id="dots-in-name"
        ),
        pytest.param(
            "pdf/2020 - A\\B - T/00.pdf",
            id="backslash-in-folder",
            marks=posix_only,
        ),
    ],
)
def test_restore_accepts_legitimate_backup_members(
    member: str, tmp_path: Path, data_paths: DataPaths
) -> None:
    """正当なバックアップは受け付けて、メンバー名どおりに復元する (要件 9.1)."""
    backup = write_zip(
        tmp_path / "ok.zip", {LIST_NAME: NEW_LIST, member: PDF_BYTES}
    )

    success, message = restore_from_zip(backup)

    assert success, message
    assert re.search(r"PDF\D*1(?!\d)", message), message
    assert data_paths.list_file.read_bytes() == NEW_LIST
    # ``\`` を含むフォルダ名も、分割されずに 1 つのフォルダ (旧形式は 1 つの
    # ファイル) として復元される
    assert os.listdir(data_paths.pdf) == [member.split("/")[1]]
    assert (
        data_paths.root.joinpath(*member.split("/")).read_bytes() == PDF_BYTES
    )


def test_restore_accepts_directory_entries(
    tmp_path: Path, data_paths: DataPaths
) -> None:
    """ディレクトリのエントリ (``./`` や ``pdf/``) があっても復元できる (要件 9.1)."""
    backup = tmp_path / "dirs.zip"
    with zipfile.ZipFile(backup, "w") as zf:
        for directory in ("./", "pdf/", "pdf/2020 - A - T/"):
            zf.writestr(directory, b"")
        zf.writestr(LIST_NAME, NEW_LIST)
        zf.writestr("pdf/2020 - A - T/00.pdf", PDF_BYTES)

    success, message = restore_from_zip(backup)

    assert success, message
    assert data_paths.list_file.read_bytes() == NEW_LIST
    assert read_pdfs(data_paths.root) == {"pdf/2020 - A - T/00.pdf": PDF_BYTES}


def test_restore_does_not_write_a_directory_entry_as_a_pdf(
    tmp_path: Path, data_paths: DataPaths
) -> None:
    """``.pdf`` で終わるディレクトリのエントリは、PDF として書かない (要件 9.1)."""
    backup = tmp_path / "dirs.zip"
    with zipfile.ZipFile(backup, "w") as zf:
        zf.writestr("pdf/2020 - A - T.pdf/", b"")
        zf.writestr(LIST_NAME, NEW_LIST)

    success, message = restore_from_zip(backup)

    assert success, message
    assert re.search(r"PDF\D*0(?!\d)", message), message
    assert not data_paths.pdf.exists()


@pytest.mark.parametrize(
    "folder",
    [
        pytest.param("2021 - 山田 太郎 - 日本語のタイトル", id="ja"),
        pytest.param(
            "2020 - A\\B - T", id="backslash-in-folder", marks=posix_only
        ),
    ],
)
def test_roundtrip_of_japanese_and_backslash_folder_names(
    folder: str, tmp_path: Path, data_paths: DataPaths
) -> None:
    """日本語名と ``\\`` を含むフォルダ名の PDF が、往復で元に戻る (要件 9.1)."""
    source = data_paths.pdf / folder / "00.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(PDF_BYTES)
    backup = create_backup_zip(tmp_path / "backup.zip")
    assert f"pdf/{folder}/00.pdf" in read_members(backup)
    empty_dir(data_paths.root)

    success, message = restore_from_zip(backup)

    assert success, message
    assert read_pdfs(data_paths.root) == {f"pdf/{folder}/00.pdf": PDF_BYTES}


def test_restore_keeps_a_utf8_bom_list_json_byte_for_byte(
    tmp_path: Path, data_paths: DataPaths
) -> None:
    """BOM 付きの ``list.json`` は、バイト列のまま復元される (要件 9.1)."""
    bom_list = b"\xef\xbb\xbf" + NEW_LIST
    backup = write_zip(tmp_path / "bom.zip", {LIST_NAME: bom_list})

    success, message = restore_from_zip(backup)

    assert success, message
    assert data_paths.list_file.read_bytes() == bom_list


# --- 要件 9.1: 結合結果の検証 (名前の判定とは独立の、二重の防御) ------------
# ``restore_from_zip`` は、名前の判定 (``find_unsafe_member_names``) に加えて、
# 書き込みに使う結合 (``_join_member``) の結果が ``data_root`` の下に収まる
# ことを、書き込みの前に確かめる.
# Windows の Python 3.9〜3.11 の pathlib は、成分の途中のドライブ指定を結合の
# ときに再解析して、ルートを置き換える (``\\?\C:\data`` + ``pdf/C:x.pdf`` が
# ``C:x.pdf`` になる). Python 3.12 以降 (このテストを動かす環境) では再現しない
# ので、その結合を模した関数に差し替えて確かめる.

DRIVE_IN_COMPONENT_NAMES = [
    "pdf/C:x.pdf",
    "pdf/C:/x.pdf",
    "pdf/x/D:/y.pdf",
    "pdf/a:b.pdf",
]


def windows_pre312_join(root: PurePath, name: str) -> PurePath:
    """Python 3.9〜3.11 の Windows の pathlib の結合を模す.

    成分の途中にドライブ指定 (``X:``) があれば、そこでルートを置き換える
    (``C:x.pdf`` はドライブ相対のパスになる)。なければ ``root`` の下に結合する。

    Parameters
    ----------
    root : PurePath
        結合の起点。
    name : str
        zip のメンバー名。

    Returns
    -------
    PurePath
        結合の結果。
    """
    parts = [part for part in re.split(r"[/\\]", name) if part]
    for index, part in enumerate(parts):
        if part[1:2] == ":":
            return PureWindowsPath(part, *parts[index + 1 :])
    return root.joinpath(*parts)


def test_find_escaping_members_flags_names_that_join_outside_the_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """結合の結果がルートの外になる名前だけを返す (要件 9.1)."""
    monkeypatch.setattr(backup_module, "_join_member", windows_pre312_join)
    root = PureWindowsPath("\\\\?\\C:\\Users\\u\\.paper-manager")
    legitimate = [
        "list.json",
        "pdf/2020 - A: B - T/00.pdf",
        "pdf/2020 - Smith - Old Format.pdf",
        "./",
    ]

    flagged = backup_module._find_escaping_members(
        root, [*DRIVE_IN_COMPONENT_NAMES, *legitimate]
    )

    assert flagged == DRIVE_IN_COMPONENT_NAMES


@pytest.mark.skipif(os.name == "nt", reason="POSIX の結合を確かめる")
def test_find_escaping_members_flags_absolute_and_dotdot_joins() -> None:
    """絶対パスと ``..`` の成分も、結合の結果がルートの外になる (要件 9.1)."""
    escaping = ["/abs.pdf", "../x.pdf", "pdf/../../x.pdf", "pdf/a/../../x.pdf"]
    legitimate = ["list.json", "pdf/x/00.pdf", "pdf/", "./", ""]

    flagged = backup_module._find_escaping_members(
        Path("/data"), [*escaping, *legitimate]
    )

    assert flagged == escaping


def test_restore_aborts_when_a_joined_path_leaves_the_data_dir(
    monkeypatch: pytest.MonkeyPatch,
    traversal_zip: Path,
    escaped_target: Path,
    sample_data: SampleData,
    data_paths: DataPaths,
) -> None:
    """名前の判定を通っても、結合の結果が外なら何も書かずに中止する (要件 9.1)."""
    monkeypatch.setattr(
        backup_module, "find_unsafe_member_names", lambda n: []
    )
    tree_before = snapshot_tree(data_paths.root.parent)

    success, message = restore_from_zip(traversal_zip)

    assert success is False
    assert TRAVERSAL_ENTRY in message
    assert not escaped_target.exists()
    assert snapshot_tree(data_paths.root.parent) == tree_before


def test_restore_aborts_on_a_drive_in_a_component_when_the_join_reparses_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_data: SampleData,
    data_paths: DataPaths,
) -> None:
    """成分の途中のドライブ指定を結合が再解析しても、書き込まない (要件 9.1)."""
    monkeypatch.setattr(
        backup_module, "find_unsafe_member_names", lambda n: []
    )
    monkeypatch.setattr(backup_module, "_join_member", windows_pre312_join)
    members = {**VALID_BACKUP_MEMBERS, "pdf/C:x.pdf": PDF_BYTES}
    backup = write_zip(tmp_path / "drive.zip", members)
    tree_before = snapshot_tree(data_paths.root.parent)

    success, message = restore_from_zip(backup)

    assert success is False
    assert "pdf/C:x.pdf" in message
    assert "pdf/2021 - A - T/00.pdf" not in message
    assert snapshot_tree(data_paths.root.parent) == tree_before


# --- 要件 1.2: 変換後のパスを起点にした作成と復元 ---------------------------
# ``windows_like_paths`` は、変換後のパス (``//x``) を変換前のパス (``/x``) と
# 別のルートとして扱わせる. 変換後のパスどうしで ``relative_to`` を使わないと
# ``ValueError`` になり、メンバー名に prefix が入る.
# 変換後のパスを起点にしているか (変換前のパスで読み書きしていないか) は、
# 変換がデータディレクトリを別のディレクトリへ写す状態
# (``conftest.redirect_data_dir``) で確かめる.


def test_backup_reads_from_the_converted_data_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    snapshot: Snapshot,
    member_names: MemberNames,
    data_paths: DataPaths,
) -> None:
    """バックアップは、変換後のパスの下の内容から作る (要件 1.2)."""
    redirected = tmp_path / "converted"
    shutil.copytree(data_paths.root, redirected)
    empty_dir(data_paths.root)
    redirect_data_dir(monkeypatch, data_paths.root, redirected)

    members = read_members(create_backup_zip())

    assert set(members) == member_names.all_members()
    assert members[LIST_NAME] == snapshot.list_json
    assert members[CONFIG_NAME] == snapshot.config_json
    for name in member_names.pdfs:
        assert members[name] == snapshot.pdfs[name]


def test_restore_writes_into_the_converted_data_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    snapshot: Snapshot,
    sample_data: SampleData,
    data_paths: DataPaths,
) -> None:
    """復元は、変換後のパスの下に書き、既存の JSON を退避する (要件 1.2)."""
    backup = create_backup_zip(tmp_path / "backup.zip")
    empty_dir(data_paths.root)
    redirected = tmp_path / "converted"
    redirected.mkdir()
    (redirected / LIST_NAME).write_bytes(OLD_LIST)
    (redirected / CONFIG_NAME).write_bytes(OLD_CONFIG)
    redirect_data_dir(monkeypatch, data_paths.root, redirected)

    success, message = restore_from_zip(backup)

    assert success, message
    assert (redirected / LIST_NAME).read_bytes() == snapshot.list_json
    assert (redirected / CONFIG_NAME).read_bytes() == snapshot.config_json
    assert (redirected / "list.json.backup").read_bytes() == OLD_LIST
    assert (redirected / "config.json.backup").read_bytes() == OLD_CONFIG
    assert read_pdfs(redirected) == snapshot.pdfs
    # 変換前のパスの下には何も書かれない
    assert list(data_paths.root.iterdir()) == []


def test_backup_under_windows_like_paths_has_no_prefix_in_member_names(
    windows_like_paths: None,
    snapshot: Snapshot,
    member_names: MemberNames,
    data_paths: DataPaths,
) -> None:
    """変換後のパスから作っても、メンバー名に prefix が入らない (要件 1.2)."""
    assert to_fs_path(data_paths.pdf) != data_paths.pdf  # 別名が有効

    members = read_members(create_backup_zip())

    assert set(members) == member_names.all_members()
    for name in members:
        assert not name.startswith("/"), name
        assert "//" not in name, name
    for name in member_names.pdfs:
        assert members[name] == snapshot.pdfs[name]


def test_restore_under_windows_like_paths_writes_the_same_layout(
    tmp_path: Path,
    windows_like_paths: None,
    snapshot: Snapshot,
    sample_data: SampleData,
    data_paths: DataPaths,
) -> None:
    """変換後のパスに復元でき、既存の JSON は退避される (要件 1.2)."""
    backup = create_backup_zip(tmp_path / "backup.zip")
    empty_dir(data_paths.root)
    data_paths.list_file.write_bytes(OLD_LIST)
    data_paths.config_file.write_bytes(OLD_CONFIG)

    success, message = restore_from_zip(backup)

    assert success, message
    assert data_paths.list_file.read_bytes() == snapshot.list_json
    assert data_paths.config_file.read_bytes() == snapshot.config_json
    assert read_pdfs(data_paths.root) == snapshot.pdfs
    assert (data_paths.root / "list.json.backup").read_bytes() == OLD_LIST
    assert (data_paths.root / "config.json.backup").read_bytes() == OLD_CONFIG


def test_restore_aborts_on_unsafe_member_under_windows_like_paths(
    traversal_zip: Path,
    escaped_target: Path,
    sample_data: SampleData,
    data_paths: DataPaths,
    windows_like_paths: None,
) -> None:
    """変換後のパスでも、不正なメンバーは何も書かずに中止する (要件 9.2)."""
    tree_before = snapshot_tree(data_paths.root.parent)

    success, _ = restore_from_zip(traversal_zip)

    assert success is False
    assert not escaped_target.exists()
    assert snapshot_tree(data_paths.root.parent) == tree_before


# --- 要件 9.3, 9.1, 9.2, 1.2: 画面からの復元 (RestorePage) -----------------
# 本物の復元ページのコードを ``run_page`` で実行する. ファイルアップロード欄
# だけを ``fake_file_uploader`` が、選んだバックアップを返す処理に差し替える
# (Streamlit 1.50 の AppTest は、アップロード欄を直接操作できない).
# 復元が成功するとページは ``st.rerun()`` するが、AppTest は再実行で画面を
# 作り直すため、成功のメッセージが残らない. そのため ``st.rerun`` は、呼ばれた
# 回数を記録するだけの処理に差し替える (中止のときに再描画すると、エラーが
# 画面から消えるので、中止のテストでは呼ばれないことを確かめる).

RESTORE_PAGE = "pages/_restore.py"
BACKUP_PAGE = "pages/_backup.py"
# ``st.file_uploader`` のラベルの接頭辞 (``fake_file_uploader`` の照合に使う)
RESTORE_UPLOADER_LABEL = "バックアップファイル"
RESTORE_BUTTON_LABEL = "復元を実行"
PDF_COUNT_LABEL = "PDFファイル数"

# 独立に作る正当なバックアップの中身. 論文リストは ``PaperList`` を通さずに
# JSON として直接書く
BACKUP_VERSION_INFO = {
    "version": "0.0.0-test",
    "backup_date": "2020-01-02T03:04:05",
}
RESTORED_KEY = "Doe2019_1"
RESTORED_LIST = json.dumps(
    {
        RESTORED_KEY: {
            "ENTRYTYPE": "article",
            "ID": RESTORED_KEY,
            "author": "Jane Doe",
            "title": "画面から復元された論文",
            "journal": "Journal of Restoring",
            "year": "2019",
        }
    },
    ensure_ascii=False,
    indent=4,
).encode("utf-8")
RESTORED_CONFIG = b'{"proxy": {"http": "http://restored.example.com:8080"}}\n'
RESTORED_PDF_NAME = "pdf/2019 - Jane Doe - 画面から復元された論文/00.pdf"
RESTORED_PDF = PDF_BYTES + b"%restored from the screen\n"

# 画面から復元しようとして中止される、不正なメンバー名. ``<sentinel>`` は、
# データディレクトリの外の PDF の絶対パスに置き換える
SCREEN_UNSAFE_MEMBERS = [
    pytest.param("../outside.pdf", id="dotdot-leading"),
    pytest.param("pdf/../../outside.pdf", id="dotdot-from-pdf"),
    pytest.param("pdf\\..\\..\\outside.pdf", id="backslash-dotdot"),
    pytest.param("<sentinel>", id="absolute-to-sentinel"),
    pytest.param("C:/outside.pdf", id="drive-slash"),
    pytest.param("pdf/C:x.pdf", id="drive-in-component"),
]

UploaderInstaller = Callable[[Mapping[str, Uploads]], None]


@pytest.fixture
def outside_cleanup(data_paths: DataPaths) -> Iterator[None]:
    """データディレクトリの外 (親) に増えたものを、テストの後に消す.

    正しく中止すれば何も増えない. 万一外へ書き込む実装でも、後のテストには
    残さない (消す対象は、セッション専用の一時ディレクトリの直下だけ).
    """
    outside = data_paths.root.parent
    check_isolation(outside, _TEMP_ROOT, _FORBIDDEN_PATHS)
    before = set(outside.iterdir())
    yield
    for path in set(outside.iterdir()) - before:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()


def open_restore_page(
    fake_file_uploader: UploaderInstaller, backup: bytes
) -> AppTest:
    """``backup`` を選んだ状態の復元ページを開く (ボタンはまだ押さない).

    Parameters
    ----------
    fake_file_uploader : UploaderInstaller
        ``fake_file_uploader`` fixture.
    backup : bytes
        アップロードされたファイルの中身.

    Returns
    -------
    AppTest
        1 回実行済みの AppTest. ページは例外なく実行されている.
    """
    upload = FakeUpload("paper-manager-backup_test.zip", backup)
    fake_file_uploader({RESTORE_UPLOADER_LABEL: upload})
    at = run_page(RESTORE_PAGE)
    assert not at.exception, [e.value for e in at.exception]
    return at


def click_restore(at: AppTest) -> AppTest:
    """「復元を実行」を押して、ページを再実行する.

    Returns
    -------
    AppTest
        再実行後の AppTest. ページは例外なく実行されている.
    """
    [button] = [b for b in at.button if b.label == RESTORE_BUTTON_LABEL]
    at = button.click().run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def restore_on_page(
    fake_file_uploader: UploaderInstaller, backup: bytes
) -> AppTest:
    """``backup`` を選んで「復元を実行」を押した後の復元ページを返す."""
    return click_restore(open_restore_page(fake_file_uploader, backup))


@pytest.mark.parametrize("member", SCREEN_UNSAFE_MEMBERS)
def test_restore_page_aborts_on_unsafe_member_and_changes_nothing(
    member: str,
    tmp_path: Path,
    fake_file_uploader: UploaderInstaller,
    rerun_calls: list[None],
    outside_cleanup: None,
    sample_data: SampleData,
    data_paths: DataPaths,
    sentinel: Path,
) -> None:
    """不正なメンバーを含む zip は、画面からでも何も書かずに中止する.

    エラーが不正な名前を挙げて表示され、成功は表示されない. 論文リスト・
    設定・PDF を含むデータディレクトリ (退避ファイルも増えない) も、その外
    (親ディレクトリ) も変わらない (要件 9.1, 9.2, 9.3).

    不正なメンバーは最後に置く (先頭から順に書く実装は、その前の
    ``list.json`` などを書いてしまうため).
    """
    name = member.replace("<sentinel>", str(sentinel))
    reached = Path(os.path.normpath(data_paths.root / name))
    assert reached.is_relative_to(_TEMP_ROOT), (
        f"unsafe repro: {name} reaches {reached}"
    )
    backup = write_zip(
        tmp_path / "unsafe.zip", {**VALID_BACKUP_MEMBERS, name: PDF_BYTES}
    ).read_bytes()
    outside = data_paths.root.parent
    tree_before = snapshot_tree(outside)
    assert data_paths.list_file.is_file()  # 書き換えられれば検出できる

    at = restore_on_page(fake_file_uploader, backup)

    assert snapshot_tree(outside) == tree_before
    assert sentinel.read_bytes() == SENTINEL_BYTES
    assert not data_paths.list_file.with_suffix(".json.backup").exists()
    assert not data_paths.config_file.with_suffix(".json.backup").exists()
    [error] = at.error
    assert name in error.value
    assert error.value != BAD_ZIP_MESSAGE
    assert not at.success
    assert rerun_calls == []


@pytest.mark.usefixtures("rerun_calls")
def test_restore_page_restores_an_independently_built_backup(
    tmp_path: Path,
    fake_file_uploader: UploaderInstaller,
    sample_data: SampleData,
    snapshot: Snapshot,
    data_paths: DataPaths,
) -> None:
    """独立に作った正当なバックアップが復元され、論文リストが更新される.

    論文リスト・設定・PDF が書かれ、既存の論文リストと設定は
    ``.json.backup`` へ退避され、既存の PDF は残る. 成功が表示され、
    セッションの論文リストは復元したものに置き換わる (要件 9.3).
    """
    backup = write_zip(
        tmp_path / "independent.zip",
        {
            VERSION_NAME: json.dumps(BACKUP_VERSION_INFO).encode("utf-8"),
            LIST_NAME: RESTORED_LIST,
            CONFIG_NAME: RESTORED_CONFIG,
            RESTORED_PDF_NAME: RESTORED_PDF,
        },
    ).read_bytes()
    at = open_restore_page(fake_file_uploader, backup)
    # 復元の前は、サンプルの論文リストがセッションにある
    assert set(at.session_state[KEY_PAPER_LIST]) == set(
        sample_data.paper_list.keys()
    )

    at = click_restore(at)

    assert not at.error
    [success] = at.success
    assert re.search(r"PDF\D*1(?!\d)", success.value), success.value
    assert data_paths.list_file.read_bytes() == RESTORED_LIST
    assert data_paths.config_file.read_bytes() == RESTORED_CONFIG
    assert read_pdfs(data_paths.root) == {
        **snapshot.pdfs,
        RESTORED_PDF_NAME: RESTORED_PDF,
    }
    backups = data_paths.root
    assert (backups / "list.json.backup").read_bytes() == snapshot.list_json
    assert (backups / "config.json.backup").read_bytes() == (
        snapshot.config_json
    )
    assert set(at.session_state[KEY_PAPER_LIST]) == {RESTORED_KEY}


@pytest.mark.usefixtures("rerun_calls")
def test_restore_page_restores_a_backup_made_by_create_backup_zip(
    fake_file_uploader: UploaderInstaller,
    sample_data: SampleData,
    snapshot: Snapshot,
    data_paths: DataPaths,
) -> None:
    """``create_backup_zip`` で作ったバックアップが、画面から元に戻る.

    バックアップの後でデータを別の内容に変えておき、復元すると、論文リスト・
    設定・PDF がバックアップ時の中身に戻り、変えた内容は ``.json.backup``
    に残る. セッションの論文リストも、バックアップ時のものになる
    (要件 9.3).
    """
    backup = create_backup_zip().getvalue()
    shutil.rmtree(data_paths.pdf)
    data_paths.list_file.write_bytes(RESTORED_LIST)
    data_paths.config_file.write_bytes(OLD_CONFIG)
    at = open_restore_page(fake_file_uploader, backup)
    assert set(at.session_state[KEY_PAPER_LIST]) == {RESTORED_KEY}

    at = click_restore(at)

    assert not at.error
    [success] = at.success
    assert re.search(r"PDF\D*3(?!\d)", success.value), success.value
    assert data_paths.list_file.read_bytes() == snapshot.list_json
    assert data_paths.config_file.read_bytes() == snapshot.config_json
    assert len(snapshot.pdfs) == 3
    assert read_pdfs(data_paths.root) == snapshot.pdfs
    assert (data_paths.root / "list.json.backup").read_bytes() == (
        RESTORED_LIST
    )
    assert (data_paths.root / "config.json.backup").read_bytes() == (
        OLD_CONFIG
    )
    assert set(at.session_state[KEY_PAPER_LIST]) == set(
        sample_data.paper_list.keys()
    )


def test_restore_page_previews_the_members_and_the_version_info(
    tmp_path: Path,
    fake_file_uploader: UploaderInstaller,
    sample_data: SampleData,
    data_paths: DataPaths,
) -> None:
    """選んだだけでは復元せず、zip の内容と ``version.json`` を表示する."""
    members = {
        VERSION_NAME: json.dumps(BACKUP_VERSION_INFO).encode("utf-8"),
        LIST_NAME: RESTORED_LIST,
        RESTORED_PDF_NAME: RESTORED_PDF,
    }
    backup = write_zip(tmp_path / "preview.zip", members).read_bytes()
    tree_before = snapshot_tree(data_paths.root)

    at = open_restore_page(fake_file_uploader, backup)

    [listing] = at.code
    assert set(listing.value.splitlines()) == set(members)
    [version] = at.json
    assert json.loads(version.value) == BACKUP_VERSION_INFO
    assert [b.label for b in at.button] == [RESTORE_BUTTON_LABEL]
    assert not at.error
    assert snapshot_tree(data_paths.root) == tree_before


@pytest.mark.parametrize("kind", ["garbage", "empty", "truncated"])
def test_restore_page_reports_an_invalid_zip_at_the_preview(
    kind: str,
    fake_file_uploader: UploaderInstaller,
    rerun_calls: list[None],
    sample_data: SampleData,
    data_paths: DataPaths,
) -> None:
    """zip として不正なファイルは、プレビューの段階でエラーになる.

    復元ボタンは出ず、データは変わらない (要件 6.7)."""
    valid = create_backup_zip().getvalue()
    content = {
        "garbage": b"this is not a zip file at all",
        "empty": b"",
        "truncated": valid[: len(valid) // 2],
    }[kind]
    tree_before = snapshot_tree(data_paths.root)

    at = open_restore_page(fake_file_uploader, content)

    assert [e.value for e in at.error] == [BAD_ZIP_MESSAGE]
    assert not at.button
    assert not at.success
    assert rerun_calls == []
    assert snapshot_tree(data_paths.root) == tree_before


@pytest.mark.usefixtures("rerun_calls")
def test_restore_page_writes_into_the_converted_data_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_file_uploader: UploaderInstaller,
    sample_data: SampleData,
    snapshot: Snapshot,
    data_paths: DataPaths,
) -> None:
    """画面からの復元は、変換後のパスの下に書き、既存の JSON を退避する.

    変換前のパスの下には何も書かれない (要件 1.2, 9.3).
    """
    backup = create_backup_zip().getvalue()
    empty_dir(data_paths.root)
    redirected = tmp_path / "converted"
    redirected.mkdir()
    (redirected / LIST_NAME).write_bytes(OLD_LIST)
    (redirected / CONFIG_NAME).write_bytes(OLD_CONFIG)
    redirect_data_dir(monkeypatch, data_paths.root, redirected)

    at = restore_on_page(fake_file_uploader, backup)

    assert not at.error
    assert len(at.success) == 1
    assert (redirected / LIST_NAME).read_bytes() == snapshot.list_json
    assert (redirected / CONFIG_NAME).read_bytes() == snapshot.config_json
    assert (redirected / "list.json.backup").read_bytes() == OLD_LIST
    assert (redirected / "config.json.backup").read_bytes() == OLD_CONFIG
    assert read_pdfs(redirected) == snapshot.pdfs
    # 変換前のパスの下には、ファイルが 1 つも書かれない (``PaperList`` は
    # 作るたびに空の PDF ルートを作るので、ディレクトリは数えない)
    assert [p for p in data_paths.root.rglob("*") if p.is_file()] == []


# --- 要件 1.2: バックアップのページの PDF の件数 (BackupPage) ---------------


def shown_pdf_count(at: AppTest) -> str:
    """バックアップのページに表示された PDF の件数を返す."""
    assert not at.exception, [e.value for e in at.exception]
    [metric] = [m for m in at.metric if m.label == PDF_COUNT_LABEL]
    return metric.value


def test_backup_page_shows_the_number_of_pdfs(
    sample_data: SampleData, snapshot: Snapshot
) -> None:
    """バックアップのページは、データディレクトリの PDF の件数を表示する."""
    assert len(snapshot.pdfs) == 3

    at = run_page(BACKUP_PAGE)

    assert shown_pdf_count(at) == "3"


def test_backup_page_shows_zero_without_a_pdf_dir(
    data_paths: DataPaths,
) -> None:
    """PDF のディレクトリがなければ、PDF の件数は 0 と表示する."""
    assert not data_paths.pdf.exists()

    at = run_page(BACKUP_PAGE)

    assert shown_pdf_count(at) == "0"


def test_backup_page_counts_pdfs_under_windows_like_paths(
    windows_like_paths: None, sample_data: SampleData, snapshot: Snapshot
) -> None:
    """Windows のパスの扱いを模しても、PDF の件数は同じ (要件 1.2)."""
    at = run_page(BACKUP_PAGE)

    assert shown_pdf_count(at) == str(len(snapshot.pdfs))


def test_backup_page_counts_pdfs_in_the_converted_pdf_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, data_paths: DataPaths
) -> None:
    """PDF の件数は、変換後のパスの下の PDF から数える (要件 1.2).

    変換前の PDF ルートは PDF のない空のディレクトリで、変換後のパスの下
    にだけ PDF がある. PDF 以外のファイルは数えない.
    """
    redirected = tmp_path / "converted"
    names = [
        "pdf/2020 - A - One/00.pdf",
        "pdf/2020 - A - One/01.pdf",
        "pdf/2021 - B - Two/00.pdf",
    ]
    for name in names:
        write_pdf(redirected / name)
    (redirected / "pdf/2020 - A - One/notes.txt").write_text("not a pdf")
    data_paths.pdf.mkdir(exist_ok=True)
    assert list(data_paths.pdf.rglob("*")) == []
    redirect_data_dir(monkeypatch, data_paths.root, redirected)

    at = run_page(BACKUP_PAGE)

    assert shown_pdf_count(at) == str(len(names))


# --- 要件 1.2, 12.3: 画面の流れ (保存 → バックアップ → 全削除 → 復元) ----------
# バックアップのページの PDF の件数と、復元のページの書き込み先を、変換後の
# パスが別の空間へ写る 2 通りの状態 (``converted_space``) で通す. 保存と削除は
# アプリの関数 (``save_pdfs`` / ``delete_pdfs``) で、変換後の空間に対して行う.


@pytest.mark.filterwarnings(
    "ignore:(pdf_filename|_migrate_legacy_pdf_filenames) is deprecated"
    ":DeprecationWarning"
)
@pytest.mark.usefixtures("rerun_calls")
def test_pages_save_backup_delete_and_restore_in_the_converted_space(
    converted_space: ConvertedSpace,
    data_paths: DataPaths,
    make_entry: Callable[..., Entry],
    fake_file_uploader: UploaderInstaller,
) -> None:
    """保存した PDF が、バックアップのページで数えられ、復元のページで戻る.

    PDF を保存してバックアップを作り、PDF を全削除してから、復元のページで
    バックアップを復元する. バックアップのページの PDF の件数は、保存後は 3、
    全削除の後は 0、復元の後は 3 になる. 復元のページは、変換後のパスの下に、
    元と同じ配置と中身で書く. 復元した PDF は、論文の PDF の一覧に変換後の
    パスで出る.
    """
    first = make_entry()
    second = make_entry(
        author="Bob Jones", title="Quantum Widgets", year="2019"
    )
    expected: dict[str, bytes] = {}
    for entry, count in ((first, 2), (second, 1)):
        uploads = [
            FakeUpload(
                f"upload-{index}.pdf", PDF_BYTES + f"%{index}\n".encode()
            )
            for index in range(count)
        ]
        assert save_pdfs(uploads, entry) == count
        for index, upload in enumerate(uploads):
            name = f"pdf/{entry.pdf_dir_name}/{index:02d}.pdf"
            expected[name] = upload.getvalue()
    assert read_pdfs(converted_space.root) == expected  # 前提
    assert shown_pdf_count(run_page(BACKUP_PAGE)) == "3"
    backup = create_backup_zip().getvalue()

    assert delete_pdfs(first) == 2
    assert delete_pdfs(second) == 1
    assert read_pdfs(converted_space.root) == {}
    assert shown_pdf_count(run_page(BACKUP_PAGE)) == "0"

    at = restore_on_page(fake_file_uploader, backup)

    assert not at.error
    [success] = at.success
    assert re.search(r"PDF\D*3(?!\d)", success.value), success.value
    assert read_pdfs(converted_space.root) == expected
    assert shown_pdf_count(run_page(BACKUP_PAGE)) == "3"
    fs_pdf = to_fs_path(data_paths.pdf)
    assert first.get_pdf_files() == [
        fs_pdf / first.pdf_dir_name / "00.pdf",
        fs_pdf / first.pdf_dir_name / "01.pdf",
    ]
    assert second.get_pdf_files() == [fs_pdf / second.pdf_dir_name / "00.pdf"]
    if converted_space.redirected:
        # 変換前のパスの下には、ファイルが 1 つも書かれない
        assert [p for p in data_paths.root.rglob("*") if p.is_file()] == []
