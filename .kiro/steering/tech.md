# Technology Stack

## Architecture

Streamlit のマルチページアプリを、`streamlit run` をラップした CLI（`paper-manager`）から起動する構成。データ永続化はファイルシステムのみ（JSON + PDF ディレクトリ）で、DB やサーバー側コンポーネントは持たない。

- UI 層（`app/`）は Streamlit に依存し、ドメイン層（`entry`, `bib`, `helper`, `logging`）はできるだけ UI に依存しない
- 例外: `PaperList` は session state との同期メソッド（`from_session_state` / `to_session_state`）を持つため `streamlit` を import している
- 各ページは `@config_page` で包まれ、ページ実行前に `list.json` → session state を読み込む。`config_page` は `list.json` を書かない（ページを開いただけでは書き換えない）。論文リストの登録・更新・削除は `save_paper_list()` が session state とファイルの両方へ保存する

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
- 失敗が想定される操作は例外ではなく `(bool, message)` や件数（`int`）、成否（`bool`）を返すパターンがある（例: `restore_from_zip`, `save_pdfs`, `delete_pdfs`, `save_paper_list`）。呼び出し側は戻り値を見て、失敗したときに画面のエラーを消す再描画（`st.rerun()`）をしない。再描画をする呼び出し側（編集ダイアログ）は、再描画の前に失敗の文を `KEY_NOTICES`（次の描画で `config_page` が `st.warning` に出して消す保留中の通知）に積む

### Logging
- 独自の `paper_manager.logging` を使う。各モジュールで `_logger = get_child_logger(__name__)` を定義し、`print` は CLI の対話出力のみに限定する
- `paper-manager run --debug` でライブラリルートロガーが DEBUG になる

### Testing
- pytest。`tests/` 直下にフラットに `test_*.py` を置く。`xfail_strict = true`（既知バグは strict な xfail で記録し、直したら外す）
- Streamlit ページは `streamlit.testing.v1.AppTest.from_file(...)` でスモークテストする。AppTest や session state に関わる変更は、CI の Python 3.9 で入る Streamlit 1.50 でも確かめる
- **実データを触らない**: `tests/conftest.py` の autouse fixture が、`PAPER_MANAGER_DATA_DIR` を `paper_manager` の import 前に一時ディレクトリへ向け、各テストの前後で状態を初期化する。テストで `~/.paper-manager` を読み書きしない
- **Windows のパスの扱い**は Linux/macOS の CI では再現できないため、opt-in の fixture で模す: `windows_like_paths`（変換後のパスを `//x` の別名にして、変換前後のパスの混在を検出する）と `redirect_data_dir`（変換後のパスを別のディレクトリへ写し、変換後のパスを使っているかを判別する）。実機の Windows での確認は手動
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
- **PDF の保存形式**: `DIRPATH_PDF/<pdf_dir_name>/NN.pdf`。ファイル名をゼロ埋め連番にしてパスを短くし、Windows の MAX_PATH を回避する。フォルダ名（`pdf_dir_name`）は 200 文字以内で、末尾の `.` と空白を除去し、空になるときは `Unknown` にする。名前は PDF ディレクトリの場所に依存しない。既存のフォルダ名は変えない（旧規則の名前は `legacy_pdf_dir_name` で探して新しい名前へ上書きなしで移行する）
- **ファイル操作のパスは変換する**: ファイルを開く・作る・一覧・削除・改名・zip を読み書きする起点は `to_fs_path()`（Windows では `\\?\` の拡張長パス）を通し、画面とログに出すパスは `to_display_path()` で prefix を外す。変換後のパスと変換前のパス（`DIRPATH_PDF` など）を混ぜて比較・`relative_to` しない。対象は PDF ルート以下の PDF とバックアップ/復元の zip で、`list.json` / `config.json` は対象外（データディレクトリ自体が約 250 文字を超える環境は未対応）。フォルダ名の同一判定は `folder_name_key()`（大文字小文字と Unicode 正規化を無視。macOS・Windows の既定のファイルシステムに合わせる）で行う
- **壊れた `list.json` を上書きしない**: `load_paper_list()` が壊れた状態（解析できない・UTF-8 でない・一覧の形でない）を区別して警告だけを出し、ページを開いただけでは書き換えない。保存するときは、書き込みの前に `list.json.broken-<timestamp>` へ退避して退避先を画面に出す。BOM 付き UTF-8 は正常として読む
- **復元は書き込みの前に検証する**: `restore_from_zip()` は zip の全メンバー名を検証し、データディレクトリの外を指す名前が 1 つでもあれば、何も書かずに中止する。続けて全メンバーを最後まで読み出せること（CRC の一致、圧縮データの破損、暗号化、未対応の圧縮方式）も確かめ、読み出せないメンバーが 1 つでもあれば、`.json.backup` も作らずに中止する（書き込み先を開いた後に読み出しに失敗すると、空の `list.json` が残り、続く復元で退避ファイルの旧内容も失うため）。画面と CLI で同じ処理を使う
- **引用キーは出力時に整える**: `list.json` の `ID` は変えず、BibTeX・XML の出力時に `sanitize_citation_key()` / `unique_citation_keys()` で空白と `,{}` を除き、大文字小文字を無視して重複を解消する
- **list.json を保存形式に採用**: シンプルで扱いやすく、BibTeX へはエクスポートで対応（README で SQLite/YAML との比較を記録済み）
- **依存バージョン方針**: ライブラリとして下限は必要時のみ上げ（dependabot `increase-if-necessary`）、破壊的変更が予想されるメジャーには上限を付ける（`pandas <3`, `bibtexparser <2`）

---
_Document standards and patterns, not every dependency_
