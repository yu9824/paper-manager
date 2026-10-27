# Technology Stack

## Architecture

Streamlit のマルチページアプリを、`streamlit run` をラップした CLI（`paper-manager`）から起動する構成。データ永続化はファイルシステムのみ（JSON + PDF ディレクトリ）で、DB やサーバー側コンポーネントは持たない。

- UI 層（`app/`）は Streamlit に依存し、ドメイン層（`entry`, `bib`, `helper`, `logging`）はできるだけ UI に依存しない
- 例外: `PaperList` は session state との同期メソッド（`from_session_state` / `to_session_state`）を持つため `streamlit` を import している
- 各ページは `@config_page` で包まれ、ページ実行前に `list.json` → session state を読み込み、実行後に session state → `list.json` を書き戻す

## Core Technologies

- **Language**: Python（`requires-python >= 3.9`、CI は 3.9〜3.13、開発は 3.12）
- **Framework**: Streamlit `>= 1.50`（`st.page_link` によるナビゲーション、`st.fragment`、`AppTest`）
- **Packaging**: setuptools + `pyproject.toml`、src レイアウト、バージョンは `paper_manager.__version__` から動的取得

## Key Libraries

- **pybtex**: BibTeX のパースと引用文の整形（`plain` スタイル）
- **bibtexparser (<2)**: Entry から BibTeX テキストへの書き出し
- **crossrefapi**: DOI からのメタデータ取得
- **bib2xml**（作者自身のリポジトリ）: BibTeX → XML 変換
- **pandas (<3)**: 一覧テーブル表示用の DataFrame 化のみ

## Development Standards

### Type Safety
- 型ヒントを付ける。mypy は `ignore_missing_imports = True` で運用
- **Python 3.9 互換を維持する**: `X | Y` 構文は使わず `Union` / `Optional` を使う。`ParamSpec` 等は `sys.version_info` 分岐で `typing_extensions` から取る。組み込みジェネリクス（`list[str]`）と walrus 演算子は可

### Code Quality
- ruff: `line-length = 79`、docstring は **numpy スタイル**
- エラーは握りつぶさず、ログ（`_logger.error`）と UI 通知（`st.error`）の両方に出す。ファイル操作は `OSError` を捕捉し、1件の失敗で処理全体を止めずに続行する設計が多い
- 失敗が想定される操作は例外ではなく `(bool, message)` や件数（`int`）を返すパターンがある（例: `restore_from_zip`, `save_pdfs`, `delete_pdfs`）

### Logging
- 独自の `paper_manager.logging` を使う。各モジュールで `_logger = get_child_logger(__name__)` を定義し、`print` は CLI の対話出力のみに限定する
- `paper-manager run --debug` でライブラリルートロガーが DEBUG になる

### Testing
- pytest。`tests/` 直下にフラットに `test_*.py` を置く
- Streamlit ページは `streamlit.testing.v1.AppTest.from_file(...)` でスモークテストする
- CI（GitHub Actions）は全 Python バージョンで `pip install -e .[test]` → `pytest -xs`

## Development Environment

### Required Tools
- conda で仮想環境を管理する（既存環境への勝手なインストールは不可、新規作成は承認制）
- テスト用 extra は `.[test]`、ドキュメント用は `.[docs]`（Sphinx + MyST + myst-nb、`docs_src/`）

### Common Commands
```bash
# Install (dev): pip install -e ".[test]"
# Run app:       paper-manager run --debug --server.address localhost
# Test:          pytest -xs
# Lint:          ruff check src tests
# Type check:    mypy src
# Docs:          sphinx-apidoc -f -o ./docs_src ./src/paper_manager --module-first
#                sphinx-build -b html ./docs_src ./docs
```

## Key Technical Decisions

- **パス定数は import 時に確定する**: `_constants.py` が `PAPER_MANAGER_DATA_DIR` を import 時に読み、`DIRPATH_DATA` / `FILEPATH_LIST` などを決める。各モジュールは `from paper_manager._constants import FILEPATH_LIST` のように**値を束縛**しているため、実行時に差し替えるには利用側モジュールの属性を差し替える必要がある（テストでは環境変数を import 前に設定するのが確実）
- **PDF の保存形式**: `DIRPATH_PDF/<pdf_dir_name>/NN.pdf`。ファイル名をゼロ埋め連番にしてパスを短くし、Windows の MAX_PATH を回避する。フォルダ名は 200 文字で切り詰め、末尾の `.` と空白を除去する
- **list.json を保存形式に採用**: シンプルで扱いやすく、BibTeX へはエクスポートで対応（README で SQLite/YAML との比較を記録済み）
- **依存バージョン方針**: ライブラリとして下限は必要時のみ上げ（dependabot `increase-if-necessary`）、破壊的変更が予想されるメジャーには上限を付ける（`pandas <3`, `bibtexparser <2`）

---
_Document standards and patterns, not every dependency_
