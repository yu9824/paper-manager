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

論文リストページは PDF の本数を数えるとき、旧形式の PDF を新形式へ移行する
非推奨の内部処理 (``pdf_filename`` / ``_migrate_legacy_pdf_filenames``)
を通るため、``DeprecationWarning`` が出る。警告の確認が目的ではないので、
この 2 つの警告だけを無視する (ほかの警告は隠さない)。
"""

import json
from pathlib import Path
from typing import Any, NamedTuple, Optional

import pytest
from conftest import PAGES, DataPaths, run_page
from streamlit.testing.v1 import AppTest

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
