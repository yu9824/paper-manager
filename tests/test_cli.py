"""CLI (``paper_manager.__main__``) の引数処理と run / version のテスト.

対象は次のとおり。

- バージョン表示オプション (``-v`` / ``--version``) がパッケージの
  バージョンを表示すること (要件 10.1)
- ``run`` / ``version`` サブコマンドが、Streamlit のサーバーを起動せずに、
  未知の引数をそのまま Streamlit へ渡すこと。``run`` ではアプリの
  トップページ (``app/_list.py``) が起動対象になること (要件 10.2)
- 実行時の引数に ``--client.showSidebarNavigation`` が含まれなければ
  ``--client.showSidebarNavigation false`` が既定で付与され、明示的に
  指定されていれば重複して付与されないこと (要件 10.3)
- ``run --debug`` でライブラリのルートロガーのレベルが DEBUG になること
  (要件 10.4)
- ``backup`` が、出力先を指定すればそのパスに、省略すれば作業ディレクトリに
  日時入りの名前 (``paper-manager-backup_YYYYMMDD_HHMMSS.zip``) でバックアップ
  zip を作ること (要件 10.5)
- ``restore`` が、存在しないファイルの指定で終了コード 1 になりデータを
  変えないこと (要件 10.6)、``--force`` なしでは確認入力が ``yes`` / ``y``
  の場合だけ復元し、それ以外では復元せず終了コード 0 で終了すること
  (要件 10.7)、復元が失敗を返したときにエラーメッセージを表示して終了
  コード 1 になること (要件 10.8)

Streamlit の起動関数 (``cli.main_run`` / ``cli.main_version``) は、受け取った
引数を記録するだけの関数に差し替える (``streamlit_calls`` fixture)。差し替えは
``paper_manager.__main__`` が参照する ``cli`` ごと行うため、これらの関数以外の
Streamlit の起動経路が使われると ``AttributeError`` で失敗し、実際には
サーバーもプロセスも起動しない。外部ネットワークは ``tests/conftest.py`` が
遮断している。

既知バグ (strict な xfail):

- KB8: ``--client.showSidebarNavigation=true`` のような ``=`` 形式の明示指定が
  認識されず、``--client.showSidebarNavigation false`` が重複して付与される
- KB9: ``version`` サブコマンドにもナビゲーション無効化が付与され、
  ``streamlit version`` が受け付けずに終了コード 2 で失敗する (KB1 と同根)
- KB1: ``main()`` が全サブコマンドにナビゲーション無効化を付与するため、
  コマンド全体の引数解析を経由した ``backup`` / ``restore`` が終了コード 2 で
  失敗する (``backup`` と ``restore`` の 2 件)

backup / restore のテストは、コマンド全体の引数解析 (KB1) を避け、
サブコマンドの関数 (``cli_main.backup`` / ``cli_main.restore``) に引数の
リストを直接渡す。バックアップ zip の出力先は ``tmp_path`` で、``tmp_path`` は
各テストの作業ディレクトリでもある。出力先を省略するテストも作業ディレクトリ
は ``tmp_path`` のままで、リポジトリの作業ツリーには何も作られない。復元は
データディレクトリ (隔離済み) に対してだけ行う。zip の中身の検証は
``tests/test_app_backup.py`` に任せ、ここでは CLI が正しく呼び出すこと、
終了コード、データの状態を検証する。

ライブラリのルートロガーは ``--debug`` で変更される。テスト後のレベルの復元は
``tests/conftest.py`` が行い、その確認は子プロセスの pytest で行う。復元されるのは
レベルだけなので、ハンドラと ``propagate`` はこのモジュールの ``library_logger``
fixture が戻す。
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import NamedTuple, Optional, Union

import pytest
import streamlit
from conftest import (
    _FORBIDDEN_PATHS,
    _TEMP_ROOT,
    APP_DIR,
    DataPaths,
    SampleData,
    check_isolation,
)

import paper_manager
import paper_manager.__main__ as cli_main
from paper_manager.app.helper._backup import create_backup_zip
from paper_manager.logging import get_library_root_logger

_PROG = "paper-manager"
_NAV = "--client.showSidebarNavigation"

# --- 補助 -----------------------------------------------------------------


class StreamlitCalls(NamedTuple):
    """Streamlit の起動関数に渡された引数の記録 (呼び出しごとに 1 要素)."""

    run: list[list[str]]
    version: list[list[str]]


@pytest.fixture
def streamlit_calls(monkeypatch: pytest.MonkeyPatch) -> StreamlitCalls:
    """Streamlit の起動関数を、引数を記録するだけの関数に差し替える.

    ``paper_manager.__main__`` の ``cli`` を、``main_run`` / ``main_version``
    だけを持つ名前空間に差し替える。サーバーは起動せず、これら以外の
    Streamlit の関数も呼べない。差し替えはテストの後に元へ戻る。

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        差し替えに使う。

    Returns
    -------
    StreamlitCalls
        ``run`` が ``main_run`` に、``version`` が ``main_version`` に
        渡された引数の記録。
    """
    calls = StreamlitCalls(run=[], version=[])

    def record_run(args: Sequence[str]) -> None:
        calls.run.append(list(args))

    def record_version(args: Sequence[str]) -> None:
        calls.version.append(list(args))

    monkeypatch.setattr(
        cli_main,
        "cli",
        SimpleNamespace(main_run=record_run, main_version=record_version),
    )
    return calls


@pytest.fixture
def library_logger(monkeypatch: pytest.MonkeyPatch) -> logging.Logger:
    """レベルを WARNING にしたライブラリのルートロガーを返す.

    既定のレベル (INFO) とも DEBUG とも異なる値にして、``--debug`` による
    変更と、指定なしでの不変の両方を見分けられるようにする。

    レベルはテストの後に ``tests/conftest.py`` が戻す。ハンドラと
    ``propagate`` は conftest が戻さないため、ここで ``monkeypatch`` に
    任せる (``handlers`` は複製に差し替えるので、テスト中の追加・削除は
    元のリストに及ばない)。

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        ハンドラと ``propagate`` の保護に使う。

    Returns
    -------
    logging.Logger
        ライブラリのルートロガー。
    """
    logger = get_library_root_logger()
    monkeypatch.setattr(logger, "handlers", list(logger.handlers))
    monkeypatch.setattr(logger, "propagate", logger.propagate)
    logger.setLevel(logging.WARNING)
    return logger


def _run_forwarded(calls: StreamlitCalls) -> list[str]:
    """``main_run`` がちょうど 1 回だけ呼ばれたことを確かめ、その引数を返す."""
    assert len(calls.run) == 1, f"main_run calls: {calls.run}"
    assert calls.version == [], f"main_version calls: {calls.version}"
    return calls.run[0]


def _version_forwarded(calls: StreamlitCalls) -> list[str]:
    """``main_version`` がちょうど 1 回だけ呼ばれたことを確かめ、引数を返す."""
    assert len(calls.version) == 1, f"main_version calls: {calls.version}"
    assert calls.run == [], f"main_run calls: {calls.run}"
    return calls.version[0]


# --- バージョン表示 (要件 10.1) -------------------------------------------


@pytest.mark.parametrize("flag", ["-v", "--version"])
def test_version_option_prints_package_version(
    flag: str,
    capsys: pytest.CaptureFixture[str],
    streamlit_calls: StreamlitCalls,
) -> None:
    """``-v`` / ``--version`` はパッケージのバージョンを表示して終了する."""
    expected = paper_manager.__version__
    assert expected  # 空文字列だと「含まれる」が空振りする

    with pytest.raises(SystemExit) as excinfo:
        cli_main.main([flag], prog=_PROG)

    assert excinfo.value.code == 0
    assert expected in capsys.readouterr().out
    assert streamlit_calls.run == []
    assert streamlit_calls.version == []


def test_entrypoint_reads_arguments_from_sys_argv(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    streamlit_calls: StreamlitCalls,
) -> None:
    """コンソールスクリプトの入口は ``sys.argv[1:]`` を引数として使う."""
    monkeypatch.setattr(sys, "argv", [_PROG, "--version"])

    with pytest.raises(SystemExit) as excinfo:
        cli_main.entrypoint()

    assert excinfo.value.code == 0
    assert paper_manager.__version__ in capsys.readouterr().out


# --- run / version への引数の受け渡しと起動対象 (要件 10.2) ---------------

_PASSTHROUGH_ARGS = [
    pytest.param(["--server.port", "8502"], id="option-and-value"),
    pytest.param(["--server.headless=true"], id="option-with-equals"),
    pytest.param(
        [
            "--server.port",
            "8502",
            "--browser.gatherUsageStats",
            "false",
            "extra",
        ],
        id="several-options-and-positional",
    ),
    pytest.param(["--help"], id="long-help"),
    pytest.param(["-h"], id="short-help"),
]


def test_run_launches_the_top_page(streamlit_calls: StreamlitCalls) -> None:
    """``run`` の起動対象は、アプリのトップページ (``_list.py``) になる."""
    cli_main.main(["run"], prog=_PROG)

    target = Path(_run_forwarded(streamlit_calls)[0])
    top_page = APP_DIR / "_list.py"
    assert top_page.is_file()
    assert target.resolve() == top_page.resolve()


@pytest.mark.parametrize("given", _PASSTHROUGH_ARGS)
def test_run_forwards_unknown_arguments_unchanged(
    given: list[str], streamlit_calls: StreamlitCalls
) -> None:
    """``run`` は未知の引数を、順序を保ってそのまま Streamlit へ渡す."""
    cli_main.main(["run", *given], prog=_PROG)

    forwarded = _run_forwarded(streamlit_calls)
    assert forwarded[1 : 1 + len(given)] == given


@pytest.mark.parametrize("given", _PASSTHROUGH_ARGS)
def test_version_forwards_unknown_arguments_unchanged(
    given: list[str], streamlit_calls: StreamlitCalls
) -> None:
    """``version`` は未知の引数を、順序を保ってそのまま Streamlit へ渡す."""
    cli_main.main(["version", *given], prog=_PROG)

    forwarded = _version_forwarded(streamlit_calls)
    assert forwarded[: len(given)] == given


def test_entrypoint_forwards_sys_argv_to_streamlit(
    monkeypatch: pytest.MonkeyPatch, streamlit_calls: StreamlitCalls
) -> None:
    """入口は ``sys.argv`` の先頭 (コマンド名) を除いて引数を解釈する."""
    monkeypatch.setattr(
        sys, "argv", [_PROG, "run", "--server.port", "8502", "extra"]
    )

    cli_main.entrypoint()

    forwarded = _run_forwarded(streamlit_calls)
    assert Path(forwarded[0]).resolve() == (APP_DIR / "_list.py").resolve()
    assert forwarded[1:4] == ["--server.port", "8502", "extra"]


# --- サイドバーナビゲーション無効化の既定の付与 (要件 10.3) ---------------


def test_run_without_options_adds_only_the_navigation_default(
    streamlit_calls: StreamlitCalls,
) -> None:
    """引数がなければ、起動対象に続けて既定の指定だけが付く."""
    cli_main.main(["run"], prog=_PROG)

    assert _run_forwarded(streamlit_calls)[1:] == [_NAV, "false"]


def test_navigation_default_is_added_next_to_other_arguments(
    streamlit_calls: StreamlitCalls,
) -> None:
    """ほかの引数があっても、既定の指定が 1 組だけ付与される."""
    given = ["--server.port", "8502", "--theme.base", "dark"]

    cli_main.main(["run", *given], prog=_PROG)

    forwarded = _run_forwarded(streamlit_calls)
    assert forwarded[1 : 1 + len(given)] == given
    assert forwarded.count(_NAV) == 1
    assert forwarded[forwarded.index(_NAV) + 1] == "false"
    assert len(forwarded) == 1 + len(given) + 2


@pytest.mark.parametrize("value", ["true", "false"])
@pytest.mark.parametrize(
    "others",
    [
        pytest.param([], id="alone"),
        pytest.param(["--server.port", "8502"], id="after-other-option"),
        pytest.param(["--debug"], id="with-debug"),
    ],
)
def test_explicit_navigation_flag_is_not_duplicated(
    value: str,
    others: list[str],
    streamlit_calls: StreamlitCalls,
    library_logger: logging.Logger,
) -> None:
    """明示された指定は、そのまま 1 組だけ渡され、重複も上書きもされない."""
    given = [*others, _NAV, value]

    cli_main.main(["run", *given], prog=_PROG)

    forwarded = _run_forwarded(streamlit_calls)
    expected = [arg for arg in given if arg != "--debug"]
    assert forwarded[1:] == expected
    assert forwarded.count(_NAV) == 1


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN BUG: ナビゲーション無効化の既定の付与が"
        " `--client.showSidebarNavigation=VALUE` の `=` 形式の明示指定を"
        "認識せず (KB8)、`--client.showSidebarNavigation false` を重複して"
        "付与する。後ろに付くため Streamlit (click) では `false` が勝ち、"
        "ユーザーが明示した `true` が無視される。"
        " Repro: `main(['run', '--client.showSidebarNavigation=true'])`"
        " (`paper-manager run --client.showSidebarNavigation=true`)。"
        "本来は 明示された指定が重複して付与されず、そのまま Streamlit へ"
        "渡される (要件 10.3)"
    ),
)
def test_explicit_navigation_flag_with_equals_is_not_duplicated(
    streamlit_calls: StreamlitCalls,
) -> None:
    """``=`` 形式で明示された指定も、重複して付与されない (KB8)."""
    given = f"{_NAV}=true"

    cli_main.main(["run", given], prog=_PROG)

    forwarded = _run_forwarded(streamlit_calls)
    assert forwarded[1:] == [given]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN BUG: `version` サブコマンドにも"
        " `--client.showSidebarNavigation false` が付与され、"
        "`streamlit version` がこのオプションを受け付けずに"
        " `No such option` で終了コード 2 になる (KB9。KB1 と同根で、"
        "`backup` / `restore` に加えて `version` も常に失敗する)。"
        " Repro: `main(['version'])` (`paper-manager version`)。"
        "本来は Streamlit のバージョンが表示され、終了コード 0 で終了する"
    ),
)
def test_version_subcommand_prints_streamlit_version(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``version`` は ``streamlit version`` と同じく Streamlit のバージョンを出す.

    Streamlit の起動関数は差し替えない (``streamlit version`` はサーバーを
    起動しない)。``main_version`` は ``sys.argv[1]`` を書き換えるため、
    ``sys.argv`` は実際のコマンドと同じ形に差し替えておく。
    """
    monkeypatch.setattr(sys, "argv", [_PROG, "version"])

    with pytest.raises(SystemExit) as excinfo:
        cli_main.main(["version"], prog=_PROG)

    captured = capsys.readouterr()
    assert excinfo.value.code in (0, None), (
        f"exit code {excinfo.value.code}: {captured.err}"
    )
    assert streamlit.__version__ in captured.out


