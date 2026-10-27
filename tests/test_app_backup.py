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
- 既知バグ KB3 (復元時にデータディレクトリの外へ書き込まれる) を strict な
  xfail として記録する (要件 13.1, 13.2)

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
from collections.abc import Iterator, Mapping
from datetime import datetime
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import NamedTuple, Optional, Union

import pytest
from conftest import (
    _FORBIDDEN_PATHS,
    _TEMP_ROOT,
    PDF_BYTES,
    DataPaths,
    SampleData,
    check_isolation,
)

from paper_manager import __version__
from paper_manager.app.helper._backup import (
    create_backup_zip,
    get_backup_info,
    restore_from_zip,
)
from paper_manager.entry import PaperList

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


# --- 既知バグ KB3 -----------------------------------------------------------


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
    resolved = target.resolve()
    problems = []
    if reached != target:
        problems.append(f"{TRAVERSAL_ENTRY} reaches {reached}, not {target}")
    if not resolved.is_relative_to(_TEMP_ROOT.resolve()):
        problems.append(f"{target} is outside the session temporary directory")
    if resolved.is_relative_to(data_paths.root.resolve()):
        problems.append(f"{target} is inside the data directory")
    if any(resolved.is_relative_to(p.resolve()) for p in _FORBIDDEN_PATHS):
        problems.append(f"{target} is under a protected path")
    if target.exists() or target.is_symlink():
        problems.append(f"{target} already exists")
    if problems:
        pytest.fail("unsafe KB3 repro target: " + "; ".join(problems))
    try:
        yield target
    finally:
        target.unlink(missing_ok=True)


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


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "KNOWN BUG: restore_from_zip がメンバー名を検証せず、"
        "DIRPATH_DATA / メンバー名 にそのまま書き込むため、'..' を含む名前"
        "でデータディレクトリの外に書き込める (KB3, パストラバーサル. "
        "画面側 pages/_restore.py にも同じ実装がある). "
        "Repro: 'pdf/../../escaped.pdf' というメンバーを含む zip を復元する "
        "(データディレクトリ <temp_root>/data に対し <temp_root>/escaped.pdf "
        "が作られる). 本来はデータディレクトリの外へは書き込まれない"
    ),
)
def test_restore_does_not_write_outside_data_dir(
    traversal_zip: Path, escaped_target: Path
) -> None:
    """'..' を含むメンバーがあっても、データディレクトリの外へ書き込まない."""
    # 結果 (拒否する / 無害な場所に書く のいずれでもよい) は問わない。
    restore_from_zip(traversal_zip)

    assert not escaped_target.exists(), (
        f"restore wrote outside the data directory: {escaped_target}"
    )
