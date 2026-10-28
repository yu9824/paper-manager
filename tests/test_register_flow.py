"""登録ページの結合テスト (要件 12, 13).

登録ページ (``pages/_register.py``) を ``conftest.run_page`` で実行し、
ウィジェットを操作して登録し、論文リストのファイル (``list.json``) と
画面の通知を検証する。実ブラウザも Streamlit サーバーも起動せず、
外部ネットワークにも出ない。

このモジュールは次のセクションで構成される。

- 登録ページの操作: ウィジェットの特定・送信・論文リストの読み出し・
  共通の検証 (後続のセクションからも使う)
- BibTeX 経路の登録 (要件 12.1, 12.2)
- BibTeX 経路のエラー (要件 12.3)
- 重複登録 (要件 12.8)
- 既知バグ: KB6 (要件 13.1, 13.2, 13.4)

DOI 経路・手動入力・必須項目の不足・PDF 添付 (要件 12.4〜12.7, 12.9) は、
同じ補助を使って、タスク 5.3 がこのモジュールの末尾に追記する。

ウィジェットの特定
------------------
登録ページには送信ボタン ``📤 登録`` が 3 つ (BIB・DOI・CUSTOM)、ラベル
``DOI`` のテキスト入力が 2 つ (``doi_form`` と ``custom_form``) あり、
ラベルでは特定できない。BibTeX のテキスト入力にはキーがない。そこで
ウィジェットが属するフォームの ID (``st.form`` の第 1 引数。
``bib_form`` / ``doi_form`` / ``custom_form``) で特定する。出現順には
頼らない。

通知の検証
----------
``st_messages`` は ``AppTest`` と併用できないので使わず、``AppTest`` の
要素 (``at.error`` / ``at.success``) を見る。エラーは種類ごとに異なる
ことを、メッセージ全文ではなく最小限の語 (``ERROR_MARKERS``) で検証する。

「論文リストが変わらない」の意味
--------------------------------
比べるのは、``list.json`` に保存されている論文のキーとエントリ内容
(``json.load`` した辞書) である。``list.json`` がないことは、空の論文
リストと同じ扱いにする。エラーのテストは ``sample_data`` (2 件) から
始めて、誤って登録されたら件数と内容が変わるようにする。
"""

import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Literal, Optional

import pytest
from conftest import DataPaths, FakeUpload, SampleData, run_page
from streamlit.testing.v1 import AppTest

# --- 登録ページの操作 -----------------------------------------------------

REGISTER_PAGE = "pages/_register.py"

# 3 つの登録フォームの ID (st.form の第 1 引数)。
FORM_BIB = "bib_form"
FORM_DOI = "doi_form"
FORM_CUSTOM = "custom_form"

# BibTeX ファイルのアップロード欄のラベル (``fake_file_uploader`` には
# 接頭辞として渡す)。PDF のアップロード欄 (``PDF file(s) (.pdf)``) とは
# 一致しない。
BIB_UPLOAD_LABEL = "BibTeXファイルをアップロード"

# エラーの種類ごとの、メッセージに含まれる語。互いに含まれない語を選び、
# あるエラーが別の種類のエラーと取り違えられないことを検証する。
ERROR_MARKERS = {
    "empty": "入力されていません",
    "multiple": "1つだけ",
    "no_entry": "有効なエントリ",
    "duplicate": "Duplicated",
}

PaperDicts = dict[str, dict[str, Any]]
BibVia = Literal["text", "file"]


def widgets_in_form(widgets: Iterable[Any], form_id: str) -> list[Any]:
    """``widgets`` のうち、フォーム ``form_id`` に属するものを返す.

    Parameters
    ----------
    widgets : Iterable[Any]
        ``at.text_area`` や ``at.button`` などの要素の一覧。
    form_id : str
        ``FORM_BIB`` / ``FORM_DOI`` / ``FORM_CUSTOM`` のいずれか。

    Returns
    -------
    list[Any]
        ページでの出現順に、フォームに属する要素。
    """
    return [w for w in widgets if w.proto.form_id == form_id]