# --- デバッグ指定でのログレベル (要件 10.4) -------------------------------


@pytest.mark.parametrize(
    "given",
    [
        pytest.param(["--debug"], id="debug-only"),
        pytest.param(["--debug", "--server.port", "8502"], id="debug-first"),
        pytest.param(["--server.port", "8502", "--debug"], id="debug-last"),
    ],
)
def test_run_debug_sets_library_logger_to_debug(
    given: list[str],
    streamlit_calls: StreamlitCalls,
    library_logger: logging.Logger,
) -> None:
    """``run --debug`` でライブラリのルートロガーが DEBUG になる.

    ``--debug`` は ``paper-manager`` 自身の指定なので、Streamlit には
    渡されない (渡すと Streamlit が未知のオプションとして失敗する)。
    """
    assert library_logger.level == logging.WARNING

    cli_main.main(["run", *given], prog=_PROG)

    assert library_logger.level == logging.DEBUG
    assert library_logger.isEnabledFor(logging.DEBUG)
    forwarded = _run_forwarded(streamlit_calls)
    expected = [arg for arg in given if arg != "--debug"]
    assert "--debug" not in forwarded
    assert forwarded[1 : 1 + len(expected)] == expected


def test_run_without_debug_keeps_the_log_level(
    streamlit_calls: StreamlitCalls, library_logger: logging.Logger
) -> None:
    """``--debug`` がなければ、ログレベルは変わらない."""
    cli_main.main(["run", "--server.port", "8502"], prog=_PROG)

    assert library_logger.level == logging.WARNING
    _run_forwarded(streamlit_calls)


