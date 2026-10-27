"""隔離の仕組み (tests/conftest.py の IsolationHarness) 自体の検証.

対象は次のとおり。

- ガード関数 ``check_isolation`` の受け入れ・拒否 (要件 1.1, 1.2, 1.3)
- データの保存先を示す 4 つのパスが、一時ディレクトリの配下にあること
  (要件 1.1, 1.2)
- テストごとの初期化: データディレクトリ・作業ディレクトリ・プロキシ関連の
  環境変数・外部ネットワークの遮断 (要件 1.4, 1.6, 1.7)
- テストごとの復元: 2 つのテストの組による独立性の検証 (要件 1.4, 1.5)

``conftest`` は pytest が先に読み込んだモジュールを、そのまま import する
(既定の ``prepend`` import mode)。``--import-mode=importlib`` では別の
モジュールとして再実行され、隔離の確立がやり直されるため使えない。

実データディレクトリ (``~/.paper-manager``) は、``check_isolation`` に
渡すパスとして扱うだけで、読み書きしない。
"""

import json
import logging
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import (
    _FORBIDDEN_PATHS,
    _TEMP_ROOT,
    DataPaths,
    IsolationError,
    NetworkBlockedError,
    check_isolation,
)

from paper_manager import _constants
from paper_manager.logging import get_library_root_logger

_PROXY_ENV_NAMES = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
_REAL_DATA_DIR = Path.home() / ".paper-manager"


def _make_symlink(link: Path, target: Path) -> None:
    """``link`` から ``target`` へのシンボリックリンクを作る.

    シンボリックリンクを作れない環境 (権限のない Windows など) では、
    テストを skip する。
    """
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"cannot create a symbolic link here: {e}")


# --- ガード関数 check_isolation -------------------------------------------


@pytest.mark.parametrize(
    "relative",
    ["data", "data/pdf", "a/b/c/list.json"],
    ids=["child", "grandchild", "deep"],
)
def test_check_isolation_accepts_path_under_temp_root(
    tmp_path: Path, relative: str
) -> None:
    """一時ディレクトリの配下のパス (未作成でもよい) を受け入れる."""
    temp_root = tmp_path / "root"
    temp_root.mkdir()
    forbidden = [tmp_path / "elsewhere", _REAL_DATA_DIR]

    check_isolation(temp_root / relative, temp_root, forbidden)


@pytest.mark.parametrize(
    "relative",
    ["other", ".", "root-evil/data", "root/../escaped"],
    ids=["sibling", "parent", "shared-prefix", "dotdot-escape"],
)
def test_check_isolation_rejects_path_outside_temp_root(
    tmp_path: Path, relative: str
) -> None:
    """一時ディレクトリの外のパスを拒否し、メッセージに検出したパスを含める."""
    temp_root = tmp_path / "root"
    temp_root.mkdir()
    data_dir = tmp_path / relative

    with pytest.raises(IsolationError) as excinfo:
        check_isolation(data_dir, temp_root, [])

    assert str(data_dir.resolve()) in str(excinfo.value)


@pytest.mark.parametrize(
    "relative",
    ["", "pdf", "list.json"],
    ids=["itself", "pdf-dir", "list-file"],
)
def test_check_isolation_rejects_real_data_dir(
    tmp_path: Path, relative: str
) -> None:
    """実データディレクトリとその配下を、一時ディレクトリの外として拒否する."""
    data_dir = _REAL_DATA_DIR / relative

    with pytest.raises(IsolationError) as excinfo:
        check_isolation(data_dir, tmp_path, [_REAL_DATA_DIR])

    assert str(data_dir.resolve()) in str(excinfo.value)


def test_check_isolation_rejects_real_data_dir_inside_temp_root() -> None:
    """一時ディレクトリが実データを覆う場合も、禁止パスとして拒否する."""
    with pytest.raises(IsolationError):
        check_isolation(_REAL_DATA_DIR, Path.home(), [_REAL_DATA_DIR])


@pytest.mark.parametrize(
    "relative",
    ["preset", "preset/pdf", "preset/a/b"],
    ids=["itself", "child", "grandchild"],
)
def test_check_isolation_rejects_preset_path(
    tmp_path: Path, relative: str
) -> None:
    """実行前に指定されていたパスとその配下を拒否する.

    一時ディレクトリの配下にあるパスを禁止パスにして、禁止パスの検査だけで
    拒否されることを確かめる。メッセージには検出したパスと禁止パスの両方を
    含める。
    """
    temp_root = tmp_path / "root"
    temp_root.mkdir()
    preset = temp_root / "preset"
    data_dir = temp_root / relative

    with pytest.raises(IsolationError) as excinfo:
        check_isolation(data_dir, temp_root, [preset])

    assert str(data_dir.resolve()) in str(excinfo.value)
    assert str(preset.resolve()) in str(excinfo.value)


