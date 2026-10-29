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
- BibTeX の構文エラーと文字コードのエラー (要件 5.4。旧 KB6)
- 著者のない BibTeX の登録 (要件 5.2)
- DOI 経路 (要件 12.4, 12.5)
- 手動入力 (要件 12.6)
- 必須フィールドの不足 (要件 12.7)
- PDF の添付 (要件 12.9)
- DOI 経路の通信エラーと Crossref の応答の取り込み (要件 6.1〜6.4。旧 KB10)

要件 12 は test-suite spec、要件 5.2, 5.4, 6.1〜6.4 は known-bug-fixes spec
の番号である。

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

import io
import json
import re
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, Literal, NamedTuple, NoReturn, Optional, Union

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
from pybtex.exceptions import PybtexError
from streamlit.testing.v1 import AppTest

import paper_manager
from paper_manager.app.helper import MAP_FIELDS
from paper_manager.app.pages._register import _create_entry_from_doi_metadata
from paper_manager.bib import load_bib

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

# PDF のアップロード欄のラベルの接頭辞。BibTeX・DOI・手動入力の 3 つの
# フォームのすべてが、同じラベル (``PDF file(s) (.pdf)``) の欄を持つ。
# BibTeX ファイルの欄 (``BIB_UPLOAD_LABEL``) とは一致しない。
PDF_UPLOAD_LABEL = "PDF file(s)"