# 子プロセスの pytest でテスト 1 件を実行し、そのライブラリのルートロガーの
# レベルを 3 つの時点で記録するスクリプト。引数は「テストのノード ID」と
# 「記録の出力先」。
#
# - before: テストの準備 (fixture) より前
# - during: テスト本体の終了直後 (後始末の前)
# - after:  後始末まで終わった後
#
# 同じプロセスの次のテストでは、テストの前に自分でレベルを揃え直す場合に
# 復元の漏れが隠れる。1 件の全工程の直後を観測すれば、隠れない。
_OBSERVER_SCRIPT = """
import json
import sys

import pytest

record = {}


def current_level():
    from paper_manager.logging import get_library_root_logger

    return get_library_root_logger().level


class Observer:
    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(self, item, nextitem):
        record["before"] = current_level()
        yield
        record["after"] = current_level()

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item):
        yield
        record["during"] = current_level()


args = [sys.argv[1], "-p", "no:cacheprovider"]
code = pytest.main(args, plugins=[Observer()])
with open(sys.argv[2], "w", encoding="utf-8") as f:
    json.dump(record, f)
sys.exit(int(code))
"""


def test_log_level_is_restored_after_debug_run(tmp_path: Path) -> None:
    """``--debug`` で変わったログレベルは、テストの後に元へ戻る.

    後続のテストでは、``library_logger`` がレベルを揃え直すため、復元の
    漏れを確かめられない。そこで、子プロセスの pytest で ``--debug`` の
    テストを 1 件だけ実行し、全工程が終わった直後のレベルを観測する。
    ``tests/conftest.py`` がレベルを戻さなくなると、この確認が失敗する。
    """
    observed_file = tmp_path / "observed.json"
    target = (
        f"{Path(__file__)}"
        "::test_run_debug_sets_library_logger_to_debug[debug-only]"
    )
    # 親の pytest が設定している PYTEST_CURRENT_TEST は、子の pytest が
    # テストの終了時に削除するため、引き継がない。
    child_env = {
        name: value
        for name, value in os.environ.items()
        if name != "PYTEST_CURRENT_TEST"
    }

    result = subprocess.run(
        [sys.executable, "-c", _OBSERVER_SCRIPT, target, str(observed_file)],
        env=child_env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(observed_file.read_text(encoding="utf-8"))
    # テストの前は DEBUG ではなく、テスト中に実際に DEBUG になっていた
    # (空振りの確認)。
    assert observed["before"] != logging.DEBUG
    assert observed["during"] == logging.DEBUG
    # 後始末の後は、テストの前のレベルに戻っている。
    assert observed["after"] == observed["before"]


# --- backup / restore: 補助 ------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKUP_NAME = re.compile(r"paper-manager-backup_(\d{8}_\d{6})\.zip")
_TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
# 要件 10.8 が求める、zip として不正なファイルの復元失敗のメッセージ.
_BAD_ZIP_MESSAGE = "無効なzipファイルです。"
# 復元を実行すると上書きされる (= 実行されたかどうかが見分けられる) 中身.
_CHANGED_LIST = b'{"changed": "after backup"}\n'

Tree = dict[str, Optional[bytes]]


def _snapshot_tree(root: Path) -> Tree:
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


def _empty_dir(directory: Path) -> None:
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


def _repository_zips() -> list[str]:
    """リポジトリ直下にある zip ファイル名の一覧を返す."""
    return sorted(path.name for path in _REPO_ROOT.glob("*.zip"))


def _main_exit_code(argv: list[str]) -> Union[int, str, None]:
    """コマンド全体の引数解析 (``main``) を実行し、終了コードを返す.

    Parameters
    ----------
    argv : list[str]
        ``paper-manager`` に続く引数。

    Returns
    -------
    Union[int, str, None]
        ``SystemExit`` の終了コード。``SystemExit`` にならなければ ``None``。
    """
    try:
        cli_main.main(argv, prog=_PROG)
    except SystemExit as exc:
        return exc.code
    return None


class BackedUp(NamedTuple):
    """バックアップを取った時点のデータの状態."""

    zip_path: Path  # データのバックアップ (``tmp_path/backups/backup.zip``)
    tree: Tree  # バックアップ時点のデータディレクトリ全体


@pytest.fixture
def backed_up(
    tmp_path: Path, sample_data: SampleData, data_paths: DataPaths
) -> BackedUp:
    """サンプルデータのバックアップを、CLI を使わずに作る.

    zip は ``tmp_path`` の下の専用ディレクトリに作るため、作業ディレクトリ
    (``tmp_path``) 直下には何も増えない。

    Parameters
    ----------
    tmp_path : Path
        zip の出力先の親。
    sample_data : SampleData
        バックアップされるデータ。
    data_paths : DataPaths
        データの保存先。

    Returns
    -------
    BackedUp
        zip のパスと、バックアップ時点のデータの状態。
    """
    tree = _snapshot_tree(data_paths.root)
    assert any(name.endswith(".pdf") for name in tree)  # PDF もある
    zip_dir = tmp_path / "backups"
    zip_dir.mkdir()
    zip_path = zip_dir / "backup.zip"
    create_backup_zip(zip_path)
    return BackedUp(zip_path=zip_path, tree=tree)


@pytest.fixture
def no_new_repository_zip() -> Iterator[None]:
    """テストの前後でリポジトリ直下の zip が増えていないことを確かめる."""
    before = _repository_zips()
    yield
    assert _repository_zips() == before


@pytest.fixture
def fake_input(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[Optional[str]], list[str]]:
    """``input()`` を、決まった回答を返す関数に差し替える installer を返す.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        差し替えに使う。テストの後に元へ戻る。

    Returns
    -------
    Callable[[Optional[str]], list[str]]
        ``install(answer)`` は、``input()`` が ``answer`` を返すように
        して、渡されたプロンプトの記録 (呼び出しごとに 1 要素) を返す。
        ``answer`` が ``None`` なら、``input()`` が呼ばれた時点でテストを
        失敗させる。
    """

    def install(answer: Optional[str]) -> list[str]:
        prompts: list[str] = []

        def _input(prompt: str = "") -> str:
            prompts.append(prompt)
            if answer is None:
                pytest.fail("input() was called, but no prompt is expected")
            return answer

        monkeypatch.setattr("builtins.input", _input)
        return prompts

    return install


RestoreOutcome = Union[tuple[bool, str], Exception, None]


@pytest.fixture
def fake_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[RestoreOutcome], list[tuple[object, object]]]:
    """``restore_from_zip`` を、決まった結果を返す関数に差し替える.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        差し替えに使う。テストの後に元へ戻る。

    Returns
    -------
    Callable[[RestoreOutcome], list[tuple[object, object]]]
        ``install(outcome)`` は、``restore_from_zip`` が ``outcome`` を
        返す (例外なら送出する) ようにして、呼び出しの記録 (呼び出しごとに
        ``(zip_path, update_session_state)``) を返す。``outcome`` が
        ``None`` なら、呼ばれた時点でテストを失敗させる。
    """

    def install(outcome: RestoreOutcome) -> list[tuple[object, object]]:
        calls: list[tuple[object, object]] = []

        def _restore_from_zip(
            zip_path: object, update_session_state: object = None
        ) -> tuple[bool, str]:
            calls.append((zip_path, update_session_state))
            if outcome is None:
                pytest.fail("restore_from_zip() was called unexpectedly")
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr(cli_main, "restore_from_zip", _restore_from_zip)
        return calls

    return install


