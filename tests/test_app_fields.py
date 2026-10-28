"""フィールド定義とエントリタイプの変換 (``app.helper._config``) の単体テスト.

対象は次のとおり。

- 8 種類のエントリタイプ (article, book, inbook, inproceedings,
  masterthesis, phdthesis, techreport, misc) のフィールド定義が
  ``MAP_FIELDS`` / ``MAP_REQUIRED_FIELDS`` に読み込まれ、各タイプの必須
  フィールドの集合が定義ファイル (``fields.json``) の ``required`` 指定と
  一致すること。``misc`` は必須フィールドを持たない (要件 8.6)
- Crossref のエントリタイプが ``journal-article`` → ``article``、
  ``proceedings-article`` → ``inproceedings``、``book`` → ``book`` に
  変換され、それ以外は ``misc`` になること (要件 8.7)

期待値は、実装の読み込み処理を再現せずに、定義ファイルの内容そのものから
導く。定義ファイルのパスはパッケージの場所から導出し、``json.load`` で
独立に読み込む。``entrytype4doi`` は Streamlit を呼ばず、ログを出すだけの
関数なので、画面を使わずに直接呼び出して検証する。
"""

import json
from pathlib import Path
from typing import Any

import pytest

import paper_manager
from paper_manager import _constants
from paper_manager.app.helper import (
    MAP_FIELDS,
    MAP_REQUIRED_FIELDS,
    entrytype4doi,
)

# 要件 8.6 が挙げる 8 種類のエントリタイプ。
_ENTRY_TYPES = (
    "article",
    "book",
    "inbook",
    "inproceedings",
    "masterthesis",
    "phdthesis",
    "techreport",
    "misc",
)
_ENTRY_TYPES_WITH_REQUIRED_FIELDS = tuple(
    entry_type for entry_type in _ENTRY_TYPES if entry_type != "misc"
)

# 定義ファイルはパッケージの場所から導出する (実装が使う定数は使わない)。
_FIELDS_JSON = Path(paper_manager.__file__).parent / "app" / "fields.json"

# 要件 8.7 が挙げる Crossref のタイプと変換先。
_CONVERTED_CROSSREF_TYPES = (
    ("journal-article", "article"),
    ("proceedings-article", "inproceedings"),
    ("book", "book"),
)
# 上記以外のタイプ。BibTeX に対応しうる書籍の章・学位論文・報告書も含め、
# 要件どおりすべて misc になる。
_OTHER_CROSSREF_TYPES = (
    "book-chapter",
    "dissertation",
    "report",
    "monograph",
    "proceedings",
    "dataset",
    "posted-content",
    "unknown-type",
    "",
)


@pytest.fixture(scope="module")
def fields_json() -> dict[str, dict[str, dict[str, Any]]]:
    """定義ファイルを ``json.load`` で独立に読み込んだ内容を返す.

    Returns
    -------
    dict[str, dict[str, dict[str, Any]]]
        エントリタイプ → フィールド名 → 属性 (``required``) の辞書。
    """
    assert _FIELDS_JSON.is_file(), f"definition file not found: {_FIELDS_JSON}"
    with open(_FIELDS_JSON, encoding="utf-8") as f:
        return json.load(f)


def _required_names(definition: dict[str, dict[str, Any]]) -> set[str]:
    """1 つのエントリタイプの定義から、必須フラグが立つ名前を集める."""
    return {
        name
        for name, attributes in definition.items()
        if attributes["required"] is True
    }


# --- 要件 8.6: フィールド定義 -----------------------------------------------


def test_independent_path_is_the_file_the_app_loads() -> None:
    """独立に読む定義ファイルは、アプリが読み込むファイルと同じである.

    別のファイルと照合して偶然一致する、という空振りを防ぐ前提の検査。
    """
    assert _FIELDS_JSON.resolve() == _constants.FILEPATH_FIELDS.resolve()