# エラーの種類ごとの、メッセージに含まれる語。互いに含まれない語を選び、
# あるエラーが別の種類のエラーと取り違えられないことを検証する。
ERROR_MARKERS = {
    "empty": "入力されていません",
    "multiple": "1つだけ",
    "no_entry": "有効なエントリ",
    "duplicate": "Duplicated",
    "fetch_failed": "無効なDOI",
    "network": "通信",
    "syntax": "構文",
    "encoding": "文字コード",
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

    返された関数 ``submit_bib(bib, via="text", pdfs=None)`` は、登録ページを
    開き、``via`` に応じて BibTeX を入力し、BIB フォームの送信ボタンを押して
    再実行した ``AppTest`` を返す。

    - ``via="text"``: テキスト入力欄に ``bib`` (``str``) を入力する
    - ``via="file"``: ファイルのアップロード欄に、``bib`` が選ばれた状態に
      する (テキスト入力欄は空のまま)。``str`` は UTF-8 で符号化した
      ``.bib`` ファイルにする。``bytes`` はそのままの中身にする (文字コード
      の読めないファイルの検証に使う)
    - ``pdfs``: 指定すると、PDF のアップロード欄にそのファイルが選ばれた
      状態にする
    """

    def _submit_bib(
        bib: Union[str, bytes],
        via: BibVia = "text",
        pdfs: Optional[list[FakeUpload]] = None,
    ) -> AppTest:
        uploads: dict[str, Union[FakeUpload, list[FakeUpload]]] = {}
        if via == "file":
            content = bib.encode() if isinstance(bib, str) else bib
            uploads[BIB_UPLOAD_LABEL] = FakeUpload("paper.bib", content)
        elif via != "text":
            raise ValueError(f"unknown way to enter BibTeX: {via!r}")
        elif not isinstance(bib, str):
            raise ValueError("only a file can hold bytes of BibTeX")
        if pdfs is not None:
            uploads[PDF_UPLOAD_LABEL] = pdfs
        if uploads:
            fake_file_uploader(uploads)
        at = open_register_page()
        if via == "text":
            assert isinstance(bib, str)
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
    (構文エラーになるテキストは「BibTeX の構文エラーと文字コードのエラー」
    の節で扱う)。
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


# --- BibTeX の構文エラーと文字コードのエラー (要件 5.4。旧 KB6) -------------

# 構文として正しくない BibTeX。pybtex が例外を送出する書き方を、例外の種類
# ごとに 1 つずつ選ぶ (どれも ``pybtex.exceptions.PybtexError`` を継承する)。
BROKEN_BIBTEX = [
    pytest.param("@article{k, title={T}", id="premature-end-of-file"),
    pytest.param('@article{k, title="T}', id="unbalanced-braces"),
    pytest.param("@article{}", id="entry-key-expected"),
    pytest.param("@article{k, title=undefined_macro}", id="undefined-macro"),
    pytest.param("@article{k, title={A}, title={B}}", id="duplicate-field"),
    pytest.param(
        "@article{k, author={a b c d, e, f, g}, title={T}}",
        id="invalid-name",
    ),
    pytest.param(
        "@article{k, title={A}}\n@article{K, title={B}}",
        id="repeated-key-ignoring-case",
    ),
]

# 文字コードが UTF-8 でないために読めないファイルの中身 (同じ BibTeX を
# 別の文字コードで符号化したもの)。UTF-16 は BOM があっても UTF-8 としては
# 読めない。
UNDECODABLE_BIBTEX = [
    pytest.param(
        "@article{k, title={日本語の題名}}".encode("shift_jis"),
        id="shift_jis",
    ),
    pytest.param("@article{k, title={é}}".encode("latin-1"), id="latin-1"),
    pytest.param("@article{k, title={T}}".encode("utf-16"), id="utf-16"),
]


def pybtex_message(bib: str) -> str:
    """``bib`` を pybtex で読んだときの、構文エラーの理由を返す.

    画面のエラーが、pybtex が示す理由を含むことの期待値にする。
    """
    with pytest.raises(PybtexError) as excinfo:
        load_bib(io.StringIO(bib))
    return str(excinfo.value)


@pytest.mark.parametrize("via", ["text", "file"])
@pytest.mark.parametrize("bib", BROKEN_BIBTEX)
def test_syntactically_broken_bibtex_shows_error_without_crashing(
    bib: str,
    via: BibVia,
    submit_bib: Callable[..., AppTest],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """構文エラーの BibTeX は、ページが落ちずにエラーが 1 つ表示される (5.4).

    エラーは構文のエラーと分かる文言で、pybtex が示す理由を含む。ほかの
    種類のエラー (未入力・複数エントリ・有効なエントリなし・重複・無効な
    DOI) とは取り違えられない。論文リストは変わらない。
    """
    reason = pybtex_message(bib)

    at = submit_bib(bib, via)

    assert_rejected(at, data_paths.list_file, papers_before, "syntax")
    assert reason in error_messages(at)[0]


@pytest.mark.parametrize("via", ["text", "file"])
def test_syntactically_broken_bibtex_saves_no_attached_pdf(
    via: BibVia,
    submit_bib: Callable[..., AppTest],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """構文エラーの BibTeX と PDF を一緒に送信しても、PDF は保存されない (5.4)."""
    pdfs_before = pdf_tree(data_paths.pdf)
    pdfs = [FakeUpload("attached.pdf", PDF_BYTES + b"%attached\n")]

    at = submit_bib("@article{k, title={T}", via, pdfs)

    assert_rejected(at, data_paths.list_file, papers_before, "syntax")
    assert pdf_tree(data_paths.pdf) == pdfs_before


def test_syntactically_broken_bibtex_is_logged_as_an_error(
    caplog: pytest.LogCaptureFixture,
    submit_bib: Callable[..., AppTest],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """構文エラーは、画面だけでなくログにも ERROR で出る (5.4)."""
    bib = "@article{k, title={T}"
    reason = pybtex_message(bib)

    with caplog.at_level("ERROR"):
        at = submit_bib(bib, "text")

    assert_rejected(at, data_paths.list_file, papers_before, "syntax")
    logged = [
        record.getMessage()
        for record in caplog.records
        if record.levelname == "ERROR"
    ]
    assert len(logged) == 1, logged
    assert reason in logged[0]


@pytest.mark.parametrize("content", UNDECODABLE_BIBTEX)
def test_bibtex_file_with_unreadable_encoding_shows_error_without_crashing(
    content: bytes,
    submit_bib: Callable[..., AppTest],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """文字コードの読めない BibTeX ファイルは、落ちずにエラーが 1 つ出る (5.4).

    エラーは文字コードのエラーと分かる文言で、UTF-8 で保存し直すよう案内
    する。構文のエラーとは取り違えられない。論文リストは変わらない。
    """
    at = submit_bib(content, "file")

    assert_rejected(at, data_paths.list_file, papers_before, "encoding")
    assert "UTF-8" in error_messages(at)[0]


def test_bibtex_file_with_unreadable_encoding_saves_no_attached_pdf(
    submit_bib: Callable[..., AppTest],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """文字コードの読めない BibTeX ファイルと PDF を送信しても、PDF は保存されない (5.4)."""
    pdfs_before = pdf_tree(data_paths.pdf)
    pdfs = [FakeUpload("attached.pdf", PDF_BYTES + b"%attached\n")]

    at = submit_bib(
        "@article{k, title={日本語}}".encode("shift_jis"), "file", pdfs
    )

    assert_rejected(at, data_paths.list_file, papers_before, "encoding")
    assert pdf_tree(data_paths.pdf) == pdfs_before


def test_unexpected_exception_from_loading_bibtex_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
    submit_bib: Callable[..., AppTest],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """入力の誤りでない例外 (バグ) は、エラー表示にすり替えずにそのまま出る (5.4).

    捕捉するのは構文エラー (``PybtexError``) と文字コードのエラー
    (``UnicodeDecodeError``) だけで、想定外の例外はページの例外として
    現れる。論文リストは変わらない。
    """

    def broken_loader(*args: Any, **kwargs: Any) -> None:
        raise IndexError("unexpected failure in the loader")

    monkeypatch.setattr(paper_manager.bib, "load_bib", broken_loader)

    at = submit_bib(BIB_ARTICLE, "text")

    assert [e.proto.type for e in at.exception] == ["IndexError"]
    assert not error_messages(at)
    assert not success_messages(at)
    assert stored_papers(data_paths.list_file) == papers_before


def test_bibtex_error_does_not_block_the_next_registration(
    submit_bib: Callable[..., AppTest],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """構文エラーの後に正しい BibTeX を送信すると、登録される (5.4).

    エラーの後も、BIB フォームは次の入力を受け付ける。
    """
    broken = submit_bib("@article{k, title={T}", "text")
    assert_rejected(broken, data_paths.list_file, papers_before, "syntax")

    at = submit_bib(BIB_ARTICLE, "text")

    assert_registered(
        at,
        data_paths.list_file,
        papers_before,
        {EXPECTED_KEY: EXPECTED_ENTRY},
    )


# --- 著者のない BibTeX の登録 (要件 5.2) ------------------------------------

# 必須項目を満たす、著者のない BibTeX。misc には必須項目がない。
BIB_MISC_WITHOUT_AUTHOR = """\
@misc{manual-2022,
  title = {A Software Manual Without Author},
  year = {2022},
  howpublished = {\\url{https://example.com/manual}},
}
"""

# BIB_MISC_WITHOUT_AUTHOR から導いた、登録後のエントリ。
# - 著者のキーを作らない (空の著者を保存しない)
# - 識別子の第一著者は、著者がないので ``Unknown`` (要件 2.2 の規則)
# - PDF のフォルダ名の第一著者も ``Unknown`` (KB2a)
MISC_WITHOUT_AUTHOR_KEY = "Unknown2022_0"
MISC_WITHOUT_AUTHOR_ENTRY = {
    "ENTRYTYPE": "misc",
    "ID": MISC_WITHOUT_AUTHOR_KEY,
    "title": "A Software Manual Without Author",
    "year": "2022",
    "howpublished": "\\url{https://example.com/manual}",
}
MISC_WITHOUT_AUTHOR_PDF_DIR = (
    "2022 - Unknown - A Software Manual Without Author"
)


@pytest.mark.parametrize("via", ["text", "file"])
def test_bibtex_without_author_registers_misc_entry(
    via: BibVia,
    submit_bib: Callable[..., AppTest],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """必須項目を満たす著者のない BibTeX (misc) は、登録されて成功が出る (5.2).

    保存されるエントリは著者のキーを持たない。
    """
    at = submit_bib(BIB_MISC_WITHOUT_AUTHOR, via)

    assert_registered(
        at,
        data_paths.list_file,
        papers_before,
        {MISC_WITHOUT_AUTHOR_KEY: MISC_WITHOUT_AUTHOR_ENTRY},
    )
    assert (
        "author"
        not in stored_papers(data_paths.list_file)[MISC_WITHOUT_AUTHOR_KEY]
    )


@pytest.mark.parametrize("via", ["text", "file"])
def test_bibtex_without_author_saves_attached_pdfs_in_unknown_folder(
    via: BibVia,
    submit_bib: Callable[..., AppTest],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """著者のない BibTeX に PDF を添付すると、Unknown の入ったフォルダに保存される (5.2)."""
    uploads = [
        FakeUpload("first.pdf", PDF_BYTES + b"%attached first\n"),
        FakeUpload("second.pdf", PDF_BYTES + b"%attached second\n"),
    ]
    pdfs_before = pdf_tree(data_paths.pdf)

    at = submit_bib(BIB_MISC_WITHOUT_AUTHOR, via, uploads)

    assert_registered(
        at,
        data_paths.list_file,
        papers_before,
        {MISC_WITHOUT_AUTHOR_KEY: MISC_WITHOUT_AUTHOR_ENTRY},
    )
    assert pdf_tree(data_paths.pdf) == {
        **pdfs_before,
        MISC_WITHOUT_AUTHOR_PDF_DIR: None,
        f"{MISC_WITHOUT_AUTHOR_PDF_DIR}/00.pdf": uploads[0].getvalue(),
        f"{MISC_WITHOUT_AUTHOR_PDF_DIR}/01.pdf": uploads[1].getvalue(),
    }


def test_bibtex_without_author_with_the_same_title_and_year_is_duplicate(
    submit_bib: Callable[..., AppTest],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """著者のない BibTeX を 2 回登録すると、2 回目は重複として拒否される (5.2).

    著者のない論文のフォルダ名が ``Unknown`` で一意に決まり、既存の重複の
    検出が働く。
    """
    first = submit_bib(BIB_MISC_WITHOUT_AUTHOR, "text")
    assert_registered(
        first,
        data_paths.list_file,
        papers_before,
        {MISC_WITHOUT_AUTHOR_KEY: MISC_WITHOUT_AUTHOR_ENTRY},
    )
    papers_after_first = stored_papers(data_paths.list_file)

    at = submit_bib(BIB_MISC_WITHOUT_AUTHOR, "text")

    assert_rejected(at, data_paths.list_file, papers_after_first, "duplicate")


@pytest.mark.parametrize(
    "bib",
    [
        pytest.param(
            "@misc{k, author = {}, title = {T}, year = {2022}}",
            id="empty-author-field",
        ),
        pytest.param(
            "@misc{k, author = {,}, title = {T}, year = {2022}}",
            id="author-of-empty-names",
        ),
    ],
)
def test_bibtex_with_empty_author_registers_without_author(
    bib: str,
    submit_bib: Callable[..., AppTest],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """空の著者の BibTeX (misc) も、著者のないエントリとして登録される (5.2)."""
    at = submit_bib(bib, "text")

    assert_registered(
        at,
        data_paths.list_file,
        papers_before,
        {
            "Unknown2022_0": {
                "ENTRYTYPE": "misc",
                "ID": "Unknown2022_0",
                "title": "T",
                "year": "2022",
            }
        },
    )


# --- DOI 経路 (要件 12.4, 12.5) --------------------------------------------

# 入力する DOI。固定の Crossref 応答の DOI にも使う。
DOI = "10.5555/fixed-doi"

# crossref_article(DOI) の既定の応答から導いた、登録後のエントリ。
# - 著者は応答の順に "<given> <family>" を " and " で連結する
# - 掲載誌は container-title の先頭、年は published の最初の年
# - 応答に volume・issue・page はないので、エントリにもそれらのキーを作らない
#   (空の値を保存しない。要件 6.4)
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
# なので、実行日に依存させない)。著者は既定では 1 人にする (フォルダ名などの
# 期待値を単純にするため)。複数人の著者は、``article-two-authors`` の
# ケースで確かめる (`` and `` で連結して保存される)。
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
            "article",
            {
                **CUSTOM_ARTICLE_VALUES,
                "author": ["Hanako Suzuki", "Taro Yamada"],
            },
            CUSTOM_ARTICLE_KEY,
            {
                **{
                    k: v
                    for k, v in CUSTOM_ARTICLE_ENTRY.items()
                    if k not in ("ENTRYTYPE", "ID")
                },
                "author": "Hanako Suzuki and Taro Yamada",
            },
            id="article-two-authors",
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
    # 英単語の集合で比べる (``title`` が ``booktitle`` に含まれる、のような
    # 部分一致で誤判定しない)。
    words = set(re.findall(r"[A-Za-z]+", message))
    for name in missing:
        assert name in words, (name, message)
    for name in present:
        assert name not in words, (name, message)
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
        pytest.param(
            "article",
            {
                "title": "An Article Without Author",
                "journal": "Journal of Registration",
                "year": "2020",
            },
            id="article-without-author",
        ),
        pytest.param(
            "inproceedings",
            {
                "title": "A Talk Without Author",
                "booktitle": "Proceedings of Registration",
                "year": "2020",
            },
            id="inproceedings-without-author",
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


@pytest.mark.parametrize(
    "author",
    [
        pytest.param("", id="empty-author-field"),
        pytest.param(",", id="author-of-empty-names"),
    ],
)
def test_bibtex_with_empty_author_is_rejected_for_a_type_requiring_it(
    author: str,
    submit_bib: Callable[..., AppTest],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """空の著者の BibTeX は、著者が必須のタイプでは著者の不足で拒否される (5.2).

    空の著者を、必須項目を満たす著者として扱わない。
    """
    fields = {
        "title": "An Article With Empty Author",
        "journal": "Journal of Registration",
        "year": "2020",
    }
    at = submit_bib(bibtex_of("article", {"author": author, **fields}), "text")

    assert_rejected_for_missing(
        at, data_paths.list_file, papers_before, "article", fields
    )


def test_doi_with_missing_required_fields_is_rejected(
    fake_crossref: Callable[[Optional[CrossrefMetadata]], list[str]],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """必須フィールドが足りない DOI の応答は、不足を示すエラーで拒否される (12.7).

    Crossref の ``book`` の応答はタイトル・著者・年を持つが、出版社
    (``publisher``) を持たないので、不足する。
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


# --- DOI 経路の通信エラー (要件 6.1。旧 KB10) -------------------------------
# ``Works.doi`` (crossrefapi 1.6.0) は ``requests.get`` を直接呼び、接続の
# 失敗・タイムアウトは ``requests.exceptions.RequestException`` の派生、JSON
# でない応答は ``requests.exceptions.JSONDecodeError``、JSON に ``message`` が
# ない応答は ``KeyError`` を送出する。404 だけが ``None`` を返す。外部
# ネットワークには出ない (conftest がソケットを遮断し、``requests.get`` も
# 差し替える)。

# 通信の例外を作る関数。例外は送出のたびに新しく作る。
COMMUNICATION_ERRORS = [
    pytest.param(
        lambda: requests.exceptions.ConnectionError("offline"),
        id="connection-error",
    ),
    pytest.param(
        lambda: requests.exceptions.ConnectTimeout("connect timed out"),
        id="connect-timeout",
    ),
    pytest.param(
        lambda: requests.exceptions.ReadTimeout("read timed out"),
        id="read-timeout",
    ),
    pytest.param(
        lambda: requests.exceptions.HTTPError("503 Server Error"),
        id="http-error",
    ),
    pytest.param(
        lambda: requests.exceptions.JSONDecodeError(
            "Expecting value", "<html>Bad Gateway</html>", 0
        ),
        id="response-is-not-json",
    ),
    pytest.param(
        lambda: requests.exceptions.SSLError("certificate verify failed"),
        id="ssl-error",
    ),
    pytest.param(
        lambda: requests.exceptions.ProxyError("cannot connect to proxy"),
        id="proxy-error",
    ),
    pytest.param(
        lambda: requests.exceptions.TooManyRedirects("redirect loop"),
        id="too-many-redirects",
    ),
    pytest.param(
        lambda: requests.exceptions.InvalidURL("invalid url"),
        id="invalid-url",
    ),
    pytest.param(lambda: KeyError("message"), id="response-without-message"),
]


def fail_doi_lookup(
    monkeypatch: pytest.MonkeyPatch,
    make_error: Callable[[], BaseException],
) -> list[str]:
    """``Works.doi`` を、呼ぶと ``make_error()`` の例外を送出する関数にする.

    Returns
    -------
    list[str]
        ``Works.doi`` が呼ばれるたびに、問い合わせた DOI が順に加わる一覧。
    """
    requested: list[str] = []

    def failing(self: Works, doi: str, only_message: bool = True) -> NoReturn:
        requested.append(doi)
        raise make_error()

    monkeypatch.setattr(Works, "doi", failing)
    return requested


def test_doi_lookup_connection_error_shows_error_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """通信の例外で DOI を取得できなくても、ページが落ちずにエラーが出る (6.1).

    ``fake_crossref`` は応答 (辞書または ``None``) しか返せないので、
    ``Works.doi`` を通信の例外を送出する関数に差し替える。エラーは 1 つで、
    「無効な DOI」とは区別でき、論文リストは変わらない。
    """
    requested = fail_doi_lookup(
        monkeypatch, lambda: requests.exceptions.ConnectionError("offline")
    )

    at = submit_doi(DOI)

    assert requested == [DOI]
    assert_rejected(at, data_paths.list_file, papers_before, "network")


@pytest.mark.parametrize("make_error", COMMUNICATION_ERRORS)
def test_doi_lookup_failure_shows_one_error_and_changes_nothing(
    make_error: Callable[[], BaseException],
    monkeypatch: pytest.MonkeyPatch,
    fake_file_uploader: Callable[..., None],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """通信の失敗では、エラーが 1 つ出て、論文リストも PDF も変わらない (6.1).

    PDF を添付していても保存されない。
    """
    fake_file_uploader(
        {PDF_UPLOAD_LABEL: [FakeUpload("attached.pdf", PDF_BYTES + b"%a\n")]}
    )
    requested = fail_doi_lookup(monkeypatch, make_error)
    pdfs_before = pdf_tree(data_paths.pdf)

    at = submit_doi(DOI)

    assert requested == [DOI]
    assert_rejected(at, data_paths.list_file, papers_before, "network")
    assert pdf_tree(data_paths.pdf) == pdfs_before


def test_doi_lookup_error_shows_only_the_type_of_the_exception(
    monkeypatch: pytest.MonkeyPatch,
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """画面には例外の型名だけを出し、例外の文面は出さない (6.1).

    例外の文面は入力した DOI やプロキシの URL を含みうる。画面の文面は
    Markdown として解釈されるので、型名など定型の語に限る。
    """
    fail_doi_lookup(
        monkeypatch,
        lambda: requests.exceptions.ConnectionError(
            "[click here](https://evil.example/x) http://user:secret@proxy"
        ),
    )

    at = submit_doi(DOI)

    assert_rejected(at, data_paths.list_file, papers_before, "network")
    message = error_messages(at)[0]
    assert "ConnectionError" in message
    for leaked in ("evil.example", "click here", "secret", "proxy"):
        assert leaked not in message, (leaked, message)


def test_doi_lookup_error_is_logged_as_an_error(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """通信エラーは、画面だけでなくログにも ERROR で 1 回出る (6.1)."""
    fail_doi_lookup(
        monkeypatch, lambda: requests.exceptions.ReadTimeout("read timed out")
    )

    with caplog.at_level("ERROR"):
        at = submit_doi(DOI)

    assert_rejected(at, data_paths.list_file, papers_before, "network")
    logged = [
        record.getMessage()
        for record in caplog.records
        if record.levelname == "ERROR"
    ]
    assert len(logged) == 1, logged
    assert "ReadTimeout" in logged[0]


@pytest.mark.parametrize(
    "make_error",
    [
        pytest.param(lambda: RuntimeError("bug"), id="runtime-error"),
        pytest.param(lambda: ValueError("bug"), id="value-error"),
        pytest.param(lambda: TypeError("bug"), id="type-error"),
        pytest.param(lambda: AttributeError("bug"), id="attribute-error"),
        pytest.param(lambda: IndexError("bug"), id="index-error"),
    ],
)
def test_unexpected_exception_from_doi_lookup_is_not_swallowed(
    make_error: Callable[[], BaseException],
    monkeypatch: pytest.MonkeyPatch,
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """通信の失敗でない例外 (バグ) は、エラー表示にすり替えずにそのまま出る (6.1).

    捕捉するのは ``requests.exceptions.RequestException`` の派生と、応答に
    ``message`` がないときの ``KeyError`` だけである。
    """
    fail_doi_lookup(monkeypatch, make_error)

    at = submit_doi(DOI)

    assert [e.proto.type for e in at.exception] == [
        type(make_error()).__name__
    ]
    assert not error_messages(at)
    assert not success_messages(at)
    assert stored_papers(data_paths.list_file) == papers_before


def test_doi_lookup_error_does_not_block_the_next_registration(
    monkeypatch: pytest.MonkeyPatch,
    fake_crossref: Callable[[Optional[CrossrefMetadata]], list[str]],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """通信エラーの後に取得できる状態で送信すると、登録される (6.1)."""
    fail_doi_lookup(
        monkeypatch, lambda: requests.exceptions.ConnectionError("offline")
    )
    failed = submit_doi(DOI)
    assert_rejected(failed, data_paths.list_file, papers_before, "network")

    fake_crossref(crossref_article(DOI))
    at = submit_doi(DOI)

    assert_registered(
        at,
        data_paths.list_file,
        papers_before,
        {EXPECTED_DOI_KEY: EXPECTED_DOI_ENTRY},
    )


class SentRequest(NamedTuple):
    """差し替えた ``requests.get`` が受け取った呼び出し."""

    url: str
    kwargs: dict[str, Any]


def fake_http_get(
    monkeypatch: pytest.MonkeyPatch, status_code: int, body: bytes
) -> list[SentRequest]:
    """``requests.get`` を、固定のステータスと本文の応答を返す関数にする.

    crossrefapi の ``Works.doi`` は差し替えず、HTTP の応答から先を本物の
    crossrefapi で処理させる (外部ネットワークには出ない)。

    Returns
    -------
    list[SentRequest]
        ``requests.get`` が呼ばれるたびに、その呼び出しが順に加わる一覧。
    """
    sent: list[SentRequest] = []

    def get(url: str, **kwargs: Any) -> requests.Response:
        sent.append(SentRequest(url, kwargs))
        response = requests.Response()
        response.status_code = status_code
        response._content = body
        response.encoding = "utf-8"
        response.url = url
        return response

    monkeypatch.setattr(requests, "get", get)
    return sent


def crossref_body(message: CrossrefMetadata) -> bytes:
    """Crossref の REST API が返す JSON (``message`` に本体を持つ) を作る."""
    return json.dumps({"status": "ok", "message": message}).encode()


@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        pytest.param(200, b"<html>captive portal</html>", id="html-200"),
        pytest.param(503, b"<html>Service Unavailable</html>", id="html-503"),
        pytest.param(200, b"", id="empty-body"),
        pytest.param(200, b'{"status": "failed"}', id="json-without-message"),
    ],
)
def test_malformed_crossref_response_shows_communication_error(
    status_code: int,
    body: bytes,
    monkeypatch: pytest.MonkeyPatch,
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """JSON でない応答や ``message`` のない応答は、通信エラーになる (6.1).

    crossrefapi が実際に送出する ``requests.exceptions.JSONDecodeError`` と
    ``KeyError`` を、HTTP の応答から作って確かめる。
    """
    sent = fake_http_get(monkeypatch, status_code, body)

    at = submit_doi(DOI)

    assert len(sent) == 1
    assert_rejected(at, data_paths.list_file, papers_before, "network")


def test_crossref_not_found_response_is_an_invalid_doi(
    monkeypatch: pytest.MonkeyPatch,
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """404 は通信エラーでなく、これまでどおり「無効な DOI」になる (6.1)."""
    fake_http_get(monkeypatch, 404, b"Resource not found.")

    at = submit_doi(DOI)

    assert_rejected(at, data_paths.list_file, papers_before, "fetch_failed")


def test_doi_registers_through_the_crossref_client(
    monkeypatch: pytest.MonkeyPatch,
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """HTTP の応答から先を本物の crossrefapi で処理しても、登録できる (6.1)."""
    sent = fake_http_get(
        monkeypatch, 200, crossref_body(crossref_article(DOI))
    )

    at = submit_doi(DOI)

    assert len(sent) == 1
    assert sent[0].url == f"https://api.crossref.org/works/{DOI}"
    assert_registered(
        at,
        data_paths.list_file,
        papers_before,
        {EXPECTED_DOI_KEY: EXPECTED_DOI_ENTRY},
    )


def test_doi_request_sends_nothing_personal_to_crossref(
    monkeypatch: pytest.MonkeyPatch,
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """Crossref への要求は、DOI のほかに利用者の情報を含まない (6.1).

    問い合わせ先 (``mailto``) は crossrefapi の既定の匿名のままで、利用者の
    メールアドレスや環境のユーザー情報を送らない。
    """
    personal = {
        "EMAIL": "taro.yamada@personal.example",
        "GIT_AUTHOR_EMAIL": "taro.yamada@personal.example",
        "USER": "taro-yamada-login",
        "LOGNAME": "taro-yamada-login",
    }
    for name, value in personal.items():
        monkeypatch.setenv(name, value)
    sent = fake_http_get(
        monkeypatch, 200, crossref_body(crossref_article(DOI))
    )

    at = submit_doi(DOI)

    assert_registered(
        at,
        data_paths.list_file,
        papers_before,
        {EXPECTED_DOI_KEY: EXPECTED_DOI_ENTRY},
    )
    assert len(sent) == 1
    request = sent[0]
    assert request.kwargs["params"] == {}
    headers = request.kwargs["headers"]
    assert set(headers) == {"user-agent"}
    assert "mailto:anonymous" in headers["user-agent"]
    sent_text = repr(request)
    for value in personal.values():
        assert value not in sent_text, (value, sent_text)


@pytest.mark.parametrize(
    "response",
    [
        pytest.param([], id="empty-list"),
        pytest.param(["10.5555/x"], id="list"),
        pytest.param("10.5555/x", id="string"),
        pytest.param({"type": "journal-article"}, id="no-doi"),
        pytest.param({"DOI": "", "type": "journal-article"}, id="empty-doi"),
    ],
)
def test_response_without_usable_metadata_is_an_invalid_doi(
    response: Any,
    monkeypatch: pytest.MonkeyPatch,
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """辞書でない応答と ``DOI`` のない応答は、「無効な DOI」になる (6.1)."""

    def doi_returning(self: Works, doi: str, only_message: bool = True) -> Any:
        return response

    monkeypatch.setattr(Works, "doi", doi_returning)

    at = submit_doi(DOI)

    assert_rejected(at, data_paths.list_file, papers_before, "fetch_failed")


# --- Crossref の応答の取り込み (要件 6.2, 6.3, 6.4) ------------------------

# ``response_with`` の変更で、キーごと取り除くことを表す。
REMOVED = object()


def response_with(
    changes: Mapping[str, Any],
    base: Optional[CrossrefMetadata] = None,
) -> CrossrefMetadata:
    """``base`` (既定は ``crossref_article(DOI)``) に ``changes`` を適用する.

    値が ``REMOVED`` のキーは取り除き、それ以外は上書きまたは追加する。
    """
    response = dict(crossref_article(DOI) if base is None else base)
    for name, value in changes.items():
        if value is REMOVED:
            response.pop(name, None)
        else:
            response[name] = value
    return response


MISSING_FIELD_CASES = [
    pytest.param(
        {"container-title": REMOVED},
        {"author", "title", "year"},
        id="no-container-title",
    ),
    pytest.param(
        {"container-title": []},
        {"author", "title", "year"},
        id="empty-container-title",
    ),
    pytest.param(
        {"container-title": ["", "  "]},
        {"author", "title", "year"},
        id="blank-container-title",
    ),
    pytest.param(
        {"author": REMOVED},
        {"title", "journal", "year"},
        id="no-author",
    ),
    pytest.param(
        {"author": []},
        {"title", "journal", "year"},
        id="empty-author-list",
    ),
    pytest.param(
        {"author": [{}, {"given": "", "family": " "}, {"affiliation": []}]},
        {"title", "journal", "year"},
        id="authors-without-names",
    ),
    pytest.param(
        {"published": REMOVED},
        {"author", "title", "journal"},
        id="no-date",
    ),
    pytest.param(
        {"published": {}},
        {"author", "title", "journal"},
        id="empty-date",
    ),
    pytest.param(
        {"published": {"date-parts": [[None]]}},
        {"author", "title", "journal"},
        id="date-parts-with-null",
    ),
    pytest.param(
        {"published": {"date-parts": [[]]}},
        {"author", "title", "journal"},
        id="date-parts-empty-inner",
    ),
    pytest.param(
        {"published": {"date-parts": []}},
        {"author", "title", "journal"},
        id="date-parts-empty",
    ),
    pytest.param(
        {"published": {"date-parts": "2021"}},
        {"author", "title", "journal"},
        id="date-parts-not-a-list",
    ),
    pytest.param(
        {"published": REMOVED, "created": {"date-parts": [[2021, 5, 17]]}},
        {"author", "title", "journal"},
        id="created-is-not-a-publication-date",
    ),
    pytest.param(
        {"title": REMOVED},
        {"author", "journal", "year"},
        id="no-title",
    ),
    pytest.param(
        {"title": []},
        {"author", "journal", "year"},
        id="empty-title",
    ),
    pytest.param(
        {"title": ["  "]},
        {"author", "journal", "year"},
        id="blank-title",
    ),
    pytest.param(
        {
            "container-title": REMOVED,
            "author": REMOVED,
            "published": REMOVED,
        },
        {"title"},
        id="journal-author-and-year-missing",
    ),
    pytest.param(
        {
            "container-title": REMOVED,
            "author": REMOVED,
            "published": REMOVED,
            "title": REMOVED,
        },
        set(),
        id="only-type-and-doi",
    ),
]


@pytest.mark.parametrize(("changes", "given"), MISSING_FIELD_CASES)
def test_doi_response_with_missing_fields_is_rejected_naming_them(
    changes: Mapping[str, Any],
    given: set[str],
    fake_crossref: Callable[[Optional[CrossrefMetadata]], list[str]],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """項目が欠けた応答は、ページが落ちずに不足した必須項目名のエラーになる (6.4).

    ``journal-article`` の必須項目 (著者・タイトル・掲載誌名・年) のうち
    欠けたものだけが、エラーに現れる。空のリストや空の文字列は、欠けている
    ものとして扱う (空の値を保存して必須項目の判定を通さない)。
    """
    requested = fake_crossref(response_with(changes))

    at = submit_doi(DOI)

    assert requested == [DOI]
    assert_rejected_for_missing(
        at, data_paths.list_file, papers_before, "article", given
    )


def test_doi_response_without_optional_fields_leaves_them_out(
    fake_crossref: Callable[[Optional[CrossrefMetadata]], list[str]],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """巻・号・ページが応答にないとき、それらのキーを作らず登録する (6.4).

    空の値は保存されない (空と未設定を区別なく扱う比較はしない)。空の
    ``volume``・``issue``・``page`` も同様に省く。
    """
    fake_crossref(response_with({"volume": "", "issue": "  ", "page": None}))

    at = submit_doi(DOI)

    assert_registered(
        at,
        data_paths.list_file,
        papers_before,
        {EXPECTED_DOI_KEY: EXPECTED_DOI_ENTRY},
    )


# 書籍の応答。``container-title`` はシリーズ名で、掲載誌名ではない。
BOOK_RESPONSE: CrossrefMetadata = {
    "type": "book",
    "DOI": DOI,
    "title": ["A Fixed Book"],
    "author": [{"given": "Jane", "family": "Doe"}],
    "publisher": "Fixed University Press",
    "container-title": ["Fixed Series"],
    "issued": {"date-parts": [[2018, 1, 1]]},
}
# BOOK_RESPONSE から導いた、登録後のエントリ。出版社を持ち、シリーズ名を
# 掲載誌名 (journal) として保存しない。
EXPECTED_BOOK_ENTRY = {
    "ENTRYTYPE": "book",
    "ID": "JaneDoe2018_0",
    "author": "Jane Doe",
    "title": "A Fixed Book",
    "publisher": "Fixed University Press",
    "year": "2018",
    "DOI": DOI,
}


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({}, id="with-series-name"),
        pytest.param({"container-title": []}, id="empty-container-title"),
        pytest.param({"container-title": REMOVED}, id="no-container-title"),
    ],
)
def test_book_doi_registers_entry_with_publisher(
    changes: Mapping[str, Any],
    fake_crossref: Callable[[Optional[CrossrefMetadata]], list[str]],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """出版社付きの書籍の DOI は、出版社を含むエントリとして登録される (6.2).

    書籍の ``container-title`` (シリーズ名。空のリストのこともある) は
    掲載誌名にしない。
    """
    fake_crossref(response_with(changes, BOOK_RESPONSE))

    at = submit_doi(DOI)

    assert_registered(
        at,
        data_paths.list_file,
        papers_before,
        {"JaneDoe2018_0": EXPECTED_BOOK_ENTRY},
    )


def test_book_doi_without_publisher_is_rejected_naming_it(
    fake_crossref: Callable[[Optional[CrossrefMetadata]], list[str]],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """出版社のない書籍の応答は、``publisher`` の不足を示すエラーになる (6.2)."""
    fake_crossref(response_with({"publisher": REMOVED}, BOOK_RESPONSE))

    at = submit_doi(DOI)

    assert_rejected_for_missing(
        at,
        data_paths.list_file,
        papers_before,
        "book",
        {"author", "title", "year"},
    )


# 会議録の論文の応答。``container-title`` が会議録名、``event.name`` が会議名。
PROCEEDINGS_RESPONSE: CrossrefMetadata = {
    "type": "proceedings-article",
    "DOI": DOI,
    "title": ["A Fixed Conference Paper"],
    "author": [{"given": "Jane", "family": "Doe"}],
    "container-title": ["Proceedings of the Fixed Conference"],
    "event": {"name": "Fixed Conference 2019"},
    "publisher": "Fixed Proceedings Press",
    "published": {"date-parts": [[2019, 6]]},
    "page": "11-20",
}
# PROCEEDINGS_RESPONSE から導いた、登録後のエントリ。会議録名を booktitle に
# 持ち、掲載誌名 (journal) は持たない。
EXPECTED_PROCEEDINGS_ENTRY = {
    "ENTRYTYPE": "inproceedings",
    "ID": "JaneDoe2019_0",
    "author": "Jane Doe",
    "title": "A Fixed Conference Paper",
    "booktitle": "Proceedings of the Fixed Conference",
    "publisher": "Fixed Proceedings Press",
    "year": "2019",
    "pages": "11-20",
    "DOI": DOI,
}


def test_proceedings_doi_registers_entry_with_proceedings_title(
    fake_crossref: Callable[[Optional[CrossrefMetadata]], list[str]],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """会議録名付きの会議録の DOI は、会議録名を含むエントリとして登録される (6.3)."""
    fake_crossref(PROCEEDINGS_RESPONSE)

    at = submit_doi(DOI)

    assert_registered(
        at,
        data_paths.list_file,
        papers_before,
        {"JaneDoe2019_0": EXPECTED_PROCEEDINGS_ENTRY},
    )


def test_proceedings_doi_without_publisher_registers_without_it(
    fake_crossref: Callable[[Optional[CrossrefMetadata]], list[str]],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """出版社は会議録の任意項目なので、応答になければ作らずに登録する (6.3)."""
    fake_crossref(response_with({"publisher": REMOVED}, PROCEEDINGS_RESPONSE))
    expected = {
        name: value
        for name, value in EXPECTED_PROCEEDINGS_ENTRY.items()
        if name != "publisher"
    }

    at = submit_doi(DOI)

    assert_registered(
        at, data_paths.list_file, papers_before, {"JaneDoe2019_0": expected}
    )


@pytest.mark.parametrize(
    "container_title",
    [
        pytest.param(REMOVED, id="no-container-title"),
        pytest.param([], id="empty-container-title"),
    ],
)
def test_proceedings_doi_without_container_title_uses_the_event_name(
    container_title: Any,
    fake_crossref: Callable[[Optional[CrossrefMetadata]], list[str]],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """``container-title`` がないときは、会議名 (``event.name``) を使う (6.3)."""
    fake_crossref(
        response_with(
            {"container-title": container_title}, PROCEEDINGS_RESPONSE
        )
    )
    expected = {
        **EXPECTED_PROCEEDINGS_ENTRY,
        "booktitle": "Fixed Conference 2019",
    }

    at = submit_doi(DOI)

    assert_registered(
        at, data_paths.list_file, papers_before, {"JaneDoe2019_0": expected}
    )


@pytest.mark.parametrize(
    "event",
    [
        pytest.param(REMOVED, id="no-event"),
        pytest.param({}, id="event-without-name"),
        pytest.param({"name": " "}, id="blank-event-name"),
    ],
)
def test_proceedings_doi_without_any_title_is_rejected_naming_booktitle(
    event: Any,
    fake_crossref: Callable[[Optional[CrossrefMetadata]], list[str]],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """会議録名も会議名もない応答は、``booktitle`` の不足を示すエラーになる (6.3)."""
    fake_crossref(
        response_with(
            {"container-title": REMOVED, "event": event},
            PROCEEDINGS_RESPONSE,
        )
    )

    at = submit_doi(DOI)

    assert_rejected_for_missing(
        at,
        data_paths.list_file,
        papers_before,
        "inproceedings",
        {"author", "title", "year"},
    )


# 出版年の候補。issued → published-print → published-online → published の
# 順に探し、年が数値のものを使う。
FIRST_DATES = {
    "issued": {"date-parts": [[2017, 3]]},
    "published-print": {"date-parts": [[2018, 4]]},
    "published-online": {"date-parts": [[2019, 5]]},
    "published": {"date-parts": [[2020, 6]]},
}


@pytest.mark.parametrize(
    ("dates", "year"),
    [
        pytest.param({"issued": FIRST_DATES["issued"]}, "2017", id="issued"),
        pytest.param(
            {"published-print": FIRST_DATES["published-print"]},
            "2018",
            id="published-print",
        ),
        pytest.param(
            {"published-online": FIRST_DATES["published-online"]},
            "2019",
            id="published-online",
        ),
        pytest.param(
            {"published": FIRST_DATES["published"]}, "2020", id="published"
        ),
        pytest.param(dict(FIRST_DATES), "2017", id="all-four-issued-first"),
        pytest.param(
            {k: v for k, v in FIRST_DATES.items() if k != "issued"},
            "2018",
            id="print-before-online-and-published",
        ),
        pytest.param(
            {
                k: v
                for k, v in FIRST_DATES.items()
                if k in ("published-online", "published")
            },
            "2019",
            id="online-before-published",
        ),
        pytest.param(
            {
                "issued": {"date-parts": [[None]]},
                "published-print": FIRST_DATES["published-print"],
            },
            "2018",
            id="skips-issued-with-null-year",
        ),
        pytest.param(
            {
                "issued": {"date-parts": [[]]},
                "published-online": FIRST_DATES["published-online"],
            },
            "2019",
            id="skips-issued-without-year",
        ),
        pytest.param(
            {"issued": {}, "published": FIRST_DATES["published"]},
            "2020",
            id="skips-issued-without-date-parts",
        ),
    ],
)
def test_doi_year_comes_from_the_first_usable_publication_date(
    dates: Mapping[str, Any],
    year: str,
    fake_crossref: Callable[[Optional[CrossrefMetadata]], list[str]],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """年は出版日の候補を順に探し、最初に見つかった年を使う (6.4)."""
    fake_crossref(response_with({"published": REMOVED, **dates}))
    key = f"JaneDoe{year}_0"

    at = submit_doi(DOI)

    assert_registered(
        at,
        data_paths.list_file,
        papers_before,
        {key: {**EXPECTED_DOI_ENTRY, "ID": key, "year": year}},
    )


@pytest.mark.parametrize(
    ("authors", "author", "first_author"),
    [
        pytest.param(
            [
                {"given": "Jane", "family": "Doe"},
                {"name": "Fixed Research Consortium"},
            ],
            "Jane Doe and Fixed Research Consortium",
            "JaneDoe",
            id="person-and-organization",
        ),
        pytest.param(
            [{"name": "Fixed Research Consortium"}],
            "Fixed Research Consortium",
            "FixedResearchConsortium",
            id="organization-only",
        ),
        pytest.param(
            [{"family": "Roe"}, {"given": "Jane", "family": "Doe"}],
            "Roe and Jane Doe",
            "Roe",
            id="family-name-only",
        ),
        pytest.param(
            [{"given": "Cher"}],
            "Cher",
            "Cher",
            id="given-name-only",
        ),
        pytest.param(
            [
                {"given": "Jane", "family": "Doe"},
                {},
                {"given": "", "family": ""},
                {"given": "John", "family": "Roe"},
            ],
            "Jane Doe and John Roe",
            "JaneDoe",
            id="authors-without-names-are-dropped",
        ),
    ],
)
def test_doi_authors_include_organizations_and_partial_names(
    authors: list[dict[str, str]],
    author: str,
    first_author: str,
    fake_crossref: Callable[[Optional[CrossrefMetadata]], list[str]],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """著者は ``given family`` → ``family`` → ``name`` の順に名前を作る (6.4).

    団体著者 (``given``/``family`` がなく ``name`` がある) の名前も取り込み、
    名前のない要素は省く。
    """
    fake_crossref(response_with({"author": authors}))
    key = f"{first_author}2021_0"

    at = submit_doi(DOI)

    assert_registered(
        at,
        data_paths.list_file,
        papers_before,
        {key: {**EXPECTED_DOI_ENTRY, "ID": key, "author": author}},
    )


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"type": "posted-content"}, id="posted-content"),
        pytest.param({"type": "dissertation"}, id="dissertation"),
        pytest.param({"type": "book-chapter"}, id="book-chapter"),
        pytest.param({"type": "proceedings"}, id="proceedings"),
        pytest.param({"type": REMOVED}, id="no-type"),
        pytest.param({"type": None}, id="null-type"),
        pytest.param({"type": ["book"]}, id="type-is-a-list"),
    ],
)
def test_doi_of_other_types_registers_as_misc(
    changes: Mapping[str, Any],
    fake_crossref: Callable[[Optional[CrossrefMetadata]], list[str]],
    data_paths: DataPaths,
    papers_before: PaperDicts,
) -> None:
    """変換表にない種類や ``type`` のない応答は、``misc`` として登録される (6.4).

    ``misc`` には必須項目がなく、画面は落ちない (``fields.json`` にない
    エントリタイプは作られない)。
    """
    fake_crossref(response_with(changes))

    at = submit_doi(DOI)

    assert_registered(
        at,
        data_paths.list_file,
        papers_before,
        {EXPECTED_DOI_KEY: {**EXPECTED_DOI_ENTRY, "ENTRYTYPE": "misc"}},
    )


# 形が想定と異なる項目を含む応答。どれも例外にならず、値のない項目は
# 省かれる。
IRREGULAR_RESPONSES = [
    pytest.param({}, id="type-only"),
    pytest.param({"DOI": DOI}, id="doi-only"),
    pytest.param({"title": None, "author": None}, id="null-title-author"),
    pytest.param({"title": "Plain Title"}, id="title-is-a-string"),
    pytest.param({"title": [None, 3, ["x"]]}, id="title-items-not-strings"),
    pytest.param({"author": "Jane Doe"}, id="author-is-a-string"),
    pytest.param(
        {"author": [None, "Jane Doe", 3, ["x"], {"given": 1, "family": [2]}]},
        id="author-items-not-objects",
    ),
    pytest.param({"container-title": "Plain Journal"}, id="journal-string"),
    pytest.param({"container-title": [None, 3]}, id="journal-not-strings"),
    pytest.param({"published": []}, id="date-is-a-list"),
    pytest.param({"issued": {"date-parts": None}}, id="date-parts-null"),
    pytest.param({"issued": {"date-parts": [None]}}, id="date-parts-inner"),
    pytest.param(
        {"issued": {"date-parts": [[True]]}}, id="date-parts-boolean"
    ),
    pytest.param(
        {"issued": {"date-parts": [["2021"]]}}, id="date-parts-string-year"
    ),
    pytest.param({"event": "Fixed Conference"}, id="event-is-a-string"),
    pytest.param({"event": {"name": None}}, id="event-name-null"),
    pytest.param(
        {"volume": None, "issue": [], "page": {}}, id="optional-not-strings"
    ),
]


@pytest.mark.parametrize("entry_type", ["article", "book", "inproceedings"])
@pytest.mark.parametrize("metadata", IRREGULAR_RESPONSES)
def test_entry_from_irregular_response_has_no_blank_value(
    metadata: CrossrefMetadata, entry_type: str
) -> None:
    """どんな形の応答でも例外にならず、空の値のない、定義済みの種類のエントリになる (6.4).

    必須項目の判定は呼び出し側の検証に任せる。ここでは、空の値を作らない
    (空の値は必須項目の判定を通ってしまう) ことと、``fields.json`` に
    あるエントリタイプを作ることを確かめる。
    """
    crossref_type = {
        "article": "journal-article",
        "book": "book",
        "inproceedings": "proceedings-article",
    }[entry_type]

    entry = _create_entry_from_doi_metadata(
        {**metadata, "type": crossref_type}
    )

    assert entry["ENTRYTYPE"] in MAP_FIELDS
    assert all(value.strip() == value and value for value in entry.values())
