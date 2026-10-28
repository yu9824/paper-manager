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

ライブラリのルートロガーは ``--debug`` で変更される。テスト後のレベルの復元は
``tests/conftest.py`` が行い、その確認は子プロセスの pytest で行う。復元されるのは
レベルだけなので、ハンドラと ``propagate`` はこのモジュールの ``library_logger``
fixture が戻す。
"""

import json
import logging
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import NamedTuple

import pytest
import streamlit
from conftest import APP_DIR

import paper_manager
import paper_manager.__main__ as cli_main
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