def test_check_isolation_checks_every_forbidden_path(tmp_path: Path) -> None:
    """禁止パスが複数あるとき、先頭以外に該当する場合も拒否する."""
    temp_root = tmp_path / "root"
    temp_root.mkdir()
    forbidden = [temp_root / "first", temp_root / "second"]

    with pytest.raises(IsolationError):
        check_isolation(temp_root / "second" / "data", temp_root, forbidden)


def test_check_isolation_does_not_overreach_on_forbidden_path(
    tmp_path: Path,
) -> None:
    """禁止パスと名前の接頭辞だけが同じ兄弟のパスは受け入れる."""
    temp_root = tmp_path / "root"
    temp_root.mkdir()
    preset = temp_root / "preset"

    check_isolation(temp_root / "preset2", temp_root, [preset])
    check_isolation(temp_root / "other" / "preset", temp_root, [preset])


def test_check_isolation_resolves_symlink_out_of_temp_root(
    tmp_path: Path,
) -> None:
    """一時ディレクトリ内のシンボリックリンクが外を指すなら拒否する."""
    temp_root = tmp_path / "root"
    temp_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    _make_symlink(temp_root / "link", outside)

    with pytest.raises(IsolationError):
        check_isolation(temp_root / "link" / "data", temp_root, [])


def test_check_isolation_resolves_symlink_to_forbidden_path(
    tmp_path: Path,
) -> None:
    """禁止パスを指すシンボリックリンクを経由しても拒否する."""
    temp_root = tmp_path / "root"
    preset = temp_root / "preset"
    preset.mkdir(parents=True)
    _make_symlink(temp_root / "alias", preset)

    with pytest.raises(IsolationError):
        check_isolation(temp_root / "alias" / "data", temp_root, [preset])


def test_conftest_protects_real_data_dir() -> None:
    """conftest が保持する禁止パスに、実データディレクトリが含まれる."""
    assert _REAL_DATA_DIR in _FORBIDDEN_PATHS


# --- データの保存先を示す 4 つのパス --------------------------------------


def test_data_paths_equal_the_application_constants(
    data_paths: DataPaths,
) -> None:
    """fixture の 4 つのパスが、アプリの定数と一致する."""
    assert data_paths == DataPaths(
        root=_constants.DIRPATH_DATA,
        pdf=_constants.DIRPATH_PDF,
        list_file=_constants.FILEPATH_LIST,
        config_file=_constants.FILEPATH_CONFIG,
    )


@pytest.mark.parametrize("name", DataPaths._fields)
def test_data_path_is_under_temp_root(
    data_paths: DataPaths, name: str
) -> None:
    """4 つのパスは、一時ディレクトリの配下にあり、実データと重ならない."""
    path = getattr(data_paths, name)

    check_isolation(path, _TEMP_ROOT, [_REAL_DATA_DIR, *_FORBIDDEN_PATHS])
    # check_isolation を介さず、解決後のパスを直接確かめる。
    assert path.resolve().is_relative_to(_TEMP_ROOT.resolve())
    assert not path.resolve().is_relative_to(_REAL_DATA_DIR.resolve())


def test_data_dir_env_var_is_set_by_the_test_suite(
    data_paths: DataPaths,
) -> None:
    """実行者が設定しなくても、環境変数が一時ディレクトリを指している."""
    value = os.environ["PAPER_MANAGER_DATA_DIR"]

    assert Path(value) == data_paths.root
    assert Path(value).resolve().is_relative_to(_TEMP_ROOT.resolve())


# --- テストごとの初期化 ---------------------------------------------------


def test_data_dir_exists_and_is_empty_at_start(
    data_paths: DataPaths,
) -> None:
    """各テストの開始時点で、データディレクトリは空で存在する."""
    assert data_paths.root.is_dir()
    assert list(data_paths.root.iterdir()) == []


def test_cwd_is_the_test_tmp_path(tmp_path: Path) -> None:
    """作業ディレクトリが、テスト専用の一時ディレクトリである."""
    assert Path.cwd().resolve() == tmp_path.resolve()


