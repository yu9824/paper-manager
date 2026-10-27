"""pytest の共通設定: 実データからの隔離 (IsolationHarness) と共有 fixture.

``paper_manager._constants`` はデータの保存先を import 時に確定する。
そのため、``paper_manager`` を初めて import するより前に環境変数
``PAPER_MANAGER_DATA_DIR`` をセッション専用の一時ディレクトリへ向ける
必要がある。このファイルは、他のどのテストモジュールよりも先に pytest に
読み込まれることを利用して、モジュールレベルでその処理を行う。

``paper_manager`` を import するコードは、必ずこのファイルの
「隔離の確立」より後に置くこと。先に import すると、実データを指した
まま確定してしまう (ガードが検出してセッションを止める)。
"""

import copy
import importlib
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import warnings
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import ExitStack
from pathlib import Path
from typing import Any, NamedTuple, NoReturn, Optional, Union

import pytest

_ENV_DATA_DIR = "PAPER_MANAGER_DATA_DIR"
_TEMP_ROOT_PREFIX = "paper-manager-test-"
_DATA_PATH_NAMES = (
    "DIRPATH_DATA",
    "DIRPATH_PDF",
    "FILEPATH_LIST",
    "FILEPATH_CONFIG",
)
_PROXY_ENV_NAMES = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")


class IsolationError(RuntimeError):
    """データの保存先がテスト用に隔離されていない場合の例外."""


class NetworkBlockedError(RuntimeError):
    """テスト中に外部ネットワークへアクセスしようとした場合の例外."""


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
# _FORBIDDEN_PATHS は、_establish_isolation が検査に使うものと同じ値。
# テストごとの初期化 (_isolate_each_test) でも再検査するために保持する。
_FORBIDDEN_PATHS = _collect_forbidden_paths(os.environ, Path.home())
_TEMP_ROOT = _establish_isolation()
# --------------------------------------------------------------------------

import streamlit as st  # noqa: E402
from crossref.restful import Works  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

import paper_manager  # noqa: E402
from paper_manager import _constants  # noqa: E402
from paper_manager.entry import Entry, PaperList  # noqa: E402
from paper_manager.logging import get_library_root_logger  # noqa: E402


class DataPaths(NamedTuple):
    """隔離されたデータの保存先を示す 4 つのパス."""

    root: Path
    pdf: Path
    list_file: Path
    config_file: Path


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


@pytest.fixture
def data_paths() -> DataPaths:
    """データの保存先を示す 4 つのパスを返す.

    Returns
    -------
    DataPaths
        ``paper_manager._constants`` の ``DIRPATH_DATA``・
        ``DIRPATH_PDF``・``FILEPATH_LIST``・``FILEPATH_CONFIG``。
        すべてセッション専用の一時ディレクトリの配下にある。
    """
    return DataPaths(
        root=_constants.DIRPATH_DATA,
        pdf=_constants.DIRPATH_PDF,
        list_file=_constants.FILEPATH_LIST,
        config_file=_constants.FILEPATH_CONFIG,
    )


def _reset_data_dir() -> None:
    """データディレクトリを空にして作り直す.

    Raises
    ------
    IsolationError
        データディレクトリが一時ディレクトリの配下にない、または
        禁止パスと重なる場合。このとき何も削除しない。
    """
    data_dir = _constants.DIRPATH_DATA
    check_isolation(data_dir, _TEMP_ROOT, _FORBIDDEN_PATHS)
    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True)


def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """外部ネットワークへの接続と名前解決を ``NetworkBlockedError`` にする.

    ``socket.socket.connect`` は、AF_INET / AF_INET6 の場合だけ遮断する。
    それ以外 (AF_UNIX など) は本来の動作に通す。

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        差し替えに使う。テストの後に元へ戻る。
    """
    original_connect = socket.socket.connect

    def blocked_connect(self: socket.socket, address: Any) -> None:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise NetworkBlockedError(
                f"network access is blocked in tests: {address[0]}"
            )
        return original_connect(self, address)

    def blocked_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> NoReturn:
        raise NetworkBlockedError(
            f"network access is blocked in tests: {host}"
        )

    monkeypatch.setattr(socket.socket, "connect", blocked_connect)
    monkeypatch.setattr(socket, "getaddrinfo", blocked_getaddrinfo)


