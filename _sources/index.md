# paper-manager

paper-managerは、Streamlitベースの論文管理アプリケーションです。学術論文の情報を管理し、PDFファイルを整理し、BibTeX形式でのエクスポートやDOIからの自動メタデータ取得などの機能を提供します。

## 主な機能

- **論文管理**: 論文情報（著者、タイトル、ジャーナル、DOIなど）の登録・編集・削除
- **PDF管理**: 論文のPDFファイルを整理して保存
- **DOI連携**: DOIから自動的にメタデータを取得
- **BibTeX対応**: BibTeX形式でのインポート・エクスポート
- **バックアップ・復元**: データのバックアップと復元機能
- **プロキシ設定**: プロキシ環境でのDOI取得に対応

## インストール

```bash
pip install git+https://github.com/yu9824/paper-manager.git
```

## クイックスタート

### アプリケーションの起動

```bash
paper-manager run
```

これにより、Streamlitサーバーが起動し、ブラウザでアプリケーションにアクセスできます。

### 開発モード

```bash
paper-manager run --debug --server.address localhost
```

## コマンドラインインターフェース

### バックアップ

```bash
paper-manager backup [-o OUTPUT]
```

論文リスト、設定ファイル、PDFファイルをzip形式でバックアップします。

### 復元

```bash
paper-manager restore BACKUP_FILE [--force]
```

バックアップファイルからデータを復元します。

## データの保存場所

デフォルトでは、以下の場所にデータが保存されます：

- **データディレクトリ**: `~/.paper-manager/`
- **論文リスト**: `~/.paper-manager/list.json`
- **設定ファイル**: `~/.paper-manager/config.json`
- **PDFファイル**: `~/.paper-manager/pdf/`

環境変数 `PAPER_MANAGER_DATA_DIR` を設定することで、データディレクトリを変更できます。

## ドキュメント

```{toctree}
:maxdepth: 2
:caption: API Reference

modules
```

```{toctree}
:maxdepth: 2
:caption: Tutorials

tutorials/index
```

```{toctree}
:maxdepth: 2
:caption: ガイド

guides/index
```