def test_definition_file_has_exactly_the_eight_entry_types(
    fields_json: dict[str, dict[str, dict[str, Any]]],
) -> None:
    """定義ファイルのエントリタイプは、要件の 8 種類と一致する."""
    assert set(fields_json) == set(_ENTRY_TYPES)
    assert len(fields_json) == 8


def test_all_eight_entry_types_are_loaded() -> None:
    """8 種類のエントリタイプが ``MAP_FIELDS`` と必須一覧に読み込まれる."""
    assert set(MAP_FIELDS) == set(_ENTRY_TYPES)
    assert len(MAP_FIELDS) == 8
    assert set(MAP_REQUIRED_FIELDS) == set(_ENTRY_TYPES)
    assert len(MAP_REQUIRED_FIELDS) == 8


@pytest.mark.parametrize("entry_type", _ENTRY_TYPES)
def test_loaded_field_definitions_match_definition_file(
    entry_type: str,
    fields_json: dict[str, dict[str, dict[str, Any]]],
) -> None:
    """読み込まれたフィールド定義が、定義ファイルの内容と一致する."""
    assert dict(MAP_FIELDS[entry_type]) == fields_json[entry_type]


@pytest.mark.parametrize("entry_type", _ENTRY_TYPES)
def test_required_fields_match_required_flags_in_definition_file(
    entry_type: str,
    fields_json: dict[str, dict[str, dict[str, Any]]],
) -> None:
    """必須フィールドの集合が、定義ファイルの ``required`` 指定と一致する."""
    expected = _required_names(fields_json[entry_type])

    assert MAP_REQUIRED_FIELDS[entry_type] == expected


@pytest.mark.parametrize("entry_type", _ENTRY_TYPES_WITH_REQUIRED_FIELDS)
def test_types_other_than_misc_have_required_fields(
    entry_type: str,
    fields_json: dict[str, dict[str, dict[str, Any]]],
) -> None:
    """``misc`` 以外のタイプは必須フィールドを持つ.

    定義ファイルと実装の両方が空集合で、一致の検証が自明に成立する
    空振りを防ぐ。
    """
    assert _required_names(fields_json[entry_type])
    assert MAP_REQUIRED_FIELDS[entry_type]


def test_misc_has_no_required_fields(
    fields_json: dict[str, dict[str, dict[str, Any]]],
) -> None:
    """``misc`` は必須フィールドを持たない."""
    assert _required_names(fields_json["misc"]) == set()
    assert MAP_REQUIRED_FIELDS["misc"] == set()
    # 必須でないだけで、入力できるフィールド自体は定義されている。
    assert MAP_FIELDS["misc"]


# --- 要件 8.7: Crossref のエントリタイプの変換 -----------------------------


@pytest.mark.parametrize(
    ("crossref_type", "entry_type"), _CONVERTED_CROSSREF_TYPES
)
def test_known_crossref_types_are_converted(
    crossref_type: str, entry_type: str
) -> None:
    """Crossref のタイプが、対応する BibTeX のエントリタイプに変換される."""
    assert entrytype4doi(crossref_type) == entry_type


@pytest.mark.parametrize("crossref_type", _OTHER_CROSSREF_TYPES)
def test_other_crossref_types_become_misc(crossref_type: str) -> None:
    """変換表に無い Crossref のタイプは、例外にならず ``misc`` になる."""
    assert entrytype4doi(crossref_type) == "misc"


@pytest.mark.parametrize(
    "crossref_type",
    [crossref for crossref, _ in _CONVERTED_CROSSREF_TYPES]
    + list(_OTHER_CROSSREF_TYPES),
)
def test_converted_type_is_a_defined_entry_type(crossref_type: str) -> None:
    """変換結果は、必ずフィールド定義のあるエントリタイプである.

    登録ページは変換結果で ``MAP_REQUIRED_FIELDS`` を引くため、定義の
    ないタイプが返ると ``KeyError`` になる。
    """
    converted = entrytype4doi(crossref_type)

    assert converted in MAP_FIELDS
    assert converted in MAP_REQUIRED_FIELDS
