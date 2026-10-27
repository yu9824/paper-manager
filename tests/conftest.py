"""pytest の共通設定: 実データからの隔離 (IsolationHarness).

``paper_manager._constants`` はデータの保存先を import 時に確定する。
そのため、``paper_manager`` を初めて import するより前に環境変数
``PAPER_MANAGER_DATA_DIR`` をセッション専用の一時ディレクトリへ向ける
必要がある。このファイルは、他のどのテストモジュールよりも先に pytest に
読み込まれることを利用して、モジュールレベルでその処理を行う。

``paper_manager`` を import するコードは、必ずこのファイルの
「隔離の確立」より後に置くこと。先に import すると、実データを指した
まま確定してしまう (ガードが検出してセッションを止める)。
"""

import importlib
import os
import shutil
import sys
import tempfile
import warnings
from collections.abc import Iterable, Mapping
from pathlib import Path

import pytest

_ENV_DATA_DIR = "PAPER_MANAGER_DATA_DIR"
_TEMP_ROOT_PREFIX = "paper-manager-test-"
_DATA_PATH_NAMES = (
    "DIRPATH_DATA",
    "DIRPATH_PDF",
    "FILEPATH_LIST",
    "FILEPATH_CONFIG",
)


class IsolationError(RuntimeError):
    """データの保存先がテスト用に隔離されていない場合の例外."""


def check_isolation(
    data_dir: Path, temp_root: Path, forbidden: Iterable[Path]
) -> None:
    """データの保存先が隔離されていることを検査する.

    シンボリックリンクを解決したうえで、``data_dir`` が ``temp_root`` の
    配下にあり、かつ ``forbidden`` のいずれとも一致せず、その配下でも
    ないことを確かめる。

    Parameters
    ----------
    data_dir : Path
        検査するデータの保存先 (存在しなくてもよい)。
    temp_root : Path
        セッション専用の一時ディレクトリ。
    forbidden : Iterable[Path]
        データの保存先にしてはならないパス
        (実データディレクトリなど)。

    Raises
    ------
    IsolationError
        ``data_dir`` が ``temp_root`` の配下にない場合、または
        ``forbidden`` のいずれかと一致する、もしくはその配下にある場合。
    """
    resolved = data_dir.resolve()
    root = temp_root.resolve()
    if not resolved.is_relative_to(root):
        raise IsolationError(
            f"data path {resolved} is not under the session temporary "
            f"directory {root}"
        )
    for path in forbidden:
        banned = path.resolve()
        if resolved.is_relative_to(banned):
            raise IsolationError(
                f"data path {resolved} is, or is under, the protected "
                f"path {banned}"
            )


def _collect_forbidden_paths(
    environ: Mapping[str, str], home: Path
) -> list[Path]:
    """データの保存先にしてはならないパスを集める.

    Parameters
    ----------
    environ : Mapping[str, str]
        テスト基盤が環境変数を上書きする前の環境変数。
    home : Path
        ホームディレクトリ。

    Returns
    -------
    list[Path]
        実データディレクトリ (``<home>/.paper-manager``) と、
        ``PAPER_MANAGER_DATA_DIR`` が設定されていればその値。
    """
    forbidden = [home / ".paper-manager"]
    preset = environ.get(_ENV_DATA_DIR)
    if preset:
        forbidden.append(Path(preset).expanduser())
    return forbidden


def _establish_isolation() -> Path:
    """データの保存先を一時ディレクトリへ向けて ``paper_manager`` を読み込む.

    Returns
    -------
    Path
        セッション専用の一時ディレクトリ。セッション終了時に削除する。
        データの保存先はその配下の ``data`` ディレクトリ。

    Notes
    -----
    次のいずれかの場合は ``pytest.exit`` でセッション全体を止め、
    テストを 1 件も実行しない。

    - ``paper_manager`` が、この処理より前に読み込み済みの場合
    - 確定したデータの保存先が、一時ディレクトリの配下にない、
      または実データなどの禁止パスと重なる場合

    conftest の読み込み中に送出された ``pytest.exit`` は pytest により
    usage error に包まれるため、``returncode`` の値にかかわらず終了
    コードは 4 になる。
    """
    if "paper_manager" in sys.modules:
        pytest.exit(
            "isolation failed: paper_manager was imported before "
            "tests/conftest.py, so its data paths may already point to "
            "the real data directory. Make sure nothing imports "
            "paper_manager before the test suite is set up.",
            returncode=1,
        )

    forbidden = _collect_forbidden_paths(os.environ, Path.home())
    temp_root = Path(tempfile.mkdtemp(prefix=_TEMP_ROOT_PREFIX))
    os.environ[_ENV_DATA_DIR] = str(temp_root / "data")

    constants = importlib.import_module("paper_manager._constants")
    try:
        for name in _DATA_PATH_NAMES:
            check_isolation(getattr(constants, name), temp_root, forbidden)
    except IsolationError as e:
        shutil.rmtree(temp_root, ignore_errors=True)
        pytest.exit(
            f"isolation failed: {e} "
            f"(protected paths: {[str(p) for p in forbidden]})",
            returncode=1,
        )
    return temp_root


# --- 隔離の確立 -----------------------------------------------------------
# この行より後で、paper_manager を import してよい。
_TEMP_ROOT = _establish_isolation()
# --------------------------------------------------------------------------


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """セッション専用の一時ディレクトリを削除する."""
    try:
        shutil.rmtree(_TEMP_ROOT)
    except FileNotFoundError:
        pass
    except OSError as e:
        warnings.warn(
            f"failed to remove the temporary directory {_TEMP_ROOT}: {e}",
            RuntimeWarning,
            stacklevel=1,
        )