# --- backup: 出力先 (要件 10.5) ------------------------------------------


@pytest.mark.parametrize("flag", ["-o", "--output"])
def test_backup_creates_the_zip_at_the_given_output_path(
    flag: str,
    tmp_path: Path,
    sample_data: SampleData,
    data_paths: DataPaths,
) -> None:
    """出力先を指定すると、そのパスにバックアップ zip が作られる."""
    output = tmp_path / "out" / "chosen-name.zip"
    output.parent.mkdir()
    list_bytes = data_paths.list_file.read_bytes()
    tree_before = _snapshot_tree(data_paths.root)

    cli_main.backup([flag, str(output)])

    assert zipfile.is_zipfile(output)
    with zipfile.ZipFile(output) as zf:
        assert {"version.json", "list.json"} <= set(zf.namelist())
        assert zf.read("list.json") == list_bytes
    # 指定先以外には何も作られず、データも変わらない。
    assert [path.name for path in tmp_path.iterdir()] == ["out"]
    assert [path.name for path in output.parent.iterdir()] == [output.name]
    assert _snapshot_tree(data_paths.root) == tree_before


def test_backup_resolves_a_relative_output_in_the_working_directory(
    tmp_path: Path, sample_data: SampleData
) -> None:
    """相対パスの出力先は、作業ディレクトリ (``tmp_path``) からの位置になる."""
    assert Path.cwd().resolve() == tmp_path.resolve()

    cli_main.backup(["-o", "relative-backup.zip"])

    assert [path.name for path in tmp_path.iterdir()] == [
        "relative-backup.zip"
    ]
    assert zipfile.is_zipfile(tmp_path / "relative-backup.zip")


