# paper-manager

[![CI](https://github.com/yu9824/paper-manager/actions/workflows/CI.yaml/badge.svg)](https://github.com/yu9824/paper-manager/actions/workflows/CI.yaml)
[![docs](https://github.com/yu9824/paper-manager/actions/workflows/docs.yaml/badge.svg)](https://github.com/yu9824/paper-manager/actions/workflows/docs.yaml)

This is my own paper-manager with streamlit.

## 主な機能

- **論文管理**: 論文情報（著者、タイトル、ジャーナル、DOIなど）の登録・編集・削除
- **PDF管理**: 各論文ごとに複数のPDFファイルを整理して保存（バージョン2.0.0以降）
- **DOI連携**: DOIから自動的にメタデータを取得
- **BibTeX対応**: BibTeX形式でのインポート・エクスポート
- **バックアップ・復元**: データのバックアップと復元機能
- **プロキシ設定**: プロキシ環境でのDOI取得に対応

## Install

```bash
pip install git+https://github.com/yu9824/paper-manager.git

```

## How to use

Run streamlit server locally.

```bash
paper-manager run   # wrapper of 'streamlit run'
```

## For developper

```bash
paper-manager run --debug --server.address localhost
```

## バックアップ・復元

paper-managerには、データをバックアップ・復元する機能が用意されています。

### バックアップ

1. アプリケーションの「バックアップ」ページに移動
2. 「バックアップを作成」ボタンをクリック
3. ダウンロードボタンからzipファイルをダウンロード

バックアップファイルには以下が含まれます：
- paper-managerのバージョン情報
- 論文リスト（`list.json`）
- すべてのPDFファイル

### 復元

1. アプリケーションの「復元」ページに移動
2. バックアップzipファイルをアップロード
3. 「復元を実行」ボタンをクリック

**注意**: 復元を実行すると、現在のデータが上書きされます。既存の`list.json`は自動的に`.json.backup`としてバックアップされます。

## 文献リストの管理方法について

現在、paper-managerは`list.json`（JSON形式）で文献リストを管理しています。以下の代替案も検討できます：

### 1. BibTeX形式（.bib）

**メリット:**
- 学術界で広く使われている標準形式
- 他の文献管理ツール（Zotero、Mendeley、JabRefなど）との互換性が高い
- テキスト形式なので、バージョン管理（Git）に適している
- 人間が読みやすい

**デメリット:**
- 複雑な構造のデータを扱いにくい
- カスタムフィールドの追加が制限される

### 2. SQLiteデータベース

**メリット:**
- 構造化されたデータ管理
- 高速な検索・クエリが可能
- トランザクション処理が可能
- データの整合性が保証される

**デメリット:**
- バイナリ形式なので、テキストエディタで直接編集できない
- バージョン管理が難しい
- 他のツールとの互換性が低い

### 3. YAML/TOML形式

**メリット:**
- JSONより人間が読みやすい
- コメントが書ける
- テキスト形式なので、バージョン管理に適している

**デメリット:**
- 学術界での標準ではない
- 他の文献管理ツールとの互換性が低い

### 推奨事項

現在の`list.json`形式は、以下の理由で適切な選択です：
- シンプルで理解しやすい
- Pythonで扱いやすい
- 必要に応じてBibTeXにエクスポート可能（既に実装済み）

将来的に他のツールとの互換性を高めたい場合は、BibTeX形式への移行を検討することをお勧めします。
