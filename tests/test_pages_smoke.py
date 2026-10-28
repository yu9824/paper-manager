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
比べるのは、``list.json`` に保存されている論文のキーとエントリ内容
(``json.load`` した辞書) である。ファイルのバイト列や更新時刻は比べない。
``list.json`` がないことは、空の論文リストと同じ扱いにする。データ領域が
空の状態では、実行前に ``list.json`` がなく、実行後に空の論文リスト
(``{}``) が書かれるが、保存されている論文は 0 件のままなので、変わって
いないとみなす。実行後に ``list.json`` がないことも許容する。

論文リストページは PDF の本数を数えるとき、旧形式の PDF を新形式へ移行する
非推奨の内部処理 (``pdf_filename`` / ``_migrate_legacy_pdf_filenames``)
を通るため、``DeprecationWarning`` が出る。警告の確認が目的ではないので、
この 2 つの警告だけを無視する (ほかの警告は隠さない)。
"""

import json
from pathlib import Path
from typing import Any, Optional

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