@pytest.mark.usefixtures("no_new_repository_zip")
def test_backup_without_output_creates_a_timestamped_zip_in_cwd(
    tmp_path: Path, sample_data: SampleData, data_paths: DataPaths
) -> None:
    """出力先を省略すると、作業ディレクトリに日時入りの名前で作られる.

    日時は実行時の値なので、名前の形式 (接頭辞・日時の形・拡張子) と、
    テストの実行中の時刻であることを確かめる。
    """
    assert Path.cwd().resolve() == tmp_path.resolve()
    list_bytes = data_paths.list_file.read_bytes()
    tree_before = _snapshot_tree(data_paths.root)
    started = datetime.now().replace(microsecond=0)

    cli_main.backup([])

    finished = datetime.now()
    created = list(tmp_path.iterdir())
    assert len(created) == 1
    match = _BACKUP_NAME.fullmatch(created[0].name)
    assert match, created[0].name
    stamp = datetime.strptime(match.group(1), _TIMESTAMP_FORMAT)
    assert started <= stamp <= finished
    with zipfile.ZipFile(created[0]) as zf:
        assert zf.read("list.json") == list_bytes
    # データディレクトリには何も作られない。
    assert _snapshot_tree(data_paths.root) == tree_before


@pytest.mark.usefixtures("no_new_repository_zip")
def test_backup_default_name_is_built_from_the_current_datetime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sample_data: SampleData
) -> None:
    """既定の名前は ``paper-manager-backup_YYYYMMDD_HHMMSS.zip`` の形になる.

    現在日時を固定して、名前全体を完全一致で確かめる。
    """

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2024, 1, 2, 3, 4, 5)

    monkeypatch.setattr(cli_main, "datetime", FixedDatetime)

    cli_main.backup([])

    assert [path.name for path in tmp_path.iterdir()] == [
        "paper-manager-backup_20240102_030405.zip"
    ]