def _restore_environ(saved: Mapping[str, str]) -> None:
    """``os.environ`` を保存した内容へ戻す (追加・変更・削除のすべて)."""
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture(autouse=True)
def _isolate_each_test(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[None]:
    """各テストの前に初期化し、後に元の状態へ戻す.

    前:

    - データディレクトリを空にして作り直す
    - 作業ディレクトリを ``tmp_path`` へ移す
    - プロキシ関連の環境変数を消す
    - 外部ネットワークへの接続と名前解決を遮断する

    後 (テストや前処理が例外で失敗しても行う):

    - ``os.environ`` をテスト前の内容へ戻す
    - ライブラリのルートロガーのレベルを戻す
    - bare mode の session state を消す

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        作業ディレクトリ・環境変数・ネットワークの差し替えに使う。
    tmp_path : Path
        テスト専用の一時ディレクトリ。作業ディレクトリになる。
    """
    with ExitStack() as restore:
        restore.callback(st.session_state.clear)
        root_logger = get_library_root_logger()
        restore.callback(root_logger.setLevel, root_logger.level)
        restore.callback(_restore_environ, dict(os.environ))

        _reset_data_dir()
        monkeypatch.chdir(tmp_path)
        for name in _PROXY_ENV_NAMES:
            monkeypatch.delenv(name, raising=False)
        _block_network(monkeypatch)
        yield


# --- TestDataFactory ------------------------------------------------------
# エントリ・PDF・サンプルデータを、隔離されたデータディレクトリに作る。

# 最小限の PDF の中身. ``%PDF-`` で始まるだけで、描画はできない。
PDF_BYTES = b"%PDF-1.4\n%test\n"

_ENTRY_DEFAULTS = {
    "ENTRYTYPE": "article",
    "author": "Alice Smith",
    "title": "A Study of Testing",
    "journal": "Journal of Testing",
    "year": "2020",
}
_SAMPLE_CONFIG = {
    "proxy": {
        "http": "http://proxy.example.com:8080",
        "https": "http://proxy.example.com:8443",
    }
}


def write_pdf(path: Path, content: bytes = PDF_BYTES) -> Path:
    """``path`` に PDF の中身を書き出す.

    親ディレクトリがなければ作る。既存のファイルは上書きする。

    Parameters
    ----------
    path : Path
        書き出し先のファイルパス。
    content : bytes, default PDF_BYTES
        書き出す中身。

    Returns
    -------
    Path
        書き出したファイルのパス (``path`` と同じ)。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


class FakeUpload(io.BytesIO):
    """アップロードされたファイル (``UploadedFile``) の代わりに使う.

    ``getvalue()`` などのバイト列の読み出しは ``io.BytesIO`` のものを使い、
    アップロードされたファイルと同じく ``name`` を持つ。

    Parameters
    ----------
    name : str
        アップロードされたファイルの名前。
    content : bytes
        ファイルの中身。
    """

    name: str

    def __init__(self, name: str, content: bytes) -> None:
        super().__init__(content)
        self.name = name


class SampleData(NamedTuple):
    """``sample_data`` が作ったデータ."""

    paper_list: PaperList  # ``data_paths.list_file`` に保存済み
    with_pdf_key: str  # 00.pdf と 01.pdf を持つエントリのキー
    without_pdf_key: str  # PDF を持たないエントリのキー
    orphan_pdf: Path  # どのエントリにも紐づかない PDF (pdf/<フォルダ>/00.pdf)


@pytest.fixture
def make_entry() -> Callable[..., Entry]:
    """既定値を持つエントリを作る関数を返す.

    既定値は ``ENTRYTYPE=article`` と ``author``・``title``・``journal``・
    ``year``。キーワード引数で、既定値の上書きと、他のフィールドの追加が
    できる。キーの大文字・小文字は ``Entry`` が正規化する。

    Returns
    -------
    Callable[..., Entry]
        ``make_entry(**fields) -> Entry``。呼ぶたびに別のエントリを返す。
    """

    def _make_entry(**fields: str) -> Entry:
        return Entry({**_ENTRY_DEFAULTS, **fields})

    return _make_entry


def _register_entry(paper_list: PaperList, entry: Entry) -> str:
    """``entry`` に識別子を付けて ``paper_list`` に加え、そのキーを返す."""
    key = entry.get_key(paper_list.keys())
    entry["ID"] = key
    paper_list[key] = entry
    return key


@pytest.fixture
def sample_data(
    data_paths: DataPaths, make_entry: Callable[..., Entry]
) -> SampleData:
    """データディレクトリにサンプルデータを作る.

    作るもの:

    - ``list.json``: PDF を 2 件持つエントリ (日本語のタイトル) と、
      PDF を持たないエントリ
    - ``config.json``: プロキシ設定
    - PDF 3 件: 上記の 1 つ目のエントリの ``00.pdf``・``01.pdf``
      (中身はそれぞれ異なる) と、どのエントリにも紐づかない孤立 PDF 1 件

    PDF の置き場所は、``Entry.get_pdf_dir`` から導出する。

    Parameters
    ----------
    data_paths : DataPaths
        データの保存先。
    make_entry : Callable[..., Entry]
        エントリを作る関数。

    Returns
    -------
    SampleData
        作った論文リストと、各エントリのキー、孤立 PDF のパス。
    """
    with_pdf = make_entry(
        author="Taro Yamada",
        title="深層学習による画像認識の研究",
        year="2021",
    )
    without_pdf = make_entry(
        author="Bob Jones", title="An Entry Without Any PDF", year="2022"
    )
    deleted = make_entry(
        author="Carol Orphan", title="A Deleted Paper", year="2018"
    )

    paper_list = PaperList({})
    with_pdf_key = _register_entry(paper_list, with_pdf)
    without_pdf_key = _register_entry(paper_list, without_pdf)
    paper_list.to_file(data_paths.list_file)

    data_paths.config_file.write_text(
        json.dumps(_SAMPLE_CONFIG, ensure_ascii=False, indent=2),
        encoding=_constants.ENCODING,
    )

    pdf_dir = with_pdf.get_pdf_dir(data_paths.pdf)
    for index in range(2):
        write_pdf(
            pdf_dir / f"{index:02d}.pdf",
            PDF_BYTES + f"%with-pdf {index:02d}\n".encode("ascii"),
        )
    # 論文リストに登録していないエントリのフォルダ = 孤立 PDF
    orphan_pdf = write_pdf(
        deleted.get_pdf_dir(data_paths.pdf) / "00.pdf",
        PDF_BYTES + b"%orphan 00\n",
    )

    return SampleData(
        paper_list=paper_list,
        with_pdf_key=with_pdf_key,
        without_pdf_key=without_pdf_key,
        orphan_pdf=orphan_pdf,
    )


# --- ExternalFakes --------------------------------------------------------
# Crossref・ファイルアップロード欄・UI 通知を、テストから制御できる代替に
# 差し替える。差し替えはすべて ``monkeypatch`` で行い、テストの後に元へ戻る。
# AppTest はページのスクリプトを同じプロセスで実行するため、モジュール属性
# の差し替えはページにも効く。

CrossrefMetadata = dict[str, Any]
Uploads = Union[FakeUpload, list[FakeUpload], None]


def crossref_article(
    doi: str = "10.1234/test", type: str = "journal-article"
) -> CrossrefMetadata:
    """Crossref の固定メタデータを作る.

    登録ページの DOI 経路が参照するキー (``type``・``title``・
    ``author``・``container-title``・``published``・``DOI``) を持つ。
    ``volume``・``issue``・``page`` は含まない。

    Parameters
    ----------
    doi : str, default "10.1234/test"
        メタデータの ``DOI``。
    type : str, default "journal-article"
        Crossref のエントリタイプ。``"book"`` を指定すると、必須項目
        ``publisher`` が不足するエントリになる (必須項目の不足の検証用)。

    Returns
    -------
    CrossrefMetadata
        登録ページを通すと、``type=journal-article`` の場合は次のエントリに
        なる。

        - ``title``: ``Fixed Metadata from Crossref``
        - ``author``: ``Jane Doe and John Roe``
        - ``journal``: ``Journal of Fixed Metadata``
        - ``year``: ``2021``
        - ``DOI``: ``doi``
    """
    return {
        "type": type,
        "DOI": doi,
        "title": ["Fixed Metadata from Crossref"],
        "author": [
            {"given": "Jane", "family": "Doe"},
            {"given": "John", "family": "Roe"},
        ],
        "container-title": ["Journal of Fixed Metadata"],
        "published": {"date-parts": [[2021, 5, 17]]},
    }


@pytest.fixture
def fake_crossref(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[Optional[CrossrefMetadata]], list[str]]:
    """Crossref の DOI 取得を、固定の応答を返す処理に差し替える関数を返す.

    返された関数 ``install(metadata)`` を呼ぶと
    ``crossref.restful.Works.doi`` が差し替わる。呼ぶまでは何も変わらない。
    ``install`` を再び呼ぶと、前の ``install`` の差し替えを置き換える。

    - ``metadata`` が辞書: 問い合わせた DOI にかかわらず、その複製を返す
    - ``metadata`` が ``None``: ``None`` を返す (取得失敗。実際の
      ``Works.doi`` も、見つからない DOI に対して ``None`` を返す)

    Returns
    -------
    Callable[[Optional[CrossrefMetadata]], list[str]]
        ``install(metadata) -> list[str]``。返す一覧には、``Works.doi`` が
        呼ばれるたびに、問い合わせた DOI が順に加わる。
    """

    def install(metadata: Optional[CrossrefMetadata]) -> list[str]:
        requested: list[str] = []

        def fake_doi(
            self: Works, doi: str, only_message: bool = True
        ) -> Optional[CrossrefMetadata]:
            requested.append(doi)
            return copy.deepcopy(metadata)

        monkeypatch.setattr(Works, "doi", fake_doi)
        return requested

    return install


@pytest.fixture
def fake_file_uploader(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[Mapping[str, Uploads]], None]:
    """ファイルアップロード欄を、指定のファイルを返す処理に差し替える関数を返す.

    返された関数 ``install(uploads)`` を呼ぶと ``streamlit.file_uploader``
    が差し替わる。``uploads`` は「ラベルの接頭辞 → 返すファイル」の
    対応で、ラベルが ``startswith`` で一致した最初の接頭辞の値を、
    ``accept_multiple_files`` の指定にかかわらず、そのまま返す。

    - ``FakeUpload``: 単体を返す (``accept_multiple_files=True`` でも単体の
      ままなので、「単一でも一覧で返る」の検証に使える)
    - ``list[FakeUpload]``: 一覧を返す
    - ``None``: ファイル未選択として ``None`` を返す

    どの接頭辞にも一致しないラベルは、元の ``streamlit.file_uploader`` に
    引数ごと渡す (本来の動作)。``install`` を再び呼ぶと、前の対応を
    置き換える。元の関数は、最初の ``install`` を呼んだ時点のものを使う。

    Returns
    -------
    Callable[[Mapping[str, Uploads]], None]
        ``install(uploads) -> None``。
    """
    originals: list[Callable[..., Any]] = []

    def install(uploads: Mapping[str, Uploads]) -> None:
        if not originals:
            originals.append(st.file_uploader)
        original = originals[0]
        table = dict(uploads)

        def fake_file_uploader(label: str, *args: Any, **kwargs: Any) -> Any:
            for prefix, files in table.items():
                if label.startswith(prefix):
                    return files
            return original(label, *args, **kwargs)

        monkeypatch.setattr(st, "file_uploader", fake_file_uploader)

    return install


class StMessages(NamedTuple):
    """``st_messages`` が記録した UI 通知の本文 (呼ばれた順)."""

    error: list[str]
    warning: list[str]
    success: list[str]


def _make_recorder(sink: list[str]) -> Callable[..., None]:
    """通知の本文を ``sink`` に記録するだけの関数を作る."""

    def record(body: Any, *args: Any, **kwargs: Any) -> None:
        sink.append(str(body))

    return record


@pytest.fixture
def st_messages(monkeypatch: pytest.MonkeyPatch) -> StMessages:
    """``st.error`` / ``st.warning`` / ``st.success`` を記録する処理に差し替える.

    差し替えた関数は、呼ばれた本文を文字列にして記録するだけで、
    画面には何も表示しない。bare mode (``streamlit run`` の外) では
    これらの関数は何もしないため、通知の内容を観測する手段になる。

    ``AppTest`` を使うテストでは併用しない。ページの要素が生成されなく
    なるため、``at.error`` などの要素が空になる。画面テストでは
    ``AppTest`` の要素を見る。

    Returns
    -------
    StMessages
        ``error``・``warning``・``success`` の各一覧。テストの後に
        差し替えは元へ戻る。
    """
    messages = StMessages(error=[], warning=[], success=[])
    for name in StMessages._fields:
        monkeypatch.setattr(st, name, _make_recorder(getattr(messages, name)))
    return messages


# --- PageRunner -----------------------------------------------------------
# 論文リストページ (_list.py) を起点に、対象のページへ移って AppTest で
# 実行する。サブページを AppTest.from_file で直接開くと、config_page の
# st.page_link が StreamlitPageNotFoundError になる (st.page_link は
# エントリポイントからの相対パスで解決されるため)。

APP_DIR = Path(paper_manager.__file__).parent / "app"
# 論文リストページ (None) と 4 つのサブページ。_list.py からの相対パス。
PAGES: tuple[Optional[str], ...] = (
    None,
    "pages/_register.py",
    "pages/_backup.py",
    "pages/_restore.py",
    "pages/_settings.py",
)
APP_TIMEOUT_SECONDS = 30.0


def run_page(page: Optional[str]) -> AppTest:
    """論文リストページを起点に ``page`` へ移り、1 回実行する.

    ``AppTest.from_file`` で論文リストページを開き、``page`` が ``None``
    でなければ ``switch_page`` で移ってから ``run`` する。

    Parameters
    ----------
    page : Optional[str]
        ``PAGES`` の要素。``None`` は論文リストページ。

    Returns
    -------
    AppTest
        1 回実行済みの ``AppTest``。呼び出し側はウィジェットを操作して
        ``run()`` を呼べる。例外が起きても送出されず、``at.exception`` に
        残る。
    """
    at = AppTest.from_file(
        str(APP_DIR / "_list.py"), default_timeout=APP_TIMEOUT_SECONDS
    )
    if page is not None:
        at.switch_page(page)
    return at.run()
