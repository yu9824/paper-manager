# Project Structure

## Organization Philosophy

src レイアウトの単一パッケージ `paper_manager`。**ドメイン層（UI 非依存）** と **Streamlit アプリ層（`app/`）** に分け、アプリ層の中でさらに「ページ」「再利用 UI 部品」「UI 非依存のアプリ用ロジック」に分ける。

依存方向は `app/pages` → `app/components` → `app/helper` → ドメイン層（`entry`, `bib`, `helper`, `logging`, `_constants`）。ドメイン層から `app/` を import しない。

## Directory Patterns

### Domain Subpackages
**Location**: `src/paper_manager/<name>/`（`entry`, `bib`, `helper`, `logging`）
**Purpose**: UI に依存しないデータモデルとユーティリティ
**Example**: `entry/_entry.py` に `Entry` / `PaperList`、`entry/__init__.py` がそれらを再エクスポート

### Constants
**Location**: `src/paper_manager/_constants.py`
**Purpose**: パス（`DIRPATH_*`, `FILEPATH_*`）、列名（`COLNAME_*`）、区切り文字、エンコーディング。マジック文字列はここに集約する

### App Entry Page
**Location**: `src/paper_manager/app/_list.py`
**Purpose**: `streamlit run` の起点となるトップページ（一覧・詳細・編集・削除・エクスポート）

### Pages
**Location**: `src/paper_manager/app/pages/_*.py`
**Purpose**: 各ページは `@config_page` を付けた `main()` を持ち、画面描画は `_render_*` のプライベート関数に分割する。ナビゲーションは `config_page` 内の `st.page_link` で明示的に定義する（自動サイドバーナビは無効化）
**Example**: `pages/_register.py` の `_render_bib_form()` / `_render_doi_form()` / `main()`

### App Components
**Location**: `src/paper_manager/app/components/`
**Purpose**: 複数ページで共有する UI 部品と、登録・更新・PDF 保存/削除など **session state やファイルを伴う操作**

### App Helpers
**Location**: `src/paper_manager/app/helper/`
**Purpose**: アプリ用だが UI 描画を持たないロジック（バックアップ zip、プロキシ設定、孤立 PDF 検出、フィールド定義）。CLI からも再利用される（`__main__.py` が `_backup` を使う）

### Package Data
**Location**: `src/paper_manager/app/fields.json`
**Purpose**: エントリタイプごとのフィールドと必須フラグの定義。`MAP_FIELDS` / `MAP_REQUIRED_FIELDS` として読み込まれる。フィールド追加はコードではなくこの JSON で行う

### CLI
**Location**: `src/paper_manager/__main__.py`
**Purpose**: argparse のサブコマンド（`run` / `version` / `backup` / `restore`）。`run` / `version` は未知の引数をそのまま Streamlit に渡す

### Tests / Docs / Notes
- `tests/test_*.py`: フラット配置の pytest
- `docs_src/`: Sphinx ソース（guides, tutorials）。ノートブックは jupytext で `.ipynb` と `.py:percent` をペアリング
- `_notes/`: メンテナ向けメモ（リリース手順など）

## Naming Conventions

- **Modules**: 実装モジュールは `_` 始まりの snake_case（`_entry.py`, `_backup.py`）。公開 API はサブパッケージの `__init__.py` で再エクスポートする
- **Classes**: PascalCase（`Entry`, `PaperList`）。ただしデコレータ的に使うクラスや tqdm 互換ダミーは小文字（`config_page`, `dummy_tqdm`）
- **Functions**: snake_case。ページ内描画関数は `_render_*`、モジュール内部は `_` 接頭辞
- **Constants**: UPPER_SNAKE_CASE。用途で接頭辞を揃える（`DIRPATH_`, `FILEPATH_`, `COLNAME_`, `MAP_`）
- **Logger**: 各モジュールで `_logger`

## Import Organization

```python
# パッケージ内は絶対 import が基本
from paper_manager._constants import COLNAME_AUTHOR, FILEPATH_LIST
from paper_manager.entry import Entry, PaperList
from paper_manager.logging import get_child_logger

# 相対 import はサブパッケージ __init__.py の再エクスポートのみ
from ._entry import Entry, PaperList

__all__ = ("Entry", "PaperList")  # __all__ はタプル
```

- 外部から使う API は再エクスポート経由（`paper_manager.entry`）で import し、`_module` を直接参照するのは同一パッケージ内または `app/helper/_backup` のような意図的な内部共有に限る

## Code Organization Principles

- **永続化の単一経路**: 論文リストの保存は `save_paper_list()`（session state とファイルの両方）または `config_page` の後処理を通す。ページから直接 `list.json` を書かない
- **PDF パスは Entry から導出する**: `entry.get_pdf_dir()` / `get_pdf_files()` を使い、パスを手組みしない。`base_dir` 引数で保存先を差し替えられる設計を保つ
- **後方互換コードは明示する**: 旧形式対応は `@deprecated` を付けるか、コメントで「後方互換性」と明記する
- **docstring・コメント**: numpy スタイル。説明文は日本語・英語が混在してよい。UI 文言は日本語

---
_Document patterns, not file trees. New files following patterns shouldn't require updates_