def widget_in_form(widgets: Iterable[Any], form_id: str) -> Any:
    """``widgets`` のうち、フォーム ``form_id`` に属する 1 つを返す.

    Raises
    ------
    AssertionError
        該当する要素が 1 つでない場合。
    """
    found = widgets_in_form(widgets, form_id)
    assert len(found) == 1, (
        f"expected exactly one widget in form {form_id!r}, "
        f"got {len(found)}: {found}"
    )
    return found[0]


def describe_exceptions(at: AppTest) -> str:
    """``at.exception`` の内容を、失敗メッセージ用の文字列にする."""
    return "\n".join(
        f"{e.proto.type}: {e.value}\n{''.join(e.stack_trace)}"
        for e in at.exception
    )


def open_register_page() -> AppTest:
    """登録ページを開く (例外なく描画されることまで確かめる)."""
    at = run_page(REGISTER_PAGE)
    assert not at.exception, describe_exceptions(at)
    return at


def submit(at: AppTest, form_id: str) -> AppTest:
    """フォーム ``form_id`` の送信ボタン (``📤 登録``) を押して再実行する."""
    widget_in_form(at.button, form_id).click()
    return at.run()


def error_messages(at: AppTest) -> list[str]:
    """画面に表示されているエラーの本文を、表示順に返す."""
    return [e.value for e in at.error]


def success_messages(at: AppTest) -> list[str]:
    """画面に表示されている成功の本文を、表示順に返す."""
    return [s.value for s in at.success]


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


def pdf_tree(pdf_root: Path) -> dict[str, Optional[bytes]]:
    """PDF ディレクトリの中身を、相対パスと中身の対応で返す.

    Returns
    -------
    dict[str, Optional[bytes]]
        ファイルは中身のバイト列、ディレクトリは ``None``。
    """
    return {
        path.relative_to(pdf_root).as_posix(): (
            path.read_bytes() if path.is_file() else None
        )
        for path in sorted(pdf_root.rglob("*"))
    }


def assert_registered(
    at: AppTest,
    list_file: Path,
    papers_before: PaperDicts,
    added: PaperDicts,
) -> None:
    """登録が成功し、論文が ``added`` の分だけ追加されたことを検証する.

    Parameters
    ----------
    at : AppTest
        送信後に実行した登録ページ。
    list_file : Path
        論文リストのファイル。
    papers_before : PaperDicts
        登録前に保存されていた論文。
    added : PaperDicts
        追加されるはずの論文 (キーとエントリ内容)。入力から導いた期待値
        を渡す。
    """
    assert not at.exception, describe_exceptions(at)
    assert not error_messages(at)
    assert len(success_messages(at)) == 1, success_messages(at)
    assert stored_papers(list_file) == {**papers_before, **added}


def assert_rejected(
    at: AppTest,
    list_file: Path,
    papers_before: PaperDicts,
    kind: Optional[str] = None,
) -> None:
    """登録が拒否され、エラーが 1 つ表示され、論文が変わらないことを検証する.

    Parameters
    ----------
    at : AppTest
        送信後に実行した登録ページ。
    list_file : Path
        論文リストのファイル。
    papers_before : PaperDicts
        登録前に保存されていた論文。
    kind : Optional[str], default None
        ``ERROR_MARKERS`` のキー。指定すると、エラーがその種類の語を含み、
        ほかの種類の語を含まないことを検証する。``None`` なら、エラーが
        表示されることだけを検証する。
    """
    assert not at.exception, describe_exceptions(at)
    errors = error_messages(at)
    assert len(errors) == 1, errors
    if kind is not None:
        assert ERROR_MARKERS[kind] in errors[0], errors
        for other, marker in ERROR_MARKERS.items():
            if other != kind:
                assert marker not in errors[0], (other, errors)
    assert not success_messages(at)
    assert stored_papers(list_file) == papers_before


@pytest.fixture
def papers_before(
    sample_data: SampleData, data_paths: DataPaths
) -> PaperDicts:
    """``sample_data`` が保存した論文 (2 件) を、登録前の状態として返す."""
    papers = stored_papers(data_paths.list_file)
    assert set(papers) == {
        sample_data.with_pdf_key,
        sample_data.without_pdf_key,
    }
    return papers


