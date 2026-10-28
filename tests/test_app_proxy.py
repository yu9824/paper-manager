"""プロキシ設定 (``app.helper._proxy``) の単体テスト.

対象は次のとおり。

- 設定ファイル (``config.json`` の ``proxy`` キー) と環境変数の両方に
  プロキシがあるとき、設定ファイルの値が優先されること (要件 7.1)
- 設定ファイルにプロキシがなく環境変数にだけあるとき、大文字・小文字の
  どちらの環境変数名からも値が取得され、HTTPS のプロキシが未設定なら
  HTTP のプロキシが使われること (要件 7.2)
- どこにも設定がなければ空の辞書が返ること (要件 7.3)
- 設定ファイルが JSON として壊れていても例外にならず、環境変数へ
  フォールバックすること (要件 7.4)
- 保存したときに設定ファイル内のプロキシ以外の項目が保たれ、HTTP・HTTPS の
  両方を空にして保存すると ``proxy`` キーが消えること (要件 7.5)
- 環境へ適用したとき、HTTP・HTTPS それぞれについて大文字・小文字両方の
  環境変数名に値が設定されること (要件 7.6)

保存先は ``data_paths.config_file`` で、実データには触れない。環境変数の
テストでは、各テストの開始時に ``tests/conftest.py`` が 4 つのプロキシ環境
変数を削除する。値の設定には ``monkeypatch`` を使う。

``apply_proxy_to_environment`` は ``os.environ`` を直接書き換える。その
後始末 (テスト後に環境変数が元に戻ること) は、``conftest.py`` が行う。
ただし、各テストの開始時にプロキシ環境変数が削除されるため、後続のテストで
の確認では、復元されずに残った値が隠れてしまう。そこで、復元は子プロセスの
pytest で確かめる。子プロセスでは、テスト 1 件の全工程 (準備・本体・後始末)
が終わった直後の ``os.environ`` を観測するプラグインを使う。
"""

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

import pytest
from conftest import DataPaths

from paper_manager.app.helper._proxy import (
    apply_proxy_to_environment,
    get_proxy_config,
    save_proxy_config,
)

_PROXY_ENV_NAMES = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
_HTTP_ENV_NAMES = ("HTTP_PROXY", "http_proxy")
_HTTPS_ENV_NAMES = ("HTTPS_PROXY", "https_proxy")

# 値の出どころが分かるよう、4 つとも異なる値にする。
_FILE_HTTP = "http://file-http.example.com:8080"
_FILE_HTTPS = "http://file-https.example.com:8443"
_ENV_HTTP = "http://env-http.example.com:3128"
_ENV_HTTPS = "http://env-https.example.com:3129"

# 壊れた設定ファイルの中身 (バイト列で書き出す)。
_BROKEN_CONFIGS = [
    pytest.param(b"{", id="unclosed-brace"),
    pytest.param(b"", id="empty-file"),
    pytest.param(b"not json at all", id="plain-text"),
    pytest.param(
        b'{"proxy": {"http": "http://file-http.exam', id="truncated-in-string"
    ),
    pytest.param(b'{"proxy": {"http": }}', id="missing-value"),
    pytest.param(b"\xff\xfe\x00\x80", id="invalid-utf8"),
    pytest.param(b'{"proxy": "http://not-an-object"}', id="proxy-not-object"),
]


@pytest.fixture
def config_file(data_paths: DataPaths) -> Path:
    """設定ファイル (``config.json``) のパスを返す (まだ存在しない)."""
    return data_paths.config_file


def _write_config(path: Path, config: Any) -> None:
    """``config`` を JSON として ``path`` に書き出す."""
    path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _read_config(path: Path) -> Any:
    """``path`` の JSON を読み込む (アプリの実装を介さない)."""
    return json.loads(path.read_text(encoding="utf-8"))


def _set_env(
    monkeypatch: pytest.MonkeyPatch, env: Mapping[str, str]
) -> None:
    """環境変数をまとめて設定する (テストの後に元へ戻る)."""
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def _proxy_env() -> dict[str, Optional[str]]:
    """4 つのプロキシ環境変数の現在の値を返す (未設定は ``None``)."""
    return {name: os.environ.get(name) for name in _PROXY_ENV_NAMES}


# --- 設定ファイルの優先 (要件 7.1) ----------------------------------------