@pytest.mark.usefixtures("no_new_repository_zip")
def test_backup_to_a_missing_directory_exits_with_1_and_creates_nothing(
    tmp_path: Path, sample_data: SampleData, data_paths: DataPaths
) -> None:
    """出力先のディレクトリがなければ、終了コード 1 で何も作られない."""
    output = tmp_path / "no-such-dir" / "backup.zip"
    tree_before = _snapshot_tree(data_paths.root)

    with pytest.raises(SystemExit) as excinfo:
        cli_main.backup(["-o", str(output)])

    assert excinfo.value.code == 1
    assert list(tmp_path.iterdir()) == []
    assert _snapshot_tree(data_paths.root) == tree_before


# --- restore: 存在しないファイル (要件 10.6) ------------------------------


@pytest.mark.parametrize("force", [True, False], ids=["force", "confirm"])
@pytest.mark.parametrize("kind", ["missing-file", "directory"])
def test_restore_of_a_nonexistent_backup_exits_with_1_and_keeps_data(
    kind: str,
    force: bool,
    tmp_path: Path,
    sample_data: SampleData,
    data_paths: DataPaths,
    fake_input: Callable[[Optional[str]], list[str]],
    fake_restore: Callable[[RestoreOutcome], list[tuple[object, object]]],
) -> None:
    """存在しないバックアップの指定は、確認も復元もせず終了コード 1 になる.

    ファイルでないもの (ディレクトリ) も同じ。確認入力と復元の関数が
    呼ばれると、その時点でテストが失敗する。
    """
    target = tmp_path / "missing.zip"
    if kind == "directory":
        target = tmp_path / "a-directory.zip"
        target.mkdir()
    fake_input(None)
    fake_restore(None)
    tree_before = _snapshot_tree(data_paths.root)
    assert tree_before  # サンプルデータがあり、変更があれば検出できる

    with pytest.raises(SystemExit) as excinfo:
        cli_main.restore([str(target), *(["--force"] if force else [])])

    assert excinfo.value.code == 1
    assert _snapshot_tree(data_paths.root) == tree_before