@pytest.fixture
def submit_bib(
    fake_file_uploader: Callable[..., None],
) -> Callable[..., AppTest]:
    """BibTeX を入力して登録ボタンを押す関数を返す.

    返された関数 ``submit_bib(bib, via="text")`` は、登録ページを開き、
    ``via`` に応じて BibTeX を入力し、BIB フォームの送信ボタンを押して
    再実行した ``AppTest`` を返す。

    - ``via="text"``: テキスト入力欄に ``bib`` を入力する
    - ``via="file"``: ファイルのアップロード欄に、``bib`` を UTF-8 で
      符号化した ``.bib`` ファイルが選ばれた状態にする (テキスト入力欄は
      空のまま)
    """

    def _submit_bib(bib: str, via: BibVia = "text") -> AppTest:
        if via == "file":
            fake_file_uploader(
                {BIB_UPLOAD_LABEL: FakeUpload("paper.bib", bib.encode())}
            )
        elif via != "text":
            raise ValueError(f"unknown way to enter BibTeX: {via!r}")
        at = open_register_page()
        if via == "text":
            widget_in_form(at.text_area, FORM_BIB).set_value(bib)
        return submit(at, FORM_BIB)

    return _submit_bib


# --- BibTeX 経路の登録 (要件 12.1, 12.2) -----------------------------------

# 登録する BibTeX。年は固定する (実行日に依存させない)。
BIB_ARTICLE = """\
@article{Doe2020,
  author = {Doe, Jane and Roe, John},
  title = {A Study of Registering Papers},
  journal = {Journal of Registration},
  year = {2020},
  volume = {7},
  pages = {1--10},
}
"""

# BIB_ARTICLE から導いた、登録後のエントリ。
# - 著者は "姓, 名" から "名 姓" に直して " and " で連結する
# - 引用キー (Doe2020) は使わず、識別子は「空白を除いた第一著者名 + 年 +
#   _ + 連番」(要件 2.2) で新たに付与し、ID にも同じ値を入れる
EXPECTED_KEY = "JaneDoe2020_0"
EXPECTED_ENTRY = {
    "ENTRYTYPE": "article",
    "ID": EXPECTED_KEY,
    "author": "Jane Doe and John Roe",
    "title": "A Study of Registering Papers",
    "journal": "Journal of Registration",
    "year": "2020",
    "volume": "7",
    "pages": "1--10",
}