def test_no_proxy_env_at_start() -> None:
    """各テストの開始時点で、プロキシ関連の環境変数がない."""
    present = [name for name in _PROXY_ENV_NAMES if name in os.environ]

    assert present == []


def test_proxy_env_is_cleared_even_when_set_outside(tmp_path: Path) -> None:
    """実行環境にプロキシが設定されていても、テストの開始時には消える.

    ``test_no_proxy_env_at_start`` は、実行環境にプロキシがなければ何も
    確かめられない。そこで、プロキシ関連の環境変数を設定した子プロセスの
    pytest で、そのテストを実行する。
    """
    env = {name: "http://proxy.invalid:8080" for name in _PROXY_ENV_NAMES}
    target = f"{Path(__file__)}::test_no_proxy_env_at_start"

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", target],
        env={**os.environ, **env},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert result.returncode == 0, result.stdout + result.stderr


# --- 外部ネットワークの遮断 -----------------------------------------------


def test_connection_to_external_host_is_blocked() -> None:
    """外部ホストへの接続は、専用の例外で失敗する."""
    with pytest.raises(NetworkBlockedError, match="example.com"):
        socket.create_connection(("example.com", 80), timeout=1)


def test_name_resolution_is_blocked() -> None:
    """名前解決は、専用の例外で失敗する."""
    with pytest.raises(NetworkBlockedError, match="example.com"):
        socket.getaddrinfo("example.com", 80)


@pytest.mark.parametrize(
    ("family", "address"),
    [
        (socket.AF_INET, ("127.0.0.1", 9)),
        (socket.AF_INET6, ("::1", 9)),
    ],
    ids=["ipv4", "ipv6"],
)
def test_socket_connect_is_blocked(
    family: socket.AddressFamily, address: tuple
) -> None:
    """名前解決を経ない直接の接続も、専用の例外で失敗する."""
    try:
        sock = socket.socket(family, socket.SOCK_STREAM)
    except OSError as e:
        pytest.skip(f"address family {family.name} is unavailable: {e}")

    with sock:
        with pytest.raises(NetworkBlockedError, match=address[0]):
            sock.connect(address)


# --- テストの独立性と状態の復元 (2 つのテストの組) ------------------------
#
# 次の 2 つは、この順に定義された組。
#
# - ``test_state_pair_1_changes_state`` が状態を変更する
# - ``test_state_pair_2_state_is_restored`` が、変更が残っていないことを確かめる
#
# 2 つ目は単独で実行しても成功する (その場合は、初期状態を確かめるだけ)。
# 1 つ目の後に実行して成功することで、復元が実際に働いていることを確かめる。
# 1 つ目は、``monkeypatch`` を使わず直接変更する。使うと ``monkeypatch`` が
# 自分で戻してしまい、テスト基盤の復元を検証できなくなる。

_MARKER_ENV = "PAPER_MANAGER_TEST_ISOLATION_MARKER"
# 比較の基準: どのテストも実行されていない収集時のログレベル。
# conftest と同じ方法でルートロガーを取得して読む。
_BASELINE_LEVEL = get_library_root_logger().level
_CHANGED_LEVEL = (
    logging.ERROR if _BASELINE_LEVEL == logging.DEBUG else logging.DEBUG
)


def test_state_pair_1_changes_state(data_paths: DataPaths) -> None:
    """組の 1 つ目: 論文リスト・環境変数・ログレベルを変更する."""
    root_logger = get_library_root_logger()
    assert root_logger.level == _BASELINE_LEVEL
    assert _MARKER_ENV not in os.environ
    assert list(data_paths.root.iterdir()) == []

    paper_list = {
        "Taro2020_0": {
            "ENTRYTYPE": "article",
            "ID": "Taro2020_0",
            "title": "状態の復元を確かめる論文",
        }
    }
    data_paths.list_file.write_text(
        json.dumps(paper_list, ensure_ascii=False), encoding="utf-8"
    )
    os.environ[_MARKER_ENV] = "changed"
    root_logger.setLevel(_CHANGED_LEVEL)

    assert data_paths.list_file.is_file()
    assert os.environ[_MARKER_ENV] == "changed"
    assert root_logger.level == _CHANGED_LEVEL != _BASELINE_LEVEL


def test_state_pair_2_state_is_restored(data_paths: DataPaths) -> None:
    """組の 2 つ目: 1 つ目の変更が、このテストに残っていない."""
    assert list(data_paths.root.iterdir()) == []
    assert not data_paths.list_file.exists()
    assert _MARKER_ENV not in os.environ
    assert get_library_root_logger().level == _BASELINE_LEVEL