def test_config_file_takes_priority_over_environment(
    monkeypatch: pytest.MonkeyPatch, config_file: Path
) -> None:
    """設定ファイルと環境変数の両方にあるとき、設定ファイルの値になる."""
    _write_config(
        config_file, {"proxy": {"http": _FILE_HTTP, "https": _FILE_HTTPS}}
    )
    _set_env(
        monkeypatch,
        {
            "HTTP_PROXY": _ENV_HTTP,
            "http_proxy": _ENV_HTTP,
            "HTTPS_PROXY": _ENV_HTTPS,
            "https_proxy": _ENV_HTTPS,
        },
    )

    assert get_proxy_config() == {"http": _FILE_HTTP, "https": _FILE_HTTPS}


@pytest.mark.parametrize(
    ("file_proxy", "env", "expected"),
    [
        pytest.param(
            {"http": _FILE_HTTP},
            {"HTTPS_PROXY": _ENV_HTTPS},
            {"http": _FILE_HTTP, "https": _ENV_HTTPS},
            id="http-from-file-https-from-environment",
        ),
        pytest.param(
            {"https": _FILE_HTTPS},
            {"HTTP_PROXY": _ENV_HTTP},
            {"http": _ENV_HTTP, "https": _FILE_HTTPS},
            id="https-from-file-http-from-environment",
        ),
    ],
)
def test_environment_fills_in_what_config_file_leaves_out(
    monkeypatch: pytest.MonkeyPatch,
    config_file: Path,
    file_proxy: dict[str, str],
    env: dict[str, str],
    expected: dict[str, str],
) -> None:
    """設定ファイルにない側だけが、環境変数から補われる."""
    _write_config(config_file, {"proxy": file_proxy})
    _set_env(monkeypatch, env)

    assert get_proxy_config() == expected


def test_https_follows_http_from_config_file_not_from_environment(
    monkeypatch: pytest.MonkeyPatch, config_file: Path
) -> None:
    """HTTPS が未設定のときの HTTP は、設定ファイルの値が優先される."""
    _write_config(config_file, {"proxy": {"http": _FILE_HTTP}})
    monkeypatch.setenv("HTTP_PROXY", _ENV_HTTP)

    assert get_proxy_config() == {"http": _FILE_HTTP, "https": _FILE_HTTP}


# --- 環境変数だけの設定 (要件 7.2) ----------------------------------------


@pytest.mark.parametrize("http_name", _HTTP_ENV_NAMES)
@pytest.mark.parametrize("https_name", _HTTPS_ENV_NAMES)
def test_environment_names_in_either_case_are_read(
    monkeypatch: pytest.MonkeyPatch, http_name: str, https_name: str
) -> None:
    """大文字・小文字のどちらの環境変数名からも値が取得される."""
    monkeypatch.setenv(http_name, _ENV_HTTP)
    monkeypatch.setenv(https_name, _ENV_HTTPS)

    assert get_proxy_config() == {"http": _ENV_HTTP, "https": _ENV_HTTPS}


