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
- DOI 経路 (要件 12.4, 12.5)
- 手動入力 (要件 12.6)
- 必須フィールドの不足 (要件 12.7)
- PDF の添付 (要件 12.9)
- 既知バグ: KB10 (要件 13.1, 13.2, 13.4)

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
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, Literal, NamedTuple, Optional, Union

import pytest
import requests
from conftest import (
    PDF_BYTES,
    CrossrefMetadata,
    DataPaths,
    FakeUpload,
    SampleData,
    crossref_article,
    run_page,
)
from crossref.restful import Works
from streamlit.testing.v1 import AppTest

import paper_manager

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
    "fetch_failed": "無効なDOI",
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


def without_blank(papers: PaperDicts) -> PaperDicts:
    """``papers`` から、値が空のフィールドを除いたものを返す."""
    return {
        key: {name: value for name, value in entry.items() if value != ""}
        for key, entry in papers.items()
    }


def assert_registered(
    at: AppTest,
    list_file: Path,
    papers_before: PaperDicts,
    added: PaperDicts,
    ignore_blank: bool = False,
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
    ignore_blank : bool, default False
        ``True`` なら、保存された論文と期待値の両方から、値が空のフィール
        ドを除いて比べる (未設定と空を同じ扱いにする)。
    """
    assert not at.exception, describe_exceptions(at)
    assert not error_messages(at)
    assert len(success_messages(at)) == 1, success_messages(at)
    stored = stored_papers(list_file)
    expected = {**papers_before, **added}
    if ignore_blank:
        stored, expected = without_blank(stored), without_blank(expected)
    assert stored == expected


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


# --- DOI 経路 (要件 12.4, 12.5) --------------------------------------------

# 入力する DOI。固定の Crossref 応答の DOI にも使う。
DOI = "10.5555/fixed-doi"

# crossref_article(DOI) の既定の応答から導いた、登録後のエントリ。
# - 著者は応答の順に "<given> <family>" を " and " で連結する
# - 掲載誌は container-title の先頭、年は published の最初の年
# - 応答に volume・issue・page はない (保存されるかどうかは問わず、空は
#   未設定と同じ扱いにする: ignore_blank)
# - 識別子は「空白を除いた第一著者名 + 年 + _ + 連番」(要件 2.2)
EXPECTED_DOI_KEY = "JaneDoe2021_0"
EXPECTED_DOI_ENTRY = {
    "ENTRYTYPE": "article",
    "ID": EXPECTED_DOI_KEY,
    "author": "Jane Doe and John Roe",
    "title": "Fixed Metadata from Crossref",
    "journal": "Journal of Fixed Metadata",
    "year": "2021",
    "DOI": DOI,
}


def submit_doi(doi: str) -> AppTest:
    """登録ページを開き、DOI を入力して DOI フォームの登録ボタンを押す."""
    at = open_register_page()
    widget_in_form(at.text_input, FORM_DOI).set_value(doi)
    return submit(at, FORM_DOI)


def test_doi_registers_entry_from_crossref_response(
    fake_crossref: Callable[[Optional[CrossrefMetadata]], list[str]],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """DOI を登録すると、応答どおりのフィールドのエントリが追加される (12.4).

    入力した DOI で Crossref に問い合わせることも検証する。
    """
    requested = fake_crossref(crossref_article(DOI))

    at = submit_doi(DOI)

    assert requested == [DOI]
    assert_registered(
        at,
        data_paths.list_file,
        papers_before,
        {EXPECTED_DOI_KEY: EXPECTED_DOI_ENTRY},
        ignore_blank=True,
    )


def test_doi_registers_optional_fields_from_response(
    fake_crossref: Callable[[Optional[CrossrefMetadata]], list[str]],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """応答の volume・issue・page は、volume・number・pages になる (12.4)."""
    metadata = {
        **crossref_article(DOI),
        "volume": "12",
        "issue": "3",
        "page": "100-110",
    }
    fake_crossref(metadata)

    at = submit_doi(DOI)

    assert_registered(
        at,
        data_paths.list_file,
        papers_before,
        {
            EXPECTED_DOI_KEY: {
                **EXPECTED_DOI_ENTRY,
                "volume": "12",
                "number": "3",
                "pages": "100-110",
            }
        },
    )


def test_doi_of_entry_is_the_one_in_the_response(
    fake_crossref: Callable[[Optional[CrossrefMetadata]], list[str]],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """エントリの DOI は、入力した値でなく応答の値になる (12.4).

    問い合わせには、入力した DOI をそのまま使う。
    """
    typed = "10.5555/TYPED-DOI"
    canonical = "10.5555/canonical-doi"
    requested = fake_crossref(crossref_article(canonical))

    at = submit_doi(typed)

    assert requested == [typed]
    assert_registered(
        at,
        data_paths.list_file,
        papers_before,
        {EXPECTED_DOI_KEY: {**EXPECTED_DOI_ENTRY, "DOI": canonical}},
        ignore_blank=True,
    )


def test_empty_doi_is_rejected_without_asking_crossref(
    fake_crossref: Callable[[Optional[CrossrefMetadata]], list[str]],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """DOI が未入力だと、問い合わせずに未入力のエラーになる (12.5)."""
    requested = fake_crossref(crossref_article(DOI))

    at = submit_doi("")

    assert requested == []
    assert_rejected(at, data_paths.list_file, papers_before, "empty")
    assert "DOI" in error_messages(at)[0]


def test_doi_without_metadata_is_rejected(
    fake_crossref: Callable[[Optional[CrossrefMetadata]], list[str]],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """メタデータを取得できない DOI は、取得失敗のエラーになる (12.5)."""
    requested = fake_crossref(None)

    at = submit_doi(DOI)

    assert requested == [DOI]
    assert_rejected(at, data_paths.list_file, papers_before, "fetch_failed")


# --- 手動入力 (要件 12.6) --------------------------------------------------

# エントリタイプの選択欄 (フォームの外にある) のラベル。
ENTRY_TYPE_LABEL = "エントリタイプを選択"

# 手動入力フォームに入力する値。文字列はテキスト入力、整数は年の数値入力、
# リストは複数選択 (著者・タグ)。年は明示的に入力する (既定値は実行日の年
# なので、実行日に依存させない)。著者は 1 人にする (複数人は KB4a)。
FormValue = Union[str, int, list[str]]

CUSTOM_ARTICLE_VALUES: dict[str, FormValue] = {
    "author": ["Hanako Suzuki"],
    "title": "Manual Entry of a Paper",
    "journal": "Journal of Manual Entry",
    "year": 2019,
    "volume": "4",
    "pages": "11--20",
}
# 入力から導いた、登録後のエントリ。年は文字列で保存され、識別子は
# 「空白を除いた第一著者名 + 年 + _ + 連番」(要件 2.2)。
CUSTOM_ARTICLE_KEY = "HanakoSuzuki2019_0"
CUSTOM_ARTICLE_ENTRY = {
    "ENTRYTYPE": "article",
    "ID": CUSTOM_ARTICLE_KEY,
    "author": "Hanako Suzuki",
    "title": "Manual Entry of a Paper",
    "journal": "Journal of Manual Entry",
    "year": "2019",
    "volume": "4",
    "pages": "11--20",
}


def select_entry_type(at: AppTest, entry_type: str) -> AppTest:
    """エントリタイプを選んで再実行する (フォームのフィールドが替わる)."""
    selectors = [s for s in at.selectbox if s.label == ENTRY_TYPE_LABEL]
    assert len(selectors) == 1, selectors
    selectors[0].select(entry_type)
    at = at.run()
    assert not at.exception, describe_exceptions(at)
    return at


def custom_widget(elements: Iterable[Any], field: str) -> Any:
    """手動入力フォームの、フィールド ``field`` のウィジェットを返す.

    フィールドのウィジェットのラベルは、フィールド名と同じ。
    """
    found = [
        w for w in widgets_in_form(elements, FORM_CUSTOM) if w.label == field
    ]
    assert len(found) == 1, (field, found)
    return found[0]


def fill_custom_form(at: AppTest, values: Mapping[str, FormValue]) -> None:
    """手動入力フォームに値を入力する (再実行はしない)."""
    for field, value in values.items():
        elements: Iterable[Any]
        if isinstance(value, str):
            elements = at.text_input
        elif isinstance(value, int):
            elements = at.number_input
        else:
            elements = at.multiselect
        custom_widget(elements, field).set_value(value)


def submit_custom(entry_type: str, values: Mapping[str, FormValue]) -> AppTest:
    """エントリタイプを選び、値を入力して手動入力フォームを送信する."""
    at = select_entry_type(open_register_page(), entry_type)
    fill_custom_form(at, values)
    return submit(at, FORM_CUSTOM)


@pytest.mark.parametrize(
    ("entry_type", "values", "key", "fields"),
    [
        pytest.param(
            "article",
            CUSTOM_ARTICLE_VALUES,
            CUSTOM_ARTICLE_KEY,
            {
                k: v
                for k, v in CUSTOM_ARTICLE_ENTRY.items()
                if k not in ("ENTRYTYPE", "ID")
            },
            id="article",
        ),
        pytest.param(
            "book",
            {
                "author": ["Ichiro Tanaka"],
                "title": "A Book Entered by Hand",
                "publisher": "Hand Press",
                "year": 2018,
                "DOI": "10.5555/manual-book",
            },
            "IchiroTanaka2018_0",
            {
                "author": "Ichiro Tanaka",
                "title": "A Book Entered by Hand",
                "publisher": "Hand Press",
                "year": "2018",
                "DOI": "10.5555/manual-book",
            },
            id="book",
        ),
        pytest.param(
            "inproceedings",
            {
                "author": ["Jiro Sato"],
                "title": "A Talk Entered by Hand",
                "booktitle": "Proceedings of Hand Entry",
                "year": 2017,
            },
            "JiroSato2017_0",
            {
                "author": "Jiro Sato",
                "title": "A Talk Entered by Hand",
                "booktitle": "Proceedings of Hand Entry",
                "year": "2017",
            },
            id="inproceedings",
        ),
    ],
)
def test_custom_form_registers_entry_with_entered_values(
    entry_type: str,
    values: Mapping[str, FormValue],
    key: str,
    fields: dict[str, str],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """エントリタイプを選んで必須フィールドを入力すると、その内容で登録される (12.6).

    入力しなかったフィールドはエントリに現れない。
    """
    at = submit_custom(entry_type, values)

    assert_registered(
        at,
        data_paths.list_file,
        papers_before,
        {key: {"ENTRYTYPE": entry_type, "ID": key, **fields}},
    )


# --- 必須フィールドの不足 (要件 12.7) ---------------------------------------

# フィールドの定義ファイル。実装が使う定数は使わず、パッケージの場所から
# 導出して独立に読み込み、必須フィールドの期待値にする。
FIELDS_JSON = Path(paper_manager.__file__).parent / "app" / "fields.json"


def required_fields(entry_type: str) -> set[str]:
    """定義ファイルから、``entry_type`` の必須フィールド名の集合を読む."""
    definitions = json.loads(FIELDS_JSON.read_text(encoding="utf-8"))
    return {
        name
        for name, attributes in definitions[entry_type].items()
        if attributes["required"]
    }


def bibtex_of(entry_type: str, fields: Mapping[str, str]) -> str:
    """``entry_type`` のエントリで、``fields`` だけを持つ BibTeX を作る."""
    body = "".join(
        f"  {name} = {{{value}}},\n" for name, value in fields.items()
    )
    return f"@{entry_type}{{key,\n{body}}}\n"


def assert_rejected_for_missing(
    at: AppTest,
    list_file: Path,
    papers_before: PaperDicts,
    entry_type: str,
    given: Iterable[str],
) -> None:
    """必須フィールドの不足で拒否され、論文リストが変わらないことを検証する.

    エラーは、不足しているフィールド名をすべて含み、入力済みの必須
    フィールド名を含まず、ほかの種類のエラー (``ERROR_MARKERS``) でない。

    Parameters
    ----------
    at : AppTest
        送信後に実行した登録ページ。
    list_file : Path
        論文リストのファイル。
    papers_before : PaperDicts
        登録前に保存されていた論文。
    entry_type : str
        登録しようとしたエントリのタイプ。
    given : Iterable[str]
        登録しようとしたエントリが持つフィールド名。不足は、定義ファイル
        の必須フィールドとの差から導く。
    """
    required = required_fields(entry_type)
    missing = required - set(given)
    present = required & set(given)
    assert missing, "premise: at least one required field is missing"

    assert_rejected(at, list_file, papers_before)

    message = error_messages(at)[0]
    for name in missing:
        assert name in message, (name, message)
    for name in present:
        assert name not in message, (name, message)
    for marker in ERROR_MARKERS.values():
        assert marker not in message, (marker, message)


@pytest.mark.parametrize(
    ("entry_type", "fields"),
    [
        pytest.param(
            "article",
            {
                "author": "Doe, Jane",
                "title": "An Article Without Journal",
                "year": "2020",
            },
            id="article-without-journal",
        ),
        pytest.param(
            "book",
            {
                "author": "Doe, Jane",
                "title": "A Book Without Publisher",
                "year": "2020",
            },
            id="book-without-publisher",
        ),
        pytest.param(
            "inbook",
            {"author": "Doe, Jane", "year": "2020"},
            id="inbook-without-several",
        ),
    ],
)
def test_bibtex_with_missing_required_fields_is_rejected(
    entry_type: str,
    fields: dict[str, str],
    submit_bib: Callable[..., AppTest],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """必須フィールドが足りない BibTeX は、不足を示すエラーで拒否される (12.7)."""
    at = submit_bib(bibtex_of(entry_type, fields), "text")

    assert_rejected_for_missing(
        at, data_paths.list_file, papers_before, entry_type, fields
    )


def test_doi_with_missing_required_fields_is_rejected(
    fake_crossref: Callable[[Optional[CrossrefMetadata]], list[str]],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """必須フィールドが足りない DOI の応答は、不足を示すエラーで拒否される (12.7).

    Crossref の ``book`` の応答はタイトル・著者・年を持つが、出版社
    (``publisher``) を登録ページが取り込まないので、不足する。
    """
    requested = fake_crossref(crossref_article(DOI, type="book"))

    at = submit_doi(DOI)

    assert requested == [DOI]
    assert_rejected_for_missing(
        at,
        data_paths.list_file,
        papers_before,
        "book",
        {"author", "title", "year"},
    )


@pytest.mark.parametrize(
    ("entry_type", "values"),
    [
        pytest.param("article", {"year": 2020}, id="article-nothing-entered"),
        pytest.param(
            "article",
            {
                "author": ["Hanako Suzuki"],
                "title": "An Article Without Journal",
                "year": 2020,
            },
            id="article-without-journal",
        ),
        pytest.param(
            "book",
            {
                "author": ["Hanako Suzuki"],
                "title": "A Book Without Publisher",
                "year": 2020,
            },
            id="book-without-publisher",
        ),
    ],
)
def test_custom_form_with_missing_required_fields_is_rejected(
    entry_type: str,
    values: Mapping[str, FormValue],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """必須フィールドが空の手動入力は、不足を示すエラーで拒否される (12.7)."""
    at = submit_custom(entry_type, values)

    assert_rejected_for_missing(
        at, data_paths.list_file, papers_before, entry_type, values
    )


# --- PDF の添付 (要件 12.9) ------------------------------------------------

# PDF のアップロード欄のラベルの接頭辞。BibTeX・DOI・手動入力の 3 つの
# フォームのすべてが、同じラベル (``PDF file(s) (.pdf)``) の欄を持つ。
# BibTeX ファイルの欄 (``BIB_UPLOAD_LABEL``) とは一致しない。
PDF_UPLOAD_LABEL = "PDF file(s)"


class Registration(NamedTuple):
    """登録経路ごとの、登録されるエントリと PDF のフォルダ名の期待値."""

    key: str
    entry: dict[str, str]
    pdf_dir: str  # "<年> - <第一著者> - <タイトル>" (PDF の保存先のフォルダ)


# 各経路の、登録前に用意した入力から導いた期待値。
REGISTRATIONS = {
    "bib": Registration(
        EXPECTED_KEY,
        EXPECTED_ENTRY,
        "2020 - Jane Doe - A Study of Registering Papers",
    ),
    "doi": Registration(
        EXPECTED_DOI_KEY,
        EXPECTED_DOI_ENTRY,
        "2021 - Jane Doe - Fixed Metadata from Crossref",
    ),
    "custom": Registration(
        CUSTOM_ARTICLE_KEY,
        CUSTOM_ARTICLE_ENTRY,
        "2019 - Hanako Suzuki - Manual Entry of a Paper",
    ),
}


@pytest.fixture
def submit_route(
    submit_bib: Callable[..., AppTest],
    fake_crossref: Callable[[Optional[CrossrefMetadata]], list[str]],
) -> Callable[[str], AppTest]:
    """``REGISTRATIONS`` の経路で、登録を実行する関数を返す.

    返された関数 ``submit_route(route)`` は、``route`` (``"bib"`` /
    ``"doi"`` / ``"custom"``) の入力で登録ページを送信した ``AppTest``
    を返す。DOI 経路の Crossref は固定の応答に差し替える。
    """
    fake_crossref(crossref_article(DOI))

    def _submit_route(route: str) -> AppTest:
        if route == "bib":
            return submit_bib(BIB_ARTICLE, "text")
        if route == "doi":
            return submit_doi(DOI)
        if route == "custom":
            return submit_custom("article", CUSTOM_ARTICLE_VALUES)
        raise ValueError(f"unknown registration route: {route!r}")

    return _submit_route


@pytest.mark.parametrize("route", ["bib", "doi", "custom"])
def test_attached_pdfs_are_saved_in_the_entry_folder_from_00(
    route: str,
    submit_route: Callable[[str], AppTest],
    fake_file_uploader: Callable[..., None],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """PDF を添付して登録すると、エントリのフォルダに 00.pdf から連番で保存される (12.9).

    どの経路 (``route``) でも、添付した順に ``00.pdf``・``01.pdf``・
    ``02.pdf`` となり、中身は添付したものと同じ。ファイル名は添付時の名前
    と無関係で、既存のエントリの PDF は変わらない。
    """
    uploads = [
        FakeUpload("z-first.pdf", PDF_BYTES + b"%attached first\n"),
        FakeUpload("m-second.pdf", PDF_BYTES + b"%attached second\n"),
        FakeUpload("a-third.pdf", PDF_BYTES + b"%attached third\n"),
    ]
    fake_file_uploader({PDF_UPLOAD_LABEL: uploads})
    registration = REGISTRATIONS[route]
    pdfs_before = pdf_tree(data_paths.pdf)

    at = submit_route(route)

    assert_registered(
        at,
        data_paths.list_file,
        papers_before,
        {registration.key: registration.entry},
        ignore_blank=True,
    )
    assert pdf_tree(data_paths.pdf) == {
        **pdfs_before,
        registration.pdf_dir: None,
        f"{registration.pdf_dir}/00.pdf": uploads[0].getvalue(),
        f"{registration.pdf_dir}/01.pdf": uploads[1].getvalue(),
        f"{registration.pdf_dir}/02.pdf": uploads[2].getvalue(),
    }


# --- 既知バグ: KB10 (要件 13.1, 13.2, 13.4) --------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN BUG: DOI の問い合わせ (Works.doi) が通信の例外を送出しても、"
        "登録ページ (_render_doi_form) が捕捉せず、ページが例外で落ちて"
        "エラーも表示されない (KB10). Repro: Works.doi が "
        "requests.exceptions.ConnectionError を送出する状態 (オフライン・"
        "タイムアウト・プロキシの誤設定など。crossref の do_http_request は"
        "例外を握らない) で DOI を入力して登録を押すと、at.exception が "
        "ConnectionError になる. 本来は例外にならずにエラーが 1 つ表示され、"
        "論文リストは変わらない (要件 12.5 の「メタデータを取得できない」)"
    ),
)
def test_doi_lookup_connection_error_shows_error_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """通信の例外で DOI を取得できなくても、ページが落ちずにエラーが出る (KB10).

    ``fake_crossref`` は応答 (辞書または ``None``) しか返せないので、
    ``Works.doi`` を通信の例外を送出する関数に差し替える。外部ネットワーク
    には出ない。
    """
    requested: list[str] = []

    def offline(self: Works, doi: str, only_message: bool = True) -> None:
        requested.append(doi)
        raise requests.exceptions.ConnectionError("offline")

    monkeypatch.setattr(Works, "doi", offline)

    at = submit_doi(DOI)

    assert requested == [DOI]
    assert_rejected(at, data_paths.list_file, papers_before)
