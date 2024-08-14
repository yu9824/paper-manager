## プロジェクトの作成

```bash
sphinx-quickstart docs_src
```

```plaintext
Sphinx 7.3.7 クイックスタートユーティリティへようこそ。

以下の設定値を入力してください（Enter キーのみ押した場合、
かっこで囲まれた値をデフォルト値として受け入れます）。

選択されたルートパス: docs_src

Sphinx 出力用のビルドディレクトリを配置する方法は2つあります。
ルートパス内にある "_build" ディレクトリを使うか、
ルートパス内に "source" と "build" ディレクトリを分ける方法です。
> ソースディレクトリとビルドディレクトリを分ける（y / n） [n]: n

プロジェクト名は、ビルドされたドキュメントのいくつかの場所にあります。
> プロジェクト名: python-template
> 著者名（複数可）: yu9824
> プロジェクトのリリース []: 0.0.1

ドキュメントを英語以外の言語で書く場合は、
 言語コードで言語を選択できます。Sphinx は生成したテキストをその言語に翻訳します。

サポートされているコードのリストについては、
https://www.sphinx-doc.org/en/master/usage/configuration.html#confval-language を参照してください。
> プロジェクトの言語 [en]: en

ファイル /Users/yu9824/python-template/docs_src/conf.py を作成しています。
ファイル /Users/yu9824/python-template/docs_src/index.rst を作成しています。
ファイル /Users/yu9824/python-template/docs_src/Makefile を作成しています。
ファイル /Users/yu9824/python-template/docs_src/make.bat を作成しています。

終了：初期ディレクトリ構造が作成されました。

マスターファイル /Users/yu9824/python-template/docs_src/index.rst を作成して
他のドキュメントソースファイルを作成します。次のように Makefile を使ってドキュメントを作成します。
 make builder
"builder" はサポートされているビルダーの 1 つです。 例: html, latex, または linkcheck。

```

```bash
rm -r docs_src/_build
touch docs_src/_static/.gitkeep
```

## docstringから生成

```bash
sphinx-apidoc -f -o ./docs_src ./src/python_template --module-first
```

## build

```bash
# single version
sphinx-build -b html ./docs_src ./docs
# multible version
sphinx-multiversion ./docs_src ./docs
```