@pytest.mark.parametrize("name", _HTTP_ENV_NAMES)
def test_https_falls_back_to_http_when_https_is_unset(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """HTTPS のプロキシが未設定なら、HTTP のプロキシが使われる."""
    monkeypatch.setenv(name, _ENV_HTTP)

    assert get_proxy_config() == {"http": _ENV_HTTP, "https": _ENV_HTTP}


@pytest.mark.parametrize("name", _HTTPS_ENV_NAMES)
def test_https_environment_alone_does_not_set_http(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """HTTPS の環境変数だけがあるとき、HTTP は設定されない."""
    monkeypatch.setenv(name, _ENV_HTTPS)

    assert get_proxy_config() == {"https": _ENV_HTTPS}


def test_environment_is_used_when_config_file_has_no_proxy(
    monkeypatch: pytest.MonkeyPatch, config_file: Path
) -> None:
    """設定ファイルに ``proxy`` キーがなければ、環境変数の値になる."""
    _write_config(config_file, {"theme": "dark"})
    monkeypatch.setenv("HTTP_PROXY", _ENV_HTTP)
    monkeypatch.setenv("https_proxy", _ENV_HTTPS)

    assert get_proxy_config() == {"http": _ENV_HTTP, "https": _ENV_HTTPS}


# --- どこにも設定がない (要件 7.3) ----------------------------------------


def test_empty_when_nothing_is_configured(config_file: Path) -> None:
    """設定ファイルも環境変数もなければ、空の辞書が返る."""
    assert not config_file.exists()

    assert get_proxy_config() == {}


def test_empty_when_config_file_has_no_proxy(config_file: Path) -> None:
    """設定ファイルに ``proxy`` キーがなく環境変数もなければ、空になる."""
    _write_config(config_file, {"theme": "dark"})

    assert get_proxy_config() == {}


@pytest.mark.parametrize("name", _PROXY_ENV_NAMES)
def test_empty_environment_value_counts_as_unset(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """値が空文字列の環境変数は、未設定として扱われる."""
    monkeypatch.setenv(name, "")

    assert get_proxy_config() == {}


# --- 壊れた設定ファイル (要件 7.4) ----------------------------------------


@pytest.mark.parametrize("content", _BROKEN_CONFIGS)
def test_broken_config_file_falls_back_to_environment(
    monkeypatch: pytest.MonkeyPatch, config_file: Path, content: bytes
) -> None:
    """設定ファイルが壊れていても例外にならず、環境変数の値になる."""
    config_file.write_bytes(content)
    monkeypatch.setenv("HTTP_PROXY", _ENV_HTTP)
    monkeypatch.setenv("https_proxy", _ENV_HTTPS)

    assert get_proxy_config() == {"http": _ENV_HTTP, "https": _ENV_HTTPS}


@pytest.mark.parametrize("content", _BROKEN_CONFIGS)
def test_broken_config_file_without_environment_is_empty(
    config_file: Path, content: bytes
) -> None:
    """設定ファイルが壊れていて環境変数もなければ、例外にならず空になる."""
    config_file.write_bytes(content)

    assert get_proxy_config() == {}


# --- 保存 (要件 7.5) ------------------------------------------------------


def test_save_writes_proxy_to_config_file(config_file: Path) -> None:
    """設定ファイルがなくても、データディレクトリ内に保存される."""
    assert not config_file.exists()

    save_proxy_config(_FILE_HTTP, _FILE_HTTPS)

    assert _read_config(config_file) == {
        "proxy": {"http": _FILE_HTTP, "https": _FILE_HTTPS}
    }


def test_save_keeps_settings_other_than_proxy(config_file: Path) -> None:
    """保存しても、プロキシ以外の設定項目はそのまま保たれる."""
    others = {
        "theme": "dark",
        "language": "日本語",
        "nested": {"values": [1, 2, 3], "flag": True},
    }
    _write_config(
        config_file,
        {**others, "proxy": {"http": _ENV_HTTP, "https": _ENV_HTTPS}},
    )

    save_proxy_config(_FILE_HTTP, _FILE_HTTPS)

    assert _read_config(config_file) == {
        **others,
        "proxy": {"http": _FILE_HTTP, "https": _FILE_HTTPS},
    }
    # 非 ASCII 文字は、エスケープされずに UTF-8 のまま読める形で残る。
    assert "日本語" in config_file.read_text(encoding="utf-8")


def test_save_replaces_the_previous_proxy_entirely(config_file: Path) -> None:
    """以前のプロキシ設定は引き継がれず、今回の値で置き換わる."""
    _write_config(
        config_file, {"proxy": {"http": _ENV_HTTP, "https": _ENV_HTTPS}}
    )

    save_proxy_config(_FILE_HTTP, None)

    assert _read_config(config_file) == {"proxy": {"http": _FILE_HTTP}}


@pytest.mark.parametrize(
    ("http_proxy", "https_proxy", "expected"),
    [
        pytest.param(
            _FILE_HTTP, None, {"http": _FILE_HTTP}, id="http-only-none"
        ),
        pytest.param(
            _FILE_HTTP, "", {"http": _FILE_HTTP}, id="http-only-empty"
        ),
        pytest.param(
            None, _FILE_HTTPS, {"https": _FILE_HTTPS}, id="https-only-none"
        ),
        pytest.param(
            "", _FILE_HTTPS, {"https": _FILE_HTTPS}, id="https-only-empty"
        ),
    ],
)
def test_save_stores_only_the_given_side(
    config_file: Path,
    http_proxy: Optional[str],
    https_proxy: Optional[str],
    expected: dict[str, str],
) -> None:
    """片方だけを指定すると、その側だけが保存される."""
    save_proxy_config(http_proxy, https_proxy)

    assert _read_config(config_file) == {"proxy": expected}


@pytest.mark.parametrize(
    ("http_proxy", "https_proxy"),
    [
        pytest.param(None, None, id="none-none"),
        pytest.param("", "", id="empty-empty"),
        pytest.param(None, "", id="none-empty"),
        pytest.param("", None, id="empty-none"),
    ],
)
def test_save_with_both_empty_removes_proxy_and_keeps_others(
    config_file: Path, http_proxy: Optional[str], https_proxy: Optional[str]
) -> None:
    """両方を空にして保存すると ``proxy`` が消え、他の項目は残る."""
    _write_config(
        config_file,
        {
            "theme": "dark",
            "proxy": {"http": _FILE_HTTP, "https": _FILE_HTTPS},
        },
    )

    save_proxy_config(http_proxy, https_proxy)

    assert _read_config(config_file) == {"theme": "dark"}
    assert get_proxy_config() == {}


def test_save_with_both_empty_does_not_create_proxy(
    config_file: Path,
) -> None:
    """設定ファイルがない状態で両方を空にして保存しても、``proxy`` はない."""
    save_proxy_config(None, None)

    assert "proxy" not in _read_config(config_file)
    assert get_proxy_config() == {}


def test_saved_proxy_can_be_read_back(config_file: Path) -> None:
    """保存したプロキシ設定を、取得で同じ内容として読める."""
    save_proxy_config(_FILE_HTTP, _FILE_HTTPS)

    assert get_proxy_config() == {"http": _FILE_HTTP, "https": _FILE_HTTPS}


# --- 環境への適用 (要件 7.6) ----------------------------------------------


def test_apply_sets_all_four_variables_from_config_file(
    config_file: Path,
) -> None:
    """HTTP・HTTPS の値が、それぞれ大文字・小文字両方の名前に設定される.

    ここで設定した環境変数は、``conftest.py`` がテストの後に元へ戻す
    (``test_environment_is_restored_after_applying`` が確かめる)。
    """
    _write_config(
        config_file, {"proxy": {"http": _FILE_HTTP, "https": _FILE_HTTPS}}
    )

    apply_proxy_to_environment()

    assert _proxy_env() == {
        "HTTP_PROXY": _FILE_HTTP,
        "http_proxy": _FILE_HTTP,
        "HTTPS_PROXY": _FILE_HTTPS,
        "https_proxy": _FILE_HTTPS,
    }


def test_apply_uses_http_for_https_when_https_is_not_configured(
    config_file: Path,
) -> None:
    """HTTPS が未設定なら、HTTP の値が HTTPS の 2 つの名前にも設定される."""
    _write_config(config_file, {"proxy": {"http": _FILE_HTTP}})

    apply_proxy_to_environment()

    assert _proxy_env() == dict.fromkeys(_PROXY_ENV_NAMES, _FILE_HTTP)


def test_apply_sets_only_https_variables_when_only_https_is_configured(
    config_file: Path,
) -> None:
    """HTTPS だけが設定されているとき、HTTP の 2 つの名前は設定されない."""
    _write_config(config_file, {"proxy": {"https": _FILE_HTTPS}})

    apply_proxy_to_environment()

    assert _proxy_env() == {
        "HTTP_PROXY": None,
        "http_proxy": None,
        "HTTPS_PROXY": _FILE_HTTPS,
        "https_proxy": _FILE_HTTPS,
    }


def test_apply_copies_environment_to_both_cases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """片方の大文字・小文字だけの環境変数が、両方の名前に設定される."""
    monkeypatch.setenv("http_proxy", _ENV_HTTP)
    monkeypatch.setenv("HTTPS_PROXY", _ENV_HTTPS)

    apply_proxy_to_environment()

    assert _proxy_env() == {
        "HTTP_PROXY": _ENV_HTTP,
        "http_proxy": _ENV_HTTP,
        "HTTPS_PROXY": _ENV_HTTPS,
        "https_proxy": _ENV_HTTPS,
    }


def test_apply_overwrites_environment_with_config_file(
    monkeypatch: pytest.MonkeyPatch, config_file: Path
) -> None:
    """設定ファイルの値が、既存の環境変数を上書きする."""
    _write_config(
        config_file, {"proxy": {"http": _FILE_HTTP, "https": _FILE_HTTPS}}
    )
    _set_env(
        monkeypatch,
        {
            "HTTP_PROXY": _ENV_HTTP,
            "http_proxy": _ENV_HTTP,
            "HTTPS_PROXY": _ENV_HTTPS,
            "https_proxy": _ENV_HTTPS,
        },
    )

    apply_proxy_to_environment()

    assert _proxy_env() == {
        "HTTP_PROXY": _FILE_HTTP,
        "http_proxy": _FILE_HTTP,
        "HTTPS_PROXY": _FILE_HTTPS,
        "https_proxy": _FILE_HTTPS,
    }


def test_apply_changes_nothing_when_nothing_is_configured() -> None:
    """どこにも設定がなければ、環境変数は何も変わらない."""
    before = dict(os.environ)

    apply_proxy_to_environment()

    assert dict(os.environ) == before
    assert _proxy_env() == dict.fromkeys(_PROXY_ENV_NAMES)


# --- 環境変数の復元 -------------------------------------------------------

# 子プロセスの pytest でテスト 1 件を実行し、その全工程が終わった直後の
# os.environ を記録するスクリプト。引数は「テストのノード ID」と
# 「記録の出力先」。
#
# - during: テスト本体の終了直後 (後始末の前) の 4 つのプロキシ環境変数
# - after:  後始末まで終わった後の 4 つのプロキシ環境変数
# - restored: 後始末まで終わった後の os.environ 全体が、テストの開始前と
#   等しいか
_OBSERVER_SCRIPT = """
import json
import os
import sys

import pytest

NAMES = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
record = {}


class Observer:
    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(self, item, nextitem):
        before = dict(os.environ)
        yield
        record["restored"] = dict(os.environ) == before
        record["after"] = {n: os.environ.get(n) for n in NAMES}

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item):
        yield
        record["during"] = {n: os.environ.get(n) for n in NAMES}


args = [sys.argv[1], "-p", "no:cacheprovider"]
code = pytest.main(args, plugins=[Observer()])
with open(sys.argv[2], "w", encoding="utf-8") as f:
    json.dump(record, f)
sys.exit(int(code))
"""


@pytest.mark.parametrize(
    "outside",
    [
        pytest.param({}, id="unset-outside"),
        pytest.param(
            {
                name: f"http://outside-{name.lower()}.invalid:3128"
                for name in _PROXY_ENV_NAMES
            },
            id="set-outside",
        ),
    ],
)
def test_environment_is_restored_after_applying(
    tmp_path: Path, outside: dict[str, str]
) -> None:
    """環境への適用で変わった環境変数は、テストの後に元の状態へ戻る.

    各テストの開始時に ``conftest.py`` がプロキシ環境変数を削除するため、
    後続のテストでは復元の有無を確かめられない。そこで、子プロセスの
    pytest で適用のテストを 1 件実行し、後始末の直後の ``os.environ`` を
    観測する。外側でプロキシ環境変数が未設定の場合は「適用で増えた変数が
    消えること」を、設定済みの場合は「元の値に戻ること」を確かめる。
    """
    observed_file = tmp_path / "observed.json"
    target = (
        f"{Path(__file__)}"
        "::test_apply_sets_all_four_variables_from_config_file"
    )
    # 親の pytest が設定している PYTEST_CURRENT_TEST は、子の pytest が
    # テストの終了時に削除するため、引き継がない (復元の比較に混ざる)。
    # プロキシ環境変数は、親でも (conftest により) 設定されていない。
    child_env = {
        name: value
        for name, value in os.environ.items()
        if name != "PYTEST_CURRENT_TEST" and name not in _PROXY_ENV_NAMES
    }

    result = subprocess.run(
        [sys.executable, "-c", _OBSERVER_SCRIPT, target, str(observed_file)],
        env={**child_env, **outside},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(observed_file.read_text(encoding="utf-8"))
    # 適用で実際に値が変わっていた (空振りの確認)。
    assert observed["during"] == {
        "HTTP_PROXY": _FILE_HTTP,
        "http_proxy": _FILE_HTTP,
        "HTTPS_PROXY": _FILE_HTTPS,
        "https_proxy": _FILE_HTTPS,
    }
    # 後始末の後は、テストの前に外側にあった状態に戻っている。
    assert observed["after"] == {
        name: outside.get(name) for name in _PROXY_ENV_NAMES
    }
    assert observed["restored"] is True
