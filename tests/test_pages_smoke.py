"""全ページのスモークテスト (要件 11, 14.3).

5 ページ (リスト・編集、登録、バックアップ、復元、設定) を、データ領域が
空の状態と ``sample_data`` の状態のそれぞれで実行し (5 × 2 = 10 通り)、
次の 3 点を検証する。

- 例外なく描画される (要件 11.1, 11.2)
- 共通のタイトルと、5 つのページへのナビゲーションが表示される
  (要件 11.3)
- 操作をしなくても、論文リストのファイルに保存されている論文
  (キーとエントリ内容) が実行の前後で同一である (要件 11.4)

ページは ``conftest.run_page`` で実行する。実ブラウザも Streamlit サーバー
も起動しない ``AppTest`` による実行なので、要件 11.5 を満たす。ページの
例外は送出されず ``at.exception`` に残るので、テスト側で検証する。

論文リストページ (``_list.py``) の起動確認は、以前は ``tests/test_app.py``
の ``test_app`` が担っていた。この確認は、ここの ``list`` × ``empty`` の
ケースに包含される (要件 14.3)。pybtex の import 確認は
``tests/test_import.py`` に残っている。

「論文リストが変わらない」の意味
--------------------------------
上の 3 点の検証で比べるのは、``list.json`` に保存されている論文のキーと
エントリ内容 (``json.load`` した辞書) である。``list.json`` がないことは、
空の論文リストと同じ扱いにする。

ページの描画では、論文リストのファイルに書き込まない (known-bug-fixes
要件 10.2)。論文リストのファイルに書くのは、登録・更新・削除で呼ばれる
``save_paper_list`` だけである。そのため、データ領域が空の状態では、
実行後も ``list.json`` は作られない。このことは
``test_page_render_does_not_write_list_file`` で、ファイルの有無・バイト列・
更新時刻を実行の前後で比べて確かめる。対象は、``list.json`` がない状態、
正常だが ``PaperList.to_file`` とは異なる書式で書かれた状態 (書き戻されると
内容が同じでもバイト列が変わる)、JSON として壊れた状態 (途中で切れた
ファイル、空のファイル) のそれぞれで、5 ページすべてを実行する。

壊れた論文リスト (known-bug-fixes 要件 10.1〜10.3)
-----------------------------------------------------
JSON として解析できない、UTF-8 として読めない、論文の一覧の形をしていない
``list.json`` を置いて 5 ページを描画すると、例外なく描画され、壊れている旨の
警告が出る。ファイルは書き換えず、退避もしない (退避は保存の直前に行う)。
壊れた状態で登録ページから論文を登録すると、壊れた内容が退避先に残り、
退避先が画面に出る。退避先は通知 (session state の ``KEY_NOTICES``) にも残り、
次の描画で表示されてから消える。

論文リストページの詳細・エクスポート・編集
--------------------------------------------
(known-bug-fixes 要件 1.2, 1.4, 3.3, 13.3, 13.4)

論文一覧の行の選択は ``AppTest`` で操作できないので、``st.dataframe`` を
選択済みの結果を返す処理に差し替える (``select_rows``)。ダウンロードボタンに
渡された内容とファイル名は、``st.download_button`` を記録する処理に差し替えて
読む (``downloads``)。

- 詳細のビューアで PDF を読めなかったとき、失敗したことと表示用のパス、理由が
  出る。1 件だけの PDF の名前が番号でない (移せなかった旧形式の PDF など)
  ときも、ダウンロードの名前を作れる
- 複数選択の zip エクスポートで、読めない PDF は zip に入れずにエラーを出し、
  残りの PDF を zip に入れる
- 変換後のパスが別の空間へ写る 2 通りの状態 (``converted_space``) で、PDF と
  孤立 PDF の件数、ビューアとダウンロード、zip エクスポート (メンバー名に
  prefix が入らない)、読めない PDF のエラー (変換前のパスで表示) が同じ結果に
  なる
- 引用キーに ``,`` や空白を含む既存の論文でも、詳細の BibTeX 表示と ``.bib``・
  ``.xml`` のエクスポートが整えた引用キーで出力され、``list.json`` の ID は
  変わらない。まとめてのエクスポートでは、整えた引用キーが大文字小文字を
  区別せずに重複しない
- 編集ダイアログ (``edit_entry``) は、更新に成功したときだけ再描画する。
  失敗したときはエラーを表示したままにする。ダイアログは ``st.fragment``
  で、実画面ではフォームの送信が fragment だけを再実行するが、``AppTest`` は
  常にスクリプト全体を再実行する。そのため ``edit_entry`` を直接呼ぶ画面
  (``edit_screen``) を実行し、再描画 (``st.rerun``) の有無は、画面の実行
  回数の増え方で確かめる (再描画すると 1 回余分に実行される)
- 編集ダイアログで追加した PDF の保存に一部失敗しても更新は成功し、再描画
  すると画面のエラーは消える。そこで、失敗した保存の文 (画面のエラーと同じ
  表示用のパスと理由。変換後の prefix なし) を保留中の通知 (``KEY_NOTICES``)
  に積み、次の描画で ``config_page`` が警告として表示して消す (known-bug-fixes
  要件 1.4, 1.6)。失敗がなければ通知を増やさない。更新が拒否・失敗した
  ときは、通知を増やさず再描画もせず、エラーを表示したままにする。既存の
  通知は消さず、順序を保って追記する。``edit_screen`` に ``config_page`` の
  通知の表示 (``show_notices``) を足した画面で、再描画の後の表示まで確かめ、
  実際のページ (``run_page``) でも同じ通知が 1 回だけ表示されることを確かめる

論文リストページは PDF の本数を数えるとき、旧形式の PDF を新形式へ移行する
非推奨の内部処理 (``pdf_filename`` / ``_migrate_legacy_pdf_filenames``)
を通るため、``DeprecationWarning`` が出る。警告の確認が目的ではないので、
この 2 つの警告だけを無視する (ほかの警告は隠さない)。
"""

import errno
import io
import json
import logging
import shutil
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple, Optional

import pytest
import streamlit as st
from conftest import (
    APP_TIMEOUT_SECONDS,
    PAGES,
    PDF_BYTES,
    ConvertedSpace,
    DataPaths,
    FakeUpload,
    failing_open_on_call,
    run_page,
    write_pdf,
)
from pybtex.database.input import bibtex
from streamlit.runtime.pages_manager import PagesManager
from streamlit.testing.v1 import AppTest

from paper_manager.app.components import _common
from paper_manager.app.helper import KEY_NOTICES
from paper_manager.entry import Entry, PaperList
from paper_manager.entry._entry import KEY_PAPER_LIST
from paper_manager.helper import to_display_path, to_fs_path

pytestmark = pytest.mark.filterwarnings(
    "ignore:(pdf_filename|_migrate_legacy_pdf_filenames) is deprecated"
    ":DeprecationWarning"
)

# 共通のタイトル (st.title)。全ページに表示される。
COMMON_TITLE = "PAPER MANAGER"

# 5 ページへのナビゲーションのラベル (要件 11.1 のページ名)。
# サイドバーに、この順で表示される。
NAV_LABELS = ["リスト・編集", "登録", "バックアップ", "復元", "設定"]

# 各ページ (PAGES の要素) の、テスト ID と、そのページ自身の見出し
# (st.header) に含まれる語。共通のタイトルとナビゲーションは全ページで
# 同じなので、これらだけでは「対象のページを実行できたか」が分からない。
# 見出しを見て、ページの切り替えが効いていることを確かめる。
PAGE_IDENTITY: dict[Optional[str], tuple[str, str]] = {
    None: ("list", "論文リスト"),
    "pages/_register.py": ("register", "論文登録"),
    "pages/_backup.py": ("backup", "バックアップ"),
    "pages/_restore.py": ("restore", "復元"),
    "pages/_settings.py": ("settings", "設定"),
}

PaperDicts = dict[str, dict[str, Any]]


def stored_papers(list_file: Path) -> PaperDicts:
    """``list.json`` に保存されている論文を、キーとエントリ内容で返す.

    Parameters
    ----------
    list_file : Path
        論文リストのファイル。存在しなくてもよい。

    Returns
    -------
    PaperDicts
        ``json.load`` した辞書。ファイルがなければ空の辞書
        (空の論文リストと同じ扱い)。
    """
    if not list_file.is_file():
        return {}
    with open(list_file, mode="r", encoding="utf-8") as f:
        return json.load(f)