def test_bibtex_text_registers_entry(
    submit_bib: Callable[..., AppTest],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """BibTeX テキストで登録すると、エントリが 1 件追加され成功が出る (12.1)."""
    at = submit_bib(BIB_ARTICLE, "text")

    assert_registered(
        at,
        data_paths.list_file,
        papers_before,
        {EXPECTED_KEY: EXPECTED_ENTRY},
    )


def test_bibtex_file_registers_same_entry(
    submit_bib: Callable[..., AppTest],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """BibTeX ファイルで登録すると、テキストと同じエントリが追加される (12.2)."""
    at = submit_bib(BIB_ARTICLE, "file")

    assert_registered(
        at,
        data_paths.list_file,
        papers_before,
        {EXPECTED_KEY: EXPECTED_ENTRY},
    )


# --- BibTeX 経路のエラー (要件 12.3) ---------------------------------------

# 2 件の有効なエントリを含む BibTeX。
BIB_TWO_ENTRIES = """\
@article{first,
  author = {Doe, Jane},
  title = {First Paper},
  journal = {Journal of Registration},
  year = {2020},
}
@article{second,
  author = {Roe, John},
  title = {Second Paper},
  journal = {Journal of Registration},
  year = {2021},
}
"""


def test_empty_bibtex_is_rejected(
    submit_bib: Callable[..., AppTest],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """テキストもファイルも未入力で登録すると、未入力のエラーになる (12.3)."""
    at = submit_bib("", "text")

    assert_rejected(at, data_paths.list_file, papers_before, "empty")


@pytest.mark.parametrize("via", ["text", "file"])
def test_bibtex_with_several_entries_is_rejected(
    via: BibVia,
    submit_bib: Callable[..., AppTest],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """複数のエントリを含む BibTeX は、1 件も登録されずエラーになる (12.3)."""
    at = submit_bib(BIB_TWO_ENTRIES, via)

    assert_rejected(at, data_paths.list_file, papers_before, "multiple")


@pytest.mark.parametrize("via", ["text", "file"])
@pytest.mark.parametrize(
    "text",
    [
        pytest.param("hello", id="plain-text"),
        pytest.param("@comment{only a comment}", id="comment-only"),
    ],
)
def test_bibtex_without_valid_entry_is_rejected(
    text: str,
    via: BibVia,
    submit_bib: Callable[..., AppTest],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """有効なエントリがない BibTeX は、登録されずエラーになる (12.3).

    構文としては成立するが、エントリを含まないテキストを使う
    (構文エラーになるテキストは KB6)。
    """
    at = submit_bib(text, via)

    assert_rejected(at, data_paths.list_file, papers_before, "no_entry")


# --- 重複登録 (要件 12.8) --------------------------------------------------

# sample_data の PDF 付きエントリ (著者 Taro Yamada、タイトル「深層学習に
# よる画像認識の研究」、年 2021) と、PDF フォルダ名
# ("<year> - <first_author> - <title>") が同じになる BibTeX。掲載誌と引用
# キーは異なる: 重複の判定はフォルダ名で行い、それ以外の内容には依らない。
BIB_DUPLICATE = """\
@article{another-key,
  author = {Yamada, Taro},
  title = {深層学習による画像認識の研究},
  journal = {Another Journal},
  year = {2021},
}
"""

# 著者と年は sample_data のエントリと同じだが、タイトルが異なる (フォルダ名
# が異なる) BibTeX。
BIB_SAME_AUTHOR_AND_YEAR = """\
@article{yamada-2,
  author = {Yamada, Taro},
  title = {Another Study by the Same Author},
  journal = {Another Journal},
  year = {2021},
}
"""


def test_entry_with_duplicate_folder_name_is_rejected(
    submit_bib: Callable[..., AppTest],
    sample_data: SampleData,
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """既存の論文とフォルダ名が重複する登録は、拒否されてエラーになる (12.8)."""
    existing = sample_data.paper_list[sample_data.with_pdf_key]
    # 前提: 既存の論文のフォルダ名が、BIB_DUPLICATE から作られる名前と同じ
    assert existing.pdf_dir_name == "2021 - Taro Yamada - 深層学習による画像認識の研究"
    pdfs_before = pdf_tree(data_paths.pdf)

    at = submit_bib(BIB_DUPLICATE, "text")

    assert_rejected(at, data_paths.list_file, papers_before, "duplicate")
    assert len(stored_papers(data_paths.list_file)) == len(papers_before)
    assert pdf_tree(data_paths.pdf) == pdfs_before


def test_same_author_and_year_with_other_title_is_not_duplicate(
    submit_bib: Callable[..., AppTest],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """著者と年が同じでも、フォルダ名が違えば重複にならず登録される (12.8).

    重複エラーが「同じ著者・年」ではなく「同じフォルダ名」で出ることの
    対照。識別子は既存の ``TaroYamada2021_0`` と重ならないよう ``_1`` に
    なる。
    """
    at = submit_bib(BIB_SAME_AUTHOR_AND_YEAR, "text")

    assert_registered(
        at,
        data_paths.list_file,
        papers_before,
        {
            "TaroYamada2021_1": {
                "ENTRYTYPE": "article",
                "ID": "TaroYamada2021_1",
                "author": "Taro Yamada",
                "title": "Another Study by the Same Author",
                "journal": "Another Journal",
                "year": "2021",
            }
        },
    )


# --- 既知バグ: KB6 (要件 13.1, 13.2, 13.4) ---------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN BUG: 構文エラーの BibTeX を登録すると、load_bib が送出する "
        "pybtex の構文エラーを登録ページ (_render_bib_form) が捕捉せず、"
        "ページが例外で落ちる (KB6). Repro: BibTeX テキストに "
        "'@article{k, title={T}' (閉じ括弧がない) を入力して登録を押すと、"
        "pybtex.scanner.PrematureEOF ('syntax error in line 1: premature "
        "end of file') で at.exception が空でなくなる. 本来は例外に"
        "ならずにエラーが表示され、論文リストは変わらない"
    ),
)
def test_syntactically_broken_bibtex_shows_error_without_crashing(
    submit_bib: Callable[..., AppTest],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """構文エラーの BibTeX は、ページが落ちずにエラーが表示される (KB6)."""
    at = submit_bib("@article{k, title={T}", "text")

    assert_rejected(at, data_paths.list_file, papers_before)
