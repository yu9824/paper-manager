# Product Overview

paper-manager は、研究者個人が学術論文の書誌情報と PDF をローカルで管理するための Streamlit アプリケーション。サーバーを立てずに `paper-manager run` で手元のブラウザから使う、シングルユーザー前提のツールである。

## Core Capabilities

- **論文登録**: BibTeX（テキスト/ファイル）、DOI（Crossref API でメタデータ自動取得）、手動入力の3経路。エントリタイプごとの必須フィールドで検証する
- **PDF 管理**: 1論文に複数 PDF を紐づけ、論文ごとの人間が読めるフォルダに整理する。論文リストに紐づかない「孤立 PDF」を検出・削除できる
- **引用・エクスポート**: pybtex で整形した引用文（HTML/テキスト）、BibTeX・XML のエクスポート、複数選択時の PDF 一括 zip
- **バックアップ・復元**: 論文リスト・設定・PDF をまとめた zip を UI と CLI（`paper-manager backup` / `restore`）の両方から扱える
- **プロキシ対応**: 社内ネットワーク等でも DOI 取得できるよう、設定ファイルまたは環境変数のプロキシを適用する

## Target Use Cases

- 読んだ論文を DOI や BibTeX から素早く登録し、PDF と一緒に保管する
- 論文を選んで引用文や BibTeX を取り出し、論文・報告書執筆に使う
- データを zip で丸ごと退避し、別マシンへ移行・復元する

## Value Proposition

- **ローカル完結・透明なデータ**: データは `~/.paper-manager/`（`PAPER_MANAGER_DATA_DIR` で変更可）に置かれ、論文リストは人間が読める `list.json`、PDF は `<year> - <first_author> - <title>/00.pdf` 形式で保存される。アプリがなくてもファイルを直接辿れる
- **後方互換を重視**: 旧形式（単一 PDF ファイル、旧ファイル名）は読み込み時に新形式へ自動移行する。ユーザーデータを壊さないことが最優先
- **標準形式との相互運用**: 保存形式は JSON だが、入出力は BibTeX を軸にしており他の文献管理ツールとも行き来できる

## Data Model (概念)

- **Entry**: BibTeX エントリ相当の辞書。キーは `ENTRYTYPE` / `ID` / `DOI` のみ大文字、他は小文字に正規化され、値は前後空白を除去する
- **PaperList**: `ID -> Entry` の辞書。`list.json` と Streamlit の session state の間で同期される
- **著者・タグ**: 単一文字列として保持し、著者は `" and "`、タグは `", "` で区切る（`_constants.py` の `AUTHOR_SEPARATOR` / `TAG_SEPARATOR`）

---
_Focus on patterns and purpose, not exhaustive feature lists_