def describe_exceptions(at: AppTest) -> str:
    """``at.exception`` の内容を、失敗メッセージ用の文字列にする."""
    return "\n".join(
        f"{e.proto.type}: {e.value}\n{''.join(e.stack_trace)}"
        for e in at.exception
    )


@pytest.fixture(params=("empty", "sample"))
def seeded_papers(
    request: pytest.FixtureRequest, data_paths: DataPaths
) -> PaperDicts:
    """データ領域の状態を用意し、そこに保存した論文を返す.

    - ``empty``: データ領域が空のまま (``list.json`` などがない)。
      保存した論文は 0 件
    - ``sample``: ``sample_data`` (PDF あり・なしの論文と孤立 PDF を含む)。
      期待値は、``sample_data`` が作った論文リストから導く。実行したページ
      が書き出したファイルの内容は使わない

    Returns
    -------
    PaperDicts
        データ領域に保存した論文のキーとエントリ内容。
    """
    if request.param == "empty":
        assert list(data_paths.root.iterdir()) == []
        return {}
    sample = request.getfixturevalue("sample_data")
    return {key: dict(entry) for key, entry in sample.paper_list.items()}


@pytest.mark.parametrize(
    "page", list(PAGES), ids=[PAGE_IDENTITY[page][0] for page in PAGES]
)
def test_page_renders_with_common_layout_and_leaves_list_unchanged(
    page: Optional[str], seeded_papers: PaperDicts, data_paths: DataPaths
) -> None:
    """各ページが、例外なく共通の枠付きで描画され、論文リストを変えない."""
    # 前提: 用意したデータが、実際に list.json に保存されている
    assert stored_papers(data_paths.list_file) == seeded_papers

    at = run_page(page)

    # 11.1, 11.2: 例外なく描画される
    assert not at.exception, describe_exceptions(at)

    # 実行したページが、対象のページである
    expected_header = PAGE_IDENTITY[page][1]
    headers = [h.value for h in at.header]
    assert any(expected_header in header for header in headers), (
        f"page {page!r} did not render its own header "
        f"(expected one containing {expected_header!r}, got {headers})"
    )

    # 11.3: 共通のタイトル
    titles = [t.value for t in at.title]
    assert COMMON_TITLE in titles, (
        f"common title {COMMON_TITLE!r} is not shown (got {titles})"
    )

    # 11.3: 5 つのページへのナビゲーション (ラベルと、遷移先が互いに異なる)
    page_links = at.get("page_link")
    assert [link.proto.label for link in page_links] == NAV_LABELS
    targets = [link.proto.page for link in page_links]
    assert len(set(targets)) == len(NAV_LABELS), (
        f"navigation links do not lead to 5 distinct pages: {targets}"
    )

    # 11.4: 操作をしていないので、保存されている論文は実行前と同一
    assert stored_papers(data_paths.list_file) == seeded_papers


# --- 描画で論文リストのファイルに書き込まない (known-bug-fixes 要件 10.2) ---

# 論文リストのファイルの状態。
# - absent: ファイルがない (データ領域が空)
# - valid: 正常な論文リスト。``PaperList.to_file`` とは異なる書式
#   (区切りの空白なし、非 ASCII 文字のエスケープ) で書く
# - truncated: 正常な論文リストが途中で切れたもの (JSON として解析できない)
# - empty: 空のファイル (JSON として解析できない)
LIST_FILE_STATES = ("absent", "valid", "truncated", "empty")


class FileSnapshot(NamedTuple):
    """ファイルの有無・バイト列・更新時刻."""

    exists: bool
    content: Optional[bytes]
    mtime_ns: Optional[int]


def snapshot_file(path: Path) -> FileSnapshot:
    """``path`` の有無・バイト列・更新時刻を記録する.

    Parameters
    ----------
    path : Path
        記録するファイル。存在しなくてもよい。

    Returns
    -------
    FileSnapshot
        ファイルがなければ、``exists`` が ``False`` で、ほかは ``None``。
    """
    if not path.exists():
        return FileSnapshot(exists=False, content=None, mtime_ns=None)
    return FileSnapshot(
        exists=True,
        content=path.read_bytes(),
        mtime_ns=path.stat().st_mtime_ns,
    )