# --- restore: 確認入力 (要件 10.7) ----------------------------------------


@pytest.mark.parametrize("answer", ["yes", "y", "YES", "Y"])
def test_restore_proceeds_when_the_confirmation_is_yes(
    answer: str,
    backed_up: BackedUp,
    data_paths: DataPaths,
    fake_input: Callable[[Optional[str]], list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """確認入力が ``yes`` / ``y`` なら、復元が実行され、終了コードは 0 になる."""
    _empty_dir(data_paths.root)
    assert _snapshot_tree(data_paths.root) == {}
    prompts = fake_input(answer)

    cli_main.restore([str(backed_up.zip_path)])  # SystemExit にならない

    assert len(prompts) == 1
    assert _snapshot_tree(data_paths.root) == backed_up.tree
    out = capsys.readouterr().out
    assert str(backed_up.zip_path) in out  # 確認の前に対象が示される
    assert "復元が完了しました" in out
    assert "キャンセル" not in out


@pytest.mark.parametrize(
    "answer",
    ["no", "n", "N", "", "ye", "yeah", "ok", "はい"],
    ids=["no", "n", "N", "empty", "ye", "yeah", "ok", "hai"],
)
def test_restore_is_cancelled_when_the_confirmation_is_not_yes(
    answer: str,
    backed_up: BackedUp,
    data_paths: DataPaths,
    fake_input: Callable[[Optional[str]], list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``yes`` / ``y`` 以外の確認入力は、復元せずに終了コード 0 で終了する.

    復元が実行されれば上書きされるよう、バックアップの後でデータを
    変えておく。
    """
    data_paths.list_file.write_bytes(_CHANGED_LIST)
    tree_before = _snapshot_tree(data_paths.root)
    assert tree_before != backed_up.tree
    prompts = fake_input(answer)

    with pytest.raises(SystemExit) as excinfo:
        cli_main.restore([str(backed_up.zip_path)])

    assert excinfo.value.code == 0
    assert len(prompts) == 1
    assert _snapshot_tree(data_paths.root) == tree_before
    assert data_paths.list_file.read_bytes() == _CHANGED_LIST
    out = capsys.readouterr().out
    assert "復元をキャンセルしました" in out
    assert "復元が完了しました" not in out


def test_restore_with_force_restores_without_asking(
    backed_up: BackedUp,
    data_paths: DataPaths,
    fake_input: Callable[[Optional[str]], list[str]],
) -> None:
    """``--force`` を付けると、確認入力を求めずに復元する."""
    _empty_dir(data_paths.root)
    fake_input(None)  # 呼ばれると失敗する

    cli_main.restore([str(backed_up.zip_path), "--force"])

    assert _snapshot_tree(data_paths.root) == backed_up.tree


# --- restore: 失敗 (要件 10.8) --------------------------------------------


@pytest.mark.parametrize("confirm", [False, True], ids=["force", "confirm"])
@pytest.mark.parametrize(
    "content", [b"this is not a zip file", b""], ids=["garbage", "empty"]
)
def test_restore_of_an_invalid_zip_exits_with_1_and_keeps_data(
    content: bytes,
    confirm: bool,
    tmp_path: Path,
    sample_data: SampleData,
    data_paths: DataPaths,
    fake_input: Callable[[Optional[str]], list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """zip として不正なファイルの復元は、メッセージを出して終了コード 1 になる."""
    bad_zip = tmp_path / "bad.zip"
    bad_zip.write_bytes(content)
    fake_input("y" if confirm else None)
    tree_before = _snapshot_tree(data_paths.root)
    assert tree_before  # サンプルデータがあり、変更があれば検出できる

    with pytest.raises(SystemExit) as excinfo:
        cli_main.restore([str(bad_zip), *([] if confirm else ["--force"])])

    assert excinfo.value.code == 1
    assert _snapshot_tree(data_paths.root) == tree_before
    assert f"エラー: {_BAD_ZIP_MESSAGE}" in capsys.readouterr().out


def test_restore_shows_the_failure_message_and_exits_with_1(
    backed_up: BackedUp,
    data_paths: DataPaths,
    fake_restore: Callable[[RestoreOutcome], list[tuple[object, object]]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``restore_from_zip`` が失敗を返したら、その文言を出して終了コード 1 になる.

    ``restore_from_zip`` は指定したバックアップのパスだけを渡され、
    セッションステート (Streamlit) は更新しない設定で呼ばれる。
    """
    calls = fake_restore((False, "復元に失敗しました (テスト用)"))
    tree_before = _snapshot_tree(data_paths.root)

    with pytest.raises(SystemExit) as excinfo:
        cli_main.restore([str(backed_up.zip_path), "--force"])

    assert excinfo.value.code == 1
    assert calls == [(backed_up.zip_path, False)]
    assert "エラー: 復元に失敗しました (テスト用)" in capsys.readouterr().out
    assert _snapshot_tree(data_paths.root) == tree_before


def test_restore_exits_with_1_when_restoring_raises(
    backed_up: BackedUp,
    data_paths: DataPaths,
    fake_restore: Callable[[RestoreOutcome], list[tuple[object, object]]],
) -> None:
    """復元の処理が例外を送出しても、終了コード 1 で終了する."""
    calls = fake_restore(RuntimeError("unexpected failure in restore"))
    tree_before = _snapshot_tree(data_paths.root)

    with pytest.raises(SystemExit) as excinfo:
        cli_main.restore([str(backed_up.zip_path), "--force"])

    assert excinfo.value.code == 1
    assert len(calls) == 1
    assert _snapshot_tree(data_paths.root) == tree_before


# --- backup と restore の往復 ---------------------------------------------


def test_backup_then_restore_round_trips_through_the_commands(
    tmp_path: Path, sample_data: SampleData, data_paths: DataPaths
) -> None:
    """``backup`` で作った zip を ``restore`` へ渡すと、データが元に戻る."""
    tree_before = _snapshot_tree(data_paths.root)
    output = tmp_path / "roundtrip.zip"
    cli_main.backup(["-o", str(output)])
    _empty_dir(data_paths.root)
    assert _snapshot_tree(data_paths.root) == {}

    cli_main.restore([str(output), "--force"])

    assert _snapshot_tree(data_paths.root) == tree_before


# --- 既知バグ KB1: コマンド全体の引数解析を経由した backup / restore --------
#
# 次の 2 つの欠陥が重なっており、どちらも ``main()`` の実装に由来する。
#
# - ``main()`` が全サブコマンドに ``--client.showSidebarNavigation false`` を
#   渡すため、``backup`` / ``restore`` 自身の引数解析 (argparse) が失敗して
#   終了コード 2 になる (``version`` が失敗する KB9 と同根)。
# - ``main()`` のサブパーサーが ``-o`` / ``backup_file`` / ``--force`` を
#   消費し、``backup()`` / ``restore()`` には未知の引数だけを渡すため、
#   前者を直しても指定した出力先やバックアップファイルが渡らない。
#
# 本来はコマンドラインから直接バックアップ・復元できる。前者だけを直した
# 場合、この 2 件は strict な xfail のまま (別の理由で) 失敗し続ける。


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN BUG: `main()` が全サブコマンドに"
        " `--client.showSidebarNavigation false` を付与するため、"
        "`backup` が `unrecognized arguments` として拒否し終了コード 2 で"
        "失敗する (KB1。`version` が同じ理由で失敗する KB9 と同根)。"
        "さらに `main()` のサブパーサーが `-o` を消費して `backup()` に渡さ"
        "ないため、付与を止めても出力先が無視される。"
        " Repro: `main(['backup', '-o', PATH])`"
        " (`paper-manager backup -o PATH`)。"
        "本来は PATH にバックアップ zip が作られ、終了コード 0 で終了する"
        " (要件 13.1)"
    ),
)
@pytest.mark.usefixtures("no_new_repository_zip")
def test_backup_command_creates_the_zip_at_the_output_path(
    tmp_path: Path,
    sample_data: SampleData,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``main(["backup", "-o", PATH])`` で PATH にバックアップが作られる (KB1)."""
    output = tmp_path / "out" / "backup.zip"
    output.parent.mkdir()

    code = _main_exit_code(["backup", "-o", str(output)])

    assert code in (0, None), f"exit code {code}: {capsys.readouterr().err}"
    assert zipfile.is_zipfile(output)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN BUG: `main()` が全サブコマンドに"
        " `--client.showSidebarNavigation false` を付与するため、"
        "`restore` が `unrecognized arguments` として拒否し終了コード 2 で"
        "失敗する (KB1。`version` が同じ理由で失敗する KB9 と同根)。"
        "さらに `main()` のサブパーサーが `backup_file` と `--force` を消費"
        "して `restore()` に渡さないため、付与を止めても必須の"
        " `backup_file` が足りずに失敗する。"
        " Repro: `main(['restore', PATH, '--force'])`"
        " (`paper-manager restore PATH --force`)。"
        "本来は PATH のバックアップからデータが復元され、終了コード 0 で"
        "終了する (要件 13.1)"
    ),
)
def test_restore_command_restores_the_data(
    backed_up: BackedUp,
    data_paths: DataPaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``main(["restore", PATH, "--force"])`` でデータが復元される (KB1)."""
    _empty_dir(data_paths.root)

    code = _main_exit_code(["restore", str(backed_up.zip_path), "--force"])

    assert code in (0, None), f"exit code {code}: {capsys.readouterr().err}"
    assert _snapshot_tree(data_paths.root) == backed_up.tree