@pytest.fixture(params=LIST_FILE_STATES)
def list_file_state(
    request: pytest.FixtureRequest, data_paths: DataPaths
) -> str:
    """論文リストのファイルを ``LIST_FILE_STATES`` の状態にする.

    ``absent`` 以外は、``sample_data`` が保存した論文リストから作る。
    途中で切れたファイルは、文字の境界で切る (UTF-8 として読めないことは
    別の壊れ方なので、ここでは JSON として解析できないことだけを作る)。

    Returns
    -------
    str
        用意した状態の名前。
    """
    state: str = request.param
    list_file = data_paths.list_file
    if state == "absent":
        assert not list_file.exists()
        return state

    request.getfixturevalue("sample_data")
    canonical = list_file.read_text(encoding="utf-8")
    if state == "valid":
        content = json.dumps(json.loads(canonical), separators=(",", ":"))
        # 前提: 内容は同じで、書式だけが to_file の出力と異なる
        assert json.loads(content) == json.loads(canonical)
        assert content != canonical
    else:
        content = (
            canonical[: len(canonical) // 2] if state == "truncated" else ""
        )
        # 前提: JSON として解析できない
        with pytest.raises(json.JSONDecodeError):
            json.loads(content)
    list_file.write_text(content, encoding="utf-8")
    return state


@pytest.mark.parametrize(
    "page", list(PAGES), ids=[PAGE_IDENTITY[page][0] for page in PAGES]
)
def test_page_render_does_not_write_list_file(
    page: Optional[str], list_file_state: str, data_paths: DataPaths
) -> None:
    """ページを描画しただけでは、論文リストのファイルに書き込まない.

    壊れたファイルを空の論文リストで上書きせず、正常なファイルの書式を
    整え直さず、ないファイルを作らない (known-bug-fixes 要件 10.2)。
    """
    before = snapshot_file(data_paths.list_file)

    at = run_page(page)

    # 例外でページの実行が途中で止まると、描画の全体を確かめたことに
    # ならない。先に、例外がなく、対象のページが描画されたことを確かめる
    assert not at.exception, describe_exceptions(at)
    expected_header = PAGE_IDENTITY[page][1]
    headers = [h.value for h in at.header]
    assert any(expected_header in header for header in headers), (
        f"page {page!r} did not render its own header "
        f"(expected one containing {expected_header!r}, got {headers})"
    )

    assert snapshot_file(data_paths.list_file) == before, (
        f"rendering page {page!r} wrote list.json (state {list_file_state!r})"
    )


# --- 壊れた論文リスト (known-bug-fixes 要件 10.1, 10.2, 10.3) -----------------

# 壊れた論文リストを読み込んだときの警告 (修正前からの文言)
BROKEN_WARNING = "Broken paper list (json)"

# 壊れた ``list.json`` の内容. 解析できない・UTF-8 として読めない・論文の一覧の
# 形をしていない (トップレベルが辞書でない、値が辞書でない、値が文字列でない).
BROKEN_LIST_FILES = [
    pytest.param(b'{"AliceSmith2020_0": {"title": "A St', id="truncated-json"),
    pytest.param(b"", id="empty-file"),
    pytest.param(
        '{"a": {"title": "深層学習"}}'.encode("shift_jis"), id="shift-jis"
    ),
    pytest.param(b"[]", id="top-array"),
    pytest.param(b"null", id="top-null"),
    pytest.param(b'{"a": "str"}', id="entry-string"),
    pytest.param(b'{"a": {"title": 1}}', id="field-number"),
]

# 登録ページの BibTeX フォームの ID (st.form の第 1 引数)
FORM_BIB = "bib_form"
BIB_ARTICLE = """\
@article{Doe2020,
  author = {Doe, Jane},
  title = {A Study of Registering Papers},
  journal = {Journal of Registration},
  year = {2020},
}
"""
REGISTERED_KEY = "JaneDoe2020_0"


def assert_page_rendered(at: AppTest, page: Optional[str]) -> None:
    """ページが例外なく、そのページ自身の見出し付きで描画されたことを確かめる."""
    assert not at.exception, describe_exceptions(at)
    expected_header = PAGE_IDENTITY[page][1]
    headers = [h.value for h in at.header]
    assert any(expected_header in header for header in headers), (
        f"page {page!r} did not render its own header "
        f"(expected one containing {expected_header!r}, got {headers})"
    )


def backups_of(data_paths: DataPaths) -> list[Path]:
    """壊れた論文リストの退避先 (``list.json.broken-*``) を名前順に返す."""
    return sorted(data_paths.root.glob("list.json.broken-*"))


def warnings_mentioning(at: AppTest, path: Path) -> list[str]:
    """画面の警告のうち、``path`` を含むものを返す."""
    return [w.value for w in at.warning if str(path) in w.value]


def widget_in_form(widgets: Any, form_id: str) -> Any:
    """``widgets`` のうち、フォーム ``form_id`` に属する 1 つを返す."""
    found = [w for w in widgets if w.proto.form_id == form_id]
    assert len(found) == 1, f"expected one widget in {form_id!r}: {found}"
    return found[0]


@pytest.mark.parametrize("broken", BROKEN_LIST_FILES)
@pytest.mark.parametrize(
    "page", list(PAGES), ids=[PAGE_IDENTITY[page][0] for page in PAGES]
)
def test_page_with_a_broken_list_warns_and_leaves_the_file_alone(
    page: Optional[str], broken: bytes, data_paths: DataPaths
) -> None:
    """壊れた論文リストでも、各ページは警告を出して落ちず、ファイルを変えない.

    バイト列と更新時刻が変わらず、退避先も作られない (退避は、保存の直前に
    行う。描画では書かない)。
    """
    data_paths.list_file.write_bytes(broken)
    before = snapshot_file(data_paths.list_file)

    at = run_page(page)

    assert_page_rendered(at, page)
    assert BROKEN_WARNING in [w.value for w in at.warning]
    assert snapshot_file(data_paths.list_file) == before
    assert backups_of(data_paths) == []


def test_registering_with_a_broken_list_backs_it_up_and_notices_it_once(
    data_paths: DataPaths,
) -> None:
    """壊れた状態で論文を登録すると、退避ファイルができて退避先が出る.

    - 退避先に、壊れた内容がそのまま残る
    - ``list.json`` は、登録した論文だけの新しい内容になる
    - 登録した描画で、退避先が警告として出る
    - 退避先は通知に残り、次の描画で表示されてから消える
    """
    broken = b'{"a": {"title": 1}}'
    data_paths.list_file.write_bytes(broken)
    at = run_page("pages/_register.py")
    assert_page_rendered(at, "pages/_register.py")
    widget_in_form(at.text_area, FORM_BIB).set_value(BIB_ARTICLE)

    widget_in_form(at.button, FORM_BIB).click()
    at = at.run()

    assert not at.exception, describe_exceptions(at)
    assert [e.value for e in at.error] == []
    assert len(at.success) == 1
    (backup,) = backups_of(data_paths)
    assert backup.read_bytes() == broken
    assert list(stored_papers(data_paths.list_file)) == [REGISTERED_KEY]
    assert len(warnings_mentioning(at, backup)) == 1

    # 次の描画で、通知として表示される
    at = at.run()
    assert not at.exception, describe_exceptions(at)
    assert len(warnings_mentioning(at, backup)) == 1
    assert KEY_NOTICES not in at.session_state

    # 表示したら消えている
    at = at.run()
    assert not at.exception, describe_exceptions(at)
    assert warnings_mentioning(at, backup) == []
    assert backups_of(data_paths) == [backup]


@pytest.mark.parametrize(
    "page", list(PAGES), ids=[PAGE_IDENTITY[page][0] for page in PAGES]
)
def test_pending_notices_are_shown_before_the_page_and_then_cleared(
    page: Optional[str],
) -> None:
    """保留中の通知は、次の描画の冒頭で表示され、表示したら消える.

    通知がなければ、通知の警告は出ない (ページ自身の警告だけがある)。
    通知はページの見出しより前に出る。
    """
    at = run_page(page)
    assert_page_rendered(at, page)
    own_warnings = [w.value for w in at.warning]
    assert "NOTICE-FIRST" not in own_warnings

    at.session_state[KEY_NOTICES] = ["NOTICE-FIRST", "NOTICE-SECOND"]
    at = at.run()

    assert_page_rendered(at, page)
    assert [w.value for w in at.warning] == [
        "NOTICE-FIRST",
        "NOTICE-SECOND",
        *own_warnings,
    ]
    assert KEY_NOTICES not in at.session_state
    order = [
        (index, element)
        for index, element in sorted(at.main.children.items())
        if element.type in ("warning", "header")
    ]
    assert [element.type for _, element in order][:3] == [
        "warning",
        "warning",
        "header",
    ]

    at = at.run()

    assert_page_rendered(at, page)
    assert [w.value for w in at.warning] == own_warnings


# --- 論文リストページ: 詳細・エクスポート・編集 ----------------------------
# (known-bug-fixes 要件 1.2, 1.4, 3.3, 13.3, 13.4)

# bib2xml が出力する XML の名前空間 (引用キーは ``b:Tag`` に入る)
XML_NAMESPACE = "http://schemas.microsoft.com/office/word/2004/10/bibliography"


class Download(NamedTuple):
    """``st.download_button`` に渡された、ボタンの文言・内容・ファイル名."""

    label: str
    data: Any
    file_name: Optional[str]


@pytest.fixture
def select_rows(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """論文一覧の行を選択した状態にする関数を返す.

    返された関数 ``select(*rows)`` を呼ぶと ``st.dataframe`` が差し替わり、
    ``rows`` (論文リストの順の位置) が選択された結果を返す。呼ぶまでは何も
    変わらない。差し替えはテストの後に元へ戻る。
    """

    def select(*rows: int) -> None:
        def dataframe(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"selection": {"rows": list(rows), "columns": []}}

        monkeypatch.setattr(st, "dataframe", dataframe)

    return select


@pytest.fixture
def downloads(monkeypatch: pytest.MonkeyPatch) -> list[Download]:
    """``st.download_button`` に渡された内容を、呼ばれた順に記録する.

    記録したうえで、本来のボタンも表示する。画面の再実行のたびに増えるので、
    操作の前に ``clear()`` して使う。
    """
    calls: list[Download] = []
    original = st.download_button

    def download_button(
        label: str,
        data: Any = None,
        file_name: Optional[str] = None,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        calls.append(Download(label, data, file_name))
        return original(label, data, file_name, *args, **kwargs)

    monkeypatch.setattr(st, "download_button", download_button)
    return calls


def store_papers(data_paths: DataPaths, *entries: Entry) -> list[str]:
    """``entries`` を ``list.json`` に保存して、論文リストのキーを返す.

    ``ID`` を持つエントリはその ``ID`` をキーにする (引用キーに読めない文字
    を含む既存の論文を作るため)。持たないエントリには、登録と同じ規則で
    ``ID`` を付ける。
    """
    paper_list = PaperList({})
    for entry in entries:
        if "ID" not in entry:
            entry["ID"] = entry.get_key(paper_list.keys())
        paper_list[entry["ID"]] = entry
    paper_list.to_file(data_paths.list_file)
    return list(paper_list.keys())


def error_messages(at: AppTest) -> list[str]:
    """画面のエラーの本文を、表示された順に返す."""
    return [e.value for e in at.error]


def fail_rename_of(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """名前が ``name`` のパスの ``Path.rename`` だけが ``OSError`` で失敗する.

    PDF の名前を ``NN.pdf`` に改名する移行が失敗して、元の名前のまま一覧に
    残る状況を作る。ほかのパスは本来の ``rename`` に通す。
    """
    original = Path.rename

    def rename(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name == name:
            raise PermissionError(
                errno.EACCES, "rename denied (simulated)", str(self)
            )
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "rename", rename)


def unreadable_pdf(pdf_dir: Path, name: str) -> tuple[Path, str]:
    """``pdf_dir/name`` を、PDF として一覧に出るが、開けないものにする.

    PDF という名前のフォルダを作る (``*.pdf`` の列挙には入るが、ファイルとして
    開くと ``OSError`` になる)。権限の変更は環境 (root など) によって効かない
    ので使わない。

    Returns
    -------
    tuple[Path, str]
        作ったパスと、開こうとしたときの ``OSError`` の理由 (``strerror``)。
    """
    path = pdf_dir / name
    path.mkdir(parents=True)
    with pytest.raises(OSError) as excinfo:
        path.read_bytes()
    reason = excinfo.value.strerror
    assert reason  # 前提: 画面に出せる理由がある
    return path, reason


# --- ビューアの読み込みの失敗 (要件 1.2, 1.4) ---


def logged_errors(caplog: pytest.LogCaptureFixture, path: Path) -> list[str]:
    """ログに出た ERROR のうち、表示用の ``path`` を含むものの本文を返す."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.ERROR
        and to_display_path(path) in record.getMessage()
    ]


def check_viewer_reports_unreadable_pdf(
    data_paths: DataPaths,
    make_entry: Callable[..., Entry],
    select_rows: Callable[..., None],
    caplog: pytest.LogCaptureFixture,
) -> Path:
    """ビューアで PDF を読めなかったときの画面を確かめる.

    例外で画面を落とさず、失敗と表示用のパス、理由を出し、ビューアもダウン
    ロードボタンも出さない。読める PDF を選び直せば、また表示される。

    Returns
    -------
    Path
        読めなかった PDF のパス (エラーに変換後の prefix が出ないことを、
        呼び出し側が確かめる)。
    """
    entry = make_entry()
    store_papers(data_paths, entry)
    pdf_dir = entry.get_pdf_dir(data_paths.pdf)
    write_pdf(pdf_dir / "00.pdf")
    unreadable, reason = unreadable_pdf(pdf_dir, "01.pdf")
    select_rows(0)

    at = run_page(None)

    # 読める PDF (既定の選択) は、ビューアとダウンロードで開く
    assert not at.exception, describe_exceptions(at)
    assert error_messages(at) == []
    assert len(at.get("component_instance")) == 1
    assert len(at.get("download_button")) == 1

    at.selectbox(key="pdf_selector").select("01.pdf").run()

    assert not at.exception, describe_exceptions(at)
    (message,) = error_messages(at)
    assert to_display_path(unreadable) in message
    if str(unreadable) != to_display_path(unreadable):  # 変換後のパス
        assert str(unreadable) not in message
    assert reason in message
    assert len(at.get("component_instance")) == 0
    assert len(at.get("download_button")) == 0
    assert "Failed" in message  # 何をしようとして失敗したか
    assert reason in " ".join(logged_errors(caplog, unreadable))  # ログにも

    at.selectbox(key="pdf_selector").select("00.pdf").run()

    assert not at.exception, describe_exceptions(at)
    assert error_messages(at) == []
    assert len(at.get("component_instance")) == 1
    return unreadable


def test_the_viewer_reports_a_pdf_it_cannot_read(
    data_paths: DataPaths,
    make_entry: Callable[..., Entry],
    select_rows: Callable[..., None],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ビューアで PDF を読めなかったら、失敗と表示用のパス、理由を出す."""
    check_viewer_reports_unreadable_pdf(
        data_paths, make_entry, select_rows, caplog
    )


@pytest.mark.usefixtures("windows_like_paths")
def test_the_viewer_error_has_no_converted_path_prefix(
    data_paths: DataPaths,
    make_entry: Callable[..., Entry],
    select_rows: Callable[..., None],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """変換後のパスの PDF を読めなかったときも、prefix (``//``) を画面に出さない."""
    unreadable = check_viewer_reports_unreadable_pdf(
        data_paths, make_entry, select_rows, caplog
    )

    assert str(unreadable).startswith("//")  # 前提: 別名が効く
    assert to_display_path(unreadable) == str(unreadable)[1:]


# --- 1 件だけの PDF のダウンロード名 (要件 1.2) ---


@pytest.mark.parametrize("name", ["00.pdf", "07.pdf"])
def test_the_download_name_of_a_single_numbered_pdf_has_the_folder_name(
    name: str,
    data_paths: DataPaths,
    make_entry: Callable[..., Entry],
    select_rows: Callable[..., None],
    downloads: list[Download],
) -> None:
    """1 件だけの ``NN.pdf`` は、``<フォルダ名>_NN.pdf`` でダウンロードする."""
    entry = make_entry()
    store_papers(data_paths, entry)
    write_pdf(entry.get_pdf_dir(data_paths.pdf) / name)
    select_rows(0)

    at = run_page(None)

    assert not at.exception, describe_exceptions(at)
    assert downloads[-1].file_name == f"{entry.pdf_dir_name}_{name}"
    assert downloads[-1].data == PDF_BYTES


@pytest.mark.parametrize("layout", ["left-behind-legacy-pdf", "unrenamed"])
def test_a_single_pdf_without_a_number_can_be_downloaded(
    layout: str,
    data_paths: DataPaths,
    make_entry: Callable[..., Entry],
    select_rows: Callable[..., None],
    downloads: list[Download],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """番号でない名前の 1 件だけの PDF でも、ビューアを開きダウンロードできる.

    一覧が番号でない名前の PDF を返すのは、移行に失敗したときだけである。

    - ``left-behind-legacy-pdf``: フォルダと同名のファイルがあるため、旧形式
      の PDF をフォルダへ移せず、一覧に残る (``<年> - <著者> - <題名>.pdf``)
    - ``unrenamed``: フォルダにある番号でない名前の PDF を、改名できずに
      そのまま一覧に残る

    ダウンロードの名前は、その PDF の名前になる。
    """
    entry = make_entry()
    store_papers(data_paths, entry)
    if layout == "left-behind-legacy-pdf":
        name = entry.pdf_filename
        write_pdf(data_paths.pdf / name)
        (data_paths.pdf / entry.pdf_dir_name).write_bytes(b"not a folder")
    else:
        name = "my notes.pdf"
        write_pdf(entry.get_pdf_dir(data_paths.pdf) / name)
        fail_rename_of(monkeypatch, name)
    select_rows(0)

    at = run_page(None)

    assert not at.exception, describe_exceptions(at)
    assert error_messages(at) == []
    assert at.selectbox(key="pdf_selector").options == [name]
    assert len(at.get("component_instance")) == 1
    assert downloads[-1].file_name == name
    assert downloads[-1].data == PDF_BYTES


# --- 複数選択の zip エクスポート (要件 1.2, 1.4) ---


def two_entries(make_entry: Callable[..., Entry]) -> tuple[Entry, Entry]:
    """PDF フォルダ名が異なる 2 つのエントリ."""
    return (
        make_entry(),
        make_entry(author="Bob Jones", title="Quantum Widgets", year="2019"),
    )


def test_the_zip_export_skips_a_pdf_it_cannot_read_and_goes_on(
    data_paths: DataPaths,
    make_entry: Callable[..., Entry],
    select_rows: Callable[..., None],
    downloads: list[Download],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """読めない PDF は zip に入れずにエラーを出し、残りの PDF を zip に入れる.

    エラーには、何をしようとして失敗したかと表示用のパス、理由を出す。
    zip の内容は、読めた PDF だけで、壊れた項目を含まない。
    """
    first, second = two_entries(make_entry)
    store_papers(data_paths, first, second)
    first_dir = first.get_pdf_dir(data_paths.pdf)
    contents = {
        f"{first.pdf_dir_name}/00.pdf": PDF_BYTES + b"%first 00\n",
        f"{second.pdf_dir_name}/00.pdf": PDF_BYTES + b"%second 00\n",
    }
    write_pdf(first_dir / "00.pdf", contents[f"{first.pdf_dir_name}/00.pdf"])
    unreadable, reason = unreadable_pdf(first_dir, "01.pdf")
    write_pdf(
        second.get_pdf_dir(data_paths.pdf) / "00.pdf",
        contents[f"{second.pdf_dir_name}/00.pdf"],
    )
    select_rows(0, 1)

    at = run_page(None)

    assert not at.exception, describe_exceptions(at)
    (message,) = error_messages(at)
    assert to_display_path(unreadable) in message
    assert reason in message
    assert reason in " ".join(logged_errors(caplog, unreadable))  # ログにも
    (archive,) = [d for d in downloads if (d.file_name or "").endswith(".zip")]
    with zipfile.ZipFile(io.BytesIO(archive.data)) as zip_file:
        assert zip_file.testzip() is None
        assert zip_file.namelist() == list(contents)
        assert {n: zip_file.read(n) for n in zip_file.namelist()} == contents


def test_the_zip_export_has_no_download_when_no_pdf_can_be_read(
    data_paths: DataPaths,
    make_entry: Callable[..., Entry],
    select_rows: Callable[..., None],
    downloads: list[Download],
) -> None:
    """どの PDF も読めなければ、読めなかった PDF ごとにエラーを出し、zip は出さない."""
    first, second = two_entries(make_entry)
    store_papers(data_paths, first, second)
    unreadable = [
        unreadable_pdf(entry.get_pdf_dir(data_paths.pdf), "00.pdf")[0]
        for entry in (first, second)
    ]
    select_rows(0, 1)

    at = run_page(None)

    assert not at.exception, describe_exceptions(at)
    messages = error_messages(at)
    assert len(messages) == 2
    for path, message in zip(unreadable, messages):
        assert to_display_path(path) in message
    assert downloads == []
    assert len(at.get("download_button")) == 0


@pytest.mark.usefixtures("windows_like_paths")
def test_the_zip_export_works_with_converted_paths(
    data_paths: DataPaths,
    make_entry: Callable[..., Entry],
    select_rows: Callable[..., None],
    downloads: list[Download],
) -> None:
    """変換後のパスの PDF も zip に入り、失敗のエラーに prefix を出さない."""
    first, second = two_entries(make_entry)
    store_papers(data_paths, first, second)
    first_dir = first.get_pdf_dir(data_paths.pdf)
    assert str(first_dir).startswith("//")  # 前提: 別名が効く
    write_pdf(first_dir / "00.pdf")
    unreadable, _ = unreadable_pdf(first_dir, "01.pdf")
    write_pdf(second.get_pdf_dir(data_paths.pdf) / "00.pdf")
    select_rows(0, 1)

    at = run_page(None)

    assert not at.exception, describe_exceptions(at)
    (message,) = error_messages(at)
    assert to_display_path(unreadable) in message
    assert str(unreadable) not in message
    (archive,) = [d for d in downloads if (d.file_name or "").endswith(".zip")]
    with zipfile.ZipFile(io.BytesIO(archive.data)) as zip_file:
        assert zip_file.namelist() == [
            f"{first.pdf_dir_name}/00.pdf",
            f"{second.pdf_dir_name}/00.pdf",
        ]


# --- 変換後のパスの空間での、ビューア・ダウンロード・zip エクスポート ---
# (要件 1.2, 12.3)
# 論文リストページが PDF の件数・孤立 PDF の件数・ビューア・ダウンロード・
# zip エクスポートに使うパスは、すべて変換後の空間にある。``converted_space``
# の 2 通りの空間 (``windows-like`` は変換後のパスが別の形、``redirected`` は
# 別のディレクトリを指す) で、同じ結果になることを確かめる。PDF は変換後の
# 空間の実体 (``converted_space.pdf``) に置く。

# 論文に紐づかない PDF のフォルダ名 (孤立 PDF の件数の確認用)
UNLINKED_DIR_NAME = "2001 - Nobody - Unregistered Paper"


def metric_value(at: AppTest, label_part: str) -> str:
    """ラベルに ``label_part`` を含む指標 (``st.metric``) の値を返す."""
    [metric] = [m for m in at.metric if label_part in m.label]
    return metric.value


def test_the_viewer_and_the_download_work_in_the_converted_space(
    converted_space: ConvertedSpace,
    data_paths: DataPaths,
    make_entry: Callable[..., Entry],
    select_rows: Callable[..., None],
    downloads: list[Download],
) -> None:
    """ビューアとダウンロードと件数の表示が、変換後の空間の PDF で動く.

    1 件だけの PDF はビューアで開き、``<フォルダ名>_00.pdf`` の名前で、PDF
    の中身をダウンロードできる。PDF の件数は論文に紐づく 1 件、孤立 PDF の
    件数は紐づかない 1 件で、エラーは出ない。
    """
    entry = make_entry()
    store_papers(data_paths, entry)
    content = PDF_BYTES + b"%linked in the converted space\n"
    write_pdf(converted_space.pdf / entry.pdf_dir_name / "00.pdf", content)
    write_pdf(converted_space.pdf / UNLINKED_DIR_NAME / "00.pdf")
    select_rows(0)

    at = run_page(None)

    assert not at.exception, describe_exceptions(at)
    assert error_messages(at) == []
    assert metric_value(at, "PDFファイル数") == "1"
    assert metric_value(at, "孤立PDF") == "1"
    assert len(at.get("component_instance")) == 1
    assert downloads[-1].file_name == f"{entry.pdf_dir_name}_00.pdf"
    assert downloads[-1].data == content


def test_the_viewer_error_shows_the_plain_path_in_the_converted_space(
    converted_space: ConvertedSpace,
    data_paths: DataPaths,
    make_entry: Callable[..., Entry],
    select_rows: Callable[..., None],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """読めない PDF のエラーは、変換後の空間でも変換前のパスを出す.

    PDF は変換後の空間の実体にある。エラーと ERROR のログには、変換前の
    パス (表示用のパス) だけを出し、変換後のパスは出さない。
    """
    unreadable = check_viewer_reports_unreadable_pdf(
        data_paths, make_entry, select_rows, caplog
    )

    assert str(unreadable) != to_display_path(unreadable)  # 前提
    fs_pdf = to_fs_path(data_paths.pdf)
    assert unreadable.is_relative_to(fs_pdf)
    # 変換後の空間の実体にある (変換前のパスの下ではない)
    assert (converted_space.pdf / unreadable.relative_to(fs_pdf)).exists()
    assert to_display_path(unreadable).startswith(str(data_paths.pdf))


def test_the_zip_export_has_no_prefix_in_the_converted_space(
    converted_space: ConvertedSpace,
    data_paths: DataPaths,
    make_entry: Callable[..., Entry],
    select_rows: Callable[..., None],
    downloads: list[Download],
) -> None:
    """複数選択の zip エクスポートは、PDF をすべて入れ、prefix を含まない.

    メンバー名は ``<フォルダ名>/<PDF の名前>`` だけで、変換後のパスの形
    (別名の ``//``、データディレクトリまでの絶対パス) を含まない。中身は
    置いたとおりで、エラーは出ない。
    """
    first, second = two_entries(make_entry)
    store_papers(data_paths, first, second)
    contents = {
        f"{first.pdf_dir_name}/00.pdf": PDF_BYTES + b"%first 00\n",
        f"{first.pdf_dir_name}/01.pdf": PDF_BYTES + b"%first 01\n",
        f"{second.pdf_dir_name}/00.pdf": PDF_BYTES + b"%second 00\n",
    }
    for name, content in contents.items():
        write_pdf(converted_space.pdf / name, content)
    select_rows(0, 1)

    at = run_page(None)

    assert not at.exception, describe_exceptions(at)
    assert error_messages(at) == []
    assert metric_value(at, "PDFファイル数") == "3"
    (archive,) = [d for d in downloads if (d.file_name or "").endswith(".zip")]
    with zipfile.ZipFile(io.BytesIO(archive.data)) as zip_file:
        assert zip_file.testzip() is None
        assert zip_file.namelist() == list(contents)
        assert {n: zip_file.read(n) for n in zip_file.namelist()} == contents
        for name in zip_file.namelist():
            assert not name.startswith("/"), name
            assert "//" not in name, name


# --- 引用キー (要件 13.3, 13.4) ---

# (論文リストに保存されている ID, 出力される引用キー). 出力は、空白・``,``・
# ``{``・``}`` を除いたもの。``\``・``"``・``'`` は残る (XML でも落ちない)
CITATION_KEYS = [
    pytest.param(
        "King,Jr.,MartinLuther2020_0",
        "KingJr.MartinLuther2020_0",
        id="suffix-author",
    ),
    pytest.param(
        "Alice Smith, Bob Jones2020_0",
        "AliceSmithBobJones2020_0",
        id="comma-separated-authors",
    ),
    pytest.param("Ab{c}d 2020_0", "Abcd2020_0", id="braces-and-space"),
    pytest.param(
        "Doe\"Jane's\\x, y2020_0",
        "Doe\"Jane's\\xy2020_0",
        id="quote-and-backslash",
    ),
]


def xml_tags(xml_data: bytes) -> list[str]:
    """``bib2xml`` の XML から、各項目の引用キー (``b:Tag``) を返す."""
    root = ET.fromstring(xml_data)
    return [tag.text or "" for tag in root.iter(f"{{{XML_NAMESPACE}}}Tag")]


def assert_ids_in_session(at: AppTest, stored_ids: list[str]) -> None:
    """画面が持つ論文リストの ``ID`` が、保存されたまま (複製を整えただけ) か."""
    in_memory = at.session_state[KEY_PAPER_LIST]
    assert list(in_memory) == stored_ids
    assert [entry["ID"] for entry in in_memory.values()] == stored_ids


@pytest.mark.parametrize("stored_id, expected_key", CITATION_KEYS)
def test_detail_and_export_use_a_readable_citation_key(
    stored_id: str,
    expected_key: str,
    data_paths: DataPaths,
    make_entry: Callable[..., Entry],
    select_rows: Callable[..., None],
    downloads: list[Download],
) -> None:
    """読めない文字を含む ID の論文でも、詳細とエクスポートが落ちない.

    BibTeX の表示・``.bib``・``.xml`` のすべてが、整えた引用キーで出力される。
    ``list.json`` の ID は、描画しても書き換わらない。
    """
    entry = make_entry(author="King, Jr., Martin Luther", ID=stored_id)
    store_papers(data_paths, entry)
    before = data_paths.list_file.read_bytes()
    select_rows(0)

    at = run_page(None)

    # 詳細: 引用文の表示と、BibTeX のダウンロード・プレビュー (.bib)
    assert not at.exception, describe_exceptions(at)
    assert len(at.get("html")) == 1
    (bib,) = [d for d in downloads if (d.file_name or "").endswith(".bib")]
    assert bib.data.startswith(f"@article{{{expected_key},\n")
    assert bib.data.strip() in [code.value for code in at.code]
    bibdata = bibtex.Parser().parse_string(bib.data)
    assert list(bibdata.entries) == [expected_key]
    author = bibdata.entries[expected_key].persons["author"][0]
    assert author.last_names == ["King"]
    assert author.lineage_names == ["Jr."]

    # エクスポート (.xml)
    downloads.clear()
    at.radio(key="ext").set_value("xml").run()

    assert not at.exception, describe_exceptions(at)
    (xml_download,) = downloads
    assert (xml_download.file_name or "").endswith(".xml")
    assert xml_tags(xml_download.data) == [expected_key]

    # 保存された ID は変わらない (ファイルも、画面が持つ論文リストも)
    assert data_paths.list_file.read_bytes() == before
    stored = stored_papers(data_paths.list_file)
    assert list(stored) == [stored_id]
    assert stored[stored_id]["ID"] == stored_id
    assert_ids_in_session(at, [stored_id])


def test_exporting_several_papers_gives_every_citation_key_once(
    data_paths: DataPaths,
    make_entry: Callable[..., Entry],
    select_rows: Callable[..., None],
    downloads: list[Download],
) -> None:
    """まとめてのエクスポートでは、整えた引用キーが重複しない.

    大文字小文字だけが違う ID と、整えると同じになる ID は、後のものに連番を
    付ける。重複のままだと pybtex が例外を出す。どの項目にどのキーが付くか
    も確かめる。``list.json`` の ID は変わらない。
    """
    stored_ids = [
        "Smith2020_0",
        "smith2020_0",
        "SMITH2020_0",
        "Doe,Jane2020_0",
    ]
    expected_keys = [
        "Smith2020_0",
        "smith2020_0_1",
        "SMITH2020_0_2",
        "DoeJane2020_0",
    ]
    entries = [
        make_entry(title=f"Paper {index}", ID=stored_id)
        for index, stored_id in enumerate(stored_ids)
    ]
    store_papers(data_paths, *entries)
    before = data_paths.list_file.read_bytes()
    select_rows(*range(len(entries)))

    at = run_page(None)

    assert not at.exception, describe_exceptions(at)
    # 出力の順序は、BibTeX の書き出しが引用キーで並べ替える
    (bib,) = [d for d in downloads if (d.file_name or "").endswith(".bib")]
    bibdata = bibtex.Parser().parse_string(bib.data)
    assert sorted(bibdata.entries) == sorted(expected_keys)
    assert {
        key: bibdata.entries[key].fields["title"] for key in bibdata.entries
    } == {key: f"Paper {n}" for n, key in enumerate(expected_keys)}

    downloads.clear()
    at.radio(key="ext_multi").set_value("xml").run()

    assert not at.exception, describe_exceptions(at)
    (xml_download,) = downloads
    assert sorted(xml_tags(xml_download.data)) == sorted(expected_keys)
    assert data_paths.list_file.read_bytes() == before
    assert_ids_in_session(at, stored_ids)


# --- 編集ダイアログ (要件 3.3) ---


def edit_screen(key: str, show_notices: bool = False) -> None:
    """論文 ``key`` の編集ダイアログ (``edit_entry``) を出す画面 (AppTest 用).

    実行のたびに session state の ``runs`` を増やす。再描画 (``st.rerun``)
    すると、1 回の操作で 2 回実行される。論文リストページと同じく、論文
    リストに ``key`` がなければダイアログを出さない (更新で ID が変わると、
    再描画後にダイアログは閉じる)。

    ``show_notices`` が真なら、``config_page`` と同じく、本体の前に保留中の
    通知を表示して消す (``config_page`` が呼ぶ関数をそのまま使う)。偽なら
    表示しないので、通知が session state に残る。
    """
    import streamlit as st

    from paper_manager.app._list import edit_entry
    from paper_manager.app.helper._config import _show_pending_notices
    from paper_manager.entry import PaperList

    st.session_state["runs"] = st.session_state.get("runs", 0) + 1
    if show_notices:
        _show_pending_notices()
    if not PaperList.from_session_state():
        PaperList.from_file().to_session_state()
    if key in PaperList.from_session_state():
        edit_entry(key)


def open_edit_dialog(key: str, show_notices: bool = False) -> AppTest:
    """論文 ``key`` の編集ダイアログを 1 回実行する."""
    at = AppTest.from_function(
        edit_screen,
        default_timeout=APP_TIMEOUT_SECONDS,
        args=(key, show_notices),
    ).run()
    assert not at.exception, describe_exceptions(at)
    assert at.session_state["runs"] == 1
    return at


def submit_edit(at: AppTest, button: str = "done", **fields: str) -> AppTest:
    """``fields`` を入力して、編集ダイアログの ``button`` を押す."""
    for field, value in fields.items():
        at.text_input(key=field).input(value)
    at.button(key=button).click()
    return at.run()


def test_the_edit_dialog_keeps_a_rejection_and_closes_after_a_fix(
    data_paths: DataPaths, make_entry: Callable[..., Entry]
) -> None:
    """重複で拒否されたら、エラーを表示したままにし、直して保存し直せる.

    他の論文と同じ PDF フォルダ名にする更新は拒否される (論文リストは
    変わらない)。拒否では再描画しないので、エラーとフォームが残る。直して
    保存し直すと、更新して再描画する (1 回余分に実行される)。
    """
    first = make_entry(title="Alpha Paper")
    second = make_entry(title="Beta Paper")
    _, second_key = store_papers(data_paths, first, second)
    before = data_paths.list_file.read_bytes()
    at = open_edit_dialog(second_key)

    at = submit_edit(at, title="Alpha Paper")

    assert not at.exception, describe_exceptions(at)
    (message,) = error_messages(at)
    assert message.startswith("FAIL: Duplicated pdf_dir_name: ")
    assert at.session_state["runs"] == 2  # 再描画していない
    assert at.text_input(key="title").value == "Alpha Paper"  # フォームも残る
    assert data_paths.list_file.read_bytes() == before

    at = submit_edit(at, title="Gamma Paper")

    assert not at.exception, describe_exceptions(at)
    assert error_messages(at) == []
    assert at.session_state["runs"] == 4  # 更新に成功して再描画した
    titles = [e["title"] for e in stored_papers(data_paths.list_file).values()]
    assert sorted(titles) == ["Alpha Paper", "Gamma Paper"]


def test_the_edit_dialog_keeps_the_error_for_a_missing_required_field(
    data_paths: DataPaths, make_entry: Callable[..., Entry]
) -> None:
    """必須項目が空のときは、更新を呼ばず、エラーを表示したままにする."""
    (key,) = store_papers(data_paths, make_entry())
    before = data_paths.list_file.read_bytes()
    at = open_edit_dialog(key)

    at = submit_edit(at, title="")

    assert not at.exception, describe_exceptions(at)
    assert error_messages(at) == ["('title') is/are necessary."]
    assert at.session_state["runs"] == 2
    assert data_paths.list_file.read_bytes() == before


def test_the_edit_dialog_keeps_the_error_when_saving_fails(
    data_paths: DataPaths,
    make_entry: Callable[..., Entry],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保存に失敗した更新は、エラーを表示したままにし、論文リストを変えない.

    ダイアログを開いた後に ``list.json`` が壊れ、退避も失敗すると、保存
    できない (``update_entry_in_list`` が ``False`` を返す)。保存していないのに
    再描画してエラーを消さない。
    """
    (key,) = store_papers(data_paths, make_entry())
    at = open_edit_dialog(key)
    broken = b'{"a": {"title": 1}}'
    data_paths.list_file.write_bytes(broken)

    def copy2(*args: Any, **kwargs: Any) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(shutil, "copy2", copy2)

    at = submit_edit(at, title="Changed Title")

    assert not at.exception, describe_exceptions(at)
    (message,) = error_messages(at)
    assert "保存しませんでした" in message
    assert "No space left on device" in message
    assert at.session_state["runs"] == 2
    assert data_paths.list_file.read_bytes() == broken


def test_the_edit_dialog_redraws_when_it_is_cancelled(
    data_paths: DataPaths, make_entry: Callable[..., Entry]
) -> None:
    """キャンセルでは、何も保存せずに再描画してダイアログを閉じる."""
    (key,) = store_papers(data_paths, make_entry())
    before = data_paths.list_file.read_bytes()
    at = open_edit_dialog(key)

    at = submit_edit(at, button="cancel", title="Ignored Title")

    assert not at.exception, describe_exceptions(at)
    assert error_messages(at) == []
    assert at.session_state["runs"] == 3
    assert data_paths.list_file.read_bytes() == before


# --- 編集ダイアログ: 追加した PDF の保存の失敗 (要件 1.4, 1.6) ---
# 更新に成功すると ``st.rerun()`` で画面のエラーが消えるので、PDF の保存の
# 失敗は、再描画の前に保留中の通知 (``KEY_NOTICES``) へ積む。通知は次の描画で
# ``config_page`` が警告として表示して消す。PDF の書き込みは、``_common`` の
# ``open`` を差し替えて失敗させる (権限に頼らない)。

# PDF のアップロード欄 (``pdf_upload_form``) のラベルの接頭辞
UPLOADER_LABEL = "PDF file(s)"
PDF_WRITE_ERROR = OSError(errno.EIO, "Input/output error")


def pdf_uploads(count: int) -> list[FakeUpload]:
    """中身が互いに異なる、アップロードされた PDF を ``count`` 件作る."""
    return [
        FakeUpload(
            f"upload-{index:02d}.pdf",
            PDF_BYTES + f"%upload {index:02d}\n".encode("ascii"),
        )
        for index in range(count)
    ]


def fail_pdf_write(
    monkeypatch: pytest.MonkeyPatch, failing_call: Optional[int] = 2
) -> None:
    """PDF の ``failing_call`` 回目の書き込みだけが ``OSError`` で失敗する.

    ``failing_call`` が ``None`` のときは、すべての書き込みが失敗する。
    """
    if failing_call is None:

        def always_fail(*args: Any, **kwargs: Any) -> Any:
            raise PDF_WRITE_ERROR

        fake_open: Callable[..., Any] = always_fail
    else:
        fake_open = failing_open_on_call(failing_call, PDF_WRITE_ERROR)
    monkeypatch.setattr(_common, "open", fake_open, raising=False)


@pytest.fixture
def shown_errors(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """``st.error`` に渡された本文を、呼ばれた順に記録する.

    記録したうえで、本来のエラーも表示する (``AppTest`` の要素は残る)。
    再描画で画面から消えたエラーの本文を、後から読むために使う。
    """
    calls: list[str] = []
    original = st.error

    def error(body: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(str(body))
        return original(body, *args, **kwargs)

    monkeypatch.setattr(st, "error", error)
    return calls


EDIT_CHANGES = [
    pytest.param({"journal": "A Different Journal"}, id="same-folder"),
    pytest.param({"title": "A Revised Study"}, id="renamed-folder"),
]


def saved_pdf_names(
    converted_space: ConvertedSpace, entry: Entry
) -> list[str]:
    """``entry`` の PDF フォルダにある PDF の名前を、変換後の空間の実体で返す."""
    return sorted(
        path.name
        for path in (converted_space.pdf / entry.pdf_dir_name).glob("*.pdf")
    )


@pytest.mark.parametrize("changes", EDIT_CHANGES)
def test_the_edit_dialog_keeps_a_failed_pdf_save_as_a_notice(
    converted_space: ConvertedSpace,
    data_paths: DataPaths,
    make_entry: Callable[..., Entry],
    fake_file_uploader: Callable[..., None],
    shown_errors: list[str],
    monkeypatch: pytest.MonkeyPatch,
    changes: dict[str, str],
) -> None:
    """一部の保存に失敗した更新の後、失敗が通知に残る (1.4, 1.6).

    3 件を追加し、2 件目の書き込みだけが失敗する。更新は成功して再描画
    するので、画面のエラーは消える。失敗した 1 件の文は、画面のエラーと
    同じ表示用のパスと理由で通知に残る。保存できた 2 件は残り、論文は
    更新されている。変換後のパスの prefix は通知に出ない。
    """
    entry = make_entry()
    (key,) = store_papers(data_paths, entry)
    updated = Entry({**entry, **changes})
    fake_file_uploader({UPLOADER_LABEL: pdf_uploads(3)})
    fail_pdf_write(monkeypatch)
    at = open_edit_dialog(key)

    at = submit_edit(at, **changes)

    assert not at.exception, describe_exceptions(at)
    # 更新に成功して再描画した (再描画の前に出たエラーは、実画面では消える。
    # AppTest は版によって残すので、画面のエラーの有無では確かめない)
    assert at.session_state["runs"] == 3
    assert len(shown_errors) == 1
    assert at.session_state[KEY_NOTICES] == shown_errors
    (notice,) = at.session_state[KEY_NOTICES]
    failed = updated.get_pdf_dir(data_paths.pdf) / "01.pdf"
    assert to_display_path(failed) in notice
    assert "Input/output error" in notice
    assert str(failed) not in notice  # 変換後のパスは出さない
    assert saved_pdf_names(converted_space, updated) == ["00.pdf", "01.pdf"]
    (stored,) = stored_papers(data_paths.list_file).values()
    assert stored.items() >= changes.items()


def test_the_edit_dialog_keeps_every_failed_pdf_save(
    data_paths: DataPaths,
    make_entry: Callable[..., Entry],
    fake_file_uploader: Callable[..., None],
    shown_errors: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """すべての保存に失敗したら、ファイルごとに 1 文ずつ通知に残る (1.6).

    PDF は 1 件も保存されないが、論文は更新される。
    """
    (key,) = store_papers(data_paths, make_entry())
    fake_file_uploader({UPLOADER_LABEL: pdf_uploads(3)})
    fail_pdf_write(monkeypatch, None)
    at = open_edit_dialog(key)

    at = submit_edit(at, journal="A Different Journal")

    assert not at.exception, describe_exceptions(at)
    assert at.session_state["runs"] == 3
    assert len(shown_errors) == 3
    assert at.session_state[KEY_NOTICES] == shown_errors
    assert list(data_paths.pdf.rglob("*.pdf")) == []
    (stored,) = stored_papers(data_paths.list_file).values()
    assert stored["journal"] == "A Different Journal"


def test_the_failed_pdf_save_is_shown_in_the_next_render_and_then_cleared(
    converted_space: ConvertedSpace,
    data_paths: DataPaths,
    make_entry: Callable[..., Entry],
    fake_file_uploader: Callable[..., None],
    shown_errors: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """再描画の後の画面に、失敗と表示用のパスが警告として出て、その次には出ない.

    ``config_page`` と同じく、描画の冒頭で通知を表示して消す画面
    (``show_notices``) で確かめる。
    """
    entry = make_entry()
    (key,) = store_papers(data_paths, entry)
    fake_file_uploader({UPLOADER_LABEL: pdf_uploads(3)})
    fail_pdf_write(monkeypatch)
    at = open_edit_dialog(key, show_notices=True)

    at = submit_edit(at, journal="A Different Journal")

    assert not at.exception, describe_exceptions(at)
    assert at.session_state["runs"] == 3  # 更新に成功して再描画した
    (warning,) = [w.value for w in at.warning]
    failed = entry.get_pdf_dir(data_paths.pdf) / "01.pdf"
    assert warning == shown_errors[0]
    assert to_display_path(failed) in warning
    assert str(failed) not in warning
    assert KEY_NOTICES not in at.session_state

    at = at.run()

    assert not at.exception, describe_exceptions(at)
    assert [w.value for w in at.warning] == []


def test_the_real_page_shows_the_kept_failure_once(
    converted_space: ConvertedSpace,
    data_paths: DataPaths,
    make_entry: Callable[..., Entry],
    fake_file_uploader: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """編集ダイアログが残した通知を、実際のページが 1 回だけ警告で表示する (1.6).

    ``AppTest`` は、ダイアログだけの再実行も、``edit_screen`` とページの間の
    session state の共有もできない。そこで、``edit_screen`` が残した通知を
    取り出し、実際のページ (``run_page``) の session state に置いて描画
    する。
    """
    (key,) = store_papers(data_paths, make_entry())
    fake_file_uploader({UPLOADER_LABEL: pdf_uploads(3)})
    fail_pdf_write(monkeypatch)
    at = open_edit_dialog(key)
    at = submit_edit(at, journal="A Different Journal")
    notices = list(at.session_state[KEY_NOTICES])
    assert len(notices) == 1  # 前提
    # Streamlit 1.50 は、1 つのテストの中の最初の AppTest で、マルチページ判定
    # を固定する。from_function の後に from_file を実行するので、判定し直す
    monkeypatch.setattr(
        PagesManager, "uses_pages_directory", None, raising=False
    )
    page = run_page(None)
    assert not page.exception, describe_exceptions(page)
    own_warnings = [w.value for w in page.warning]

    page.session_state[KEY_NOTICES] = notices
    page = page.run()

    assert not page.exception, describe_exceptions(page)
    assert [w.value for w in page.warning] == [*notices, *own_warnings]
    assert KEY_NOTICES not in page.session_state

    page = page.run()

    assert not page.exception, describe_exceptions(page)
    assert [w.value for w in page.warning] == own_warnings


@pytest.mark.parametrize("count", [0, 2])
def test_the_edit_dialog_adds_no_notice_when_every_pdf_is_saved(
    converted_space: ConvertedSpace,
    data_paths: DataPaths,
    make_entry: Callable[..., Entry],
    fake_file_uploader: Callable[..., None],
    shown_errors: list[str],
    count: int,
) -> None:
    """失敗がなければ、通知は増えない (追加なしの更新も同じ) (1.6)."""
    entry = make_entry()
    (key,) = store_papers(data_paths, entry)
    fake_file_uploader({UPLOADER_LABEL: pdf_uploads(count)})
    at = open_edit_dialog(key)

    at = submit_edit(at, journal="A Different Journal")

    assert not at.exception, describe_exceptions(at)
    assert at.session_state["runs"] == 3  # 更新に成功して再描画した
    assert shown_errors == []
    assert KEY_NOTICES not in at.session_state
    assert len(saved_pdf_names(converted_space, entry)) == count


def test_the_edit_dialog_adds_no_notice_for_a_rejected_update(
    data_paths: DataPaths,
    make_entry: Callable[..., Entry],
    fake_file_uploader: Callable[..., None],
    shown_errors: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重複で拒否された更新は、通知を増やさず、エラーを表示したままにする (1.6).

    拒否では PDF を保存しないので、保存の失敗は起きない。再描画もしない。
    """
    first = make_entry(title="Alpha Paper")
    second = make_entry(title="Beta Paper")
    _, second_key = store_papers(data_paths, first, second)
    fake_file_uploader({UPLOADER_LABEL: pdf_uploads(2)})
    fail_pdf_write(monkeypatch, None)
    at = open_edit_dialog(second_key)

    at = submit_edit(at, title="Alpha Paper")

    assert not at.exception, describe_exceptions(at)
    (message,) = error_messages(at)
    assert message.startswith("FAIL: Duplicated pdf_dir_name: ")
    assert shown_errors == [message]
    assert at.session_state["runs"] == 2  # 再描画していない
    assert KEY_NOTICES not in at.session_state
    assert list(data_paths.pdf.rglob("*.pdf")) == []


def test_the_edit_dialog_adds_no_notice_when_saving_the_list_fails(
    data_paths: DataPaths,
    make_entry: Callable[..., Entry],
    fake_file_uploader: Callable[..., None],
    shown_errors: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """論文リストを保存できず失敗した更新は、通知を増やさず再描画しない (1.6).

    PDF の保存の失敗と、保存の中止のエラーが、どちらも画面に残る。
    """
    (key,) = store_papers(data_paths, make_entry())
    fake_file_uploader({UPLOADER_LABEL: pdf_uploads(2)})
    fail_pdf_write(monkeypatch, 1)
    at = open_edit_dialog(key)
    broken = b'{"a": {"title": 1}}'
    data_paths.list_file.write_bytes(broken)

    def copy2(*args: Any, **kwargs: Any) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(shutil, "copy2", copy2)

    at = submit_edit(at, journal="A Different Journal")

    assert not at.exception, describe_exceptions(at)
    pdf_failure, list_failure = error_messages(at)
    assert "Input/output error" in pdf_failure
    assert "保存しませんでした" in list_failure
    assert at.session_state["runs"] == 2  # 再描画していない
    assert KEY_NOTICES not in at.session_state
    assert data_paths.list_file.read_bytes() == broken


def test_the_edit_dialog_keeps_the_notices_that_are_already_pending(
    data_paths: DataPaths,
    make_entry: Callable[..., Entry],
    fake_file_uploader: Callable[..., None],
    shown_errors: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """既に保留中の通知は消さず、その後ろに失敗の文を追記する (1.6)."""
    (key,) = store_papers(data_paths, make_entry())
    fake_file_uploader({UPLOADER_LABEL: pdf_uploads(3)})
    fail_pdf_write(monkeypatch)
    at = open_edit_dialog(key)
    at.session_state[KEY_NOTICES] = ["EARLIER NOTICE"]

    at = submit_edit(at, journal="A Different Journal")

    assert not at.exception, describe_exceptions(at)
    assert len(shown_errors) == 1
    assert at.session_state[KEY_NOTICES] == ["EARLIER NOTICE", *shown_errors]


def test_the_edit_dialog_puts_the_failure_after_the_backup_notice(
    data_paths: DataPaths,
    make_entry: Callable[..., Entry],
    fake_file_uploader: Callable[..., None],
    shown_errors: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """壊れた論文リストの退避先の通知は消えず、失敗の文はその後ろに並ぶ (1.6).

    ダイアログを開いた後に ``list.json`` が壊れると、更新の保存が壊れた
    ファイルを退避し、退避先を通知に残す。その後に、PDF の保存の失敗が
    追記される。
    """
    (key,) = store_papers(data_paths, make_entry())
    fake_file_uploader({UPLOADER_LABEL: pdf_uploads(3)})
    fail_pdf_write(monkeypatch)
    at = open_edit_dialog(key)
    data_paths.list_file.write_bytes(b'{"a": {"title": 1}}')

    at = submit_edit(at, journal="A Different Journal")

    assert not at.exception, describe_exceptions(at)
    (backup,) = sorted(data_paths.root.glob("list.json.broken-*"))
    notices = at.session_state[KEY_NOTICES]
    assert len(notices) == 2
    assert to_display_path(backup) in notices[0]
    assert len(shown_errors) == 1
    assert notices[1] == shown_errors[0]
