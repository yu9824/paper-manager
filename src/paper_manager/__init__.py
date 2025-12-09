"""paper-manager: Streamlitベースの論文管理アプリケーション

paper-managerは、学術論文の情報を管理し、PDFファイルを整理するための
Streamlitベースのアプリケーションです。

主な機能:
    - 論文情報（著者、タイトル、ジャーナル、DOIなど）の登録・編集・削除
    - PDFファイルの管理と整理
    - DOIからの自動メタデータ取得
    - BibTeX形式でのインポート・エクスポート
    - データのバックアップ・復元
    - プロキシ環境でのDOI取得に対応

使用方法:
    アプリケーションを起動するには、以下のコマンドを実行します::

        paper-manager run

    開発モードで起動する場合::

        paper-manager run --debug --server.address localhost

    バックアップを作成する場合::

        paper-manager backup [-o OUTPUT]

    バックアップから復元する場合::

        paper-manager restore BACKUP_FILE [--force]

データの保存場所:
    デフォルトでは、以下の場所にデータが保存されます:
    - データディレクトリ: ``~/.paper-manager/``
    - 論文リスト: ``~/.paper-manager/list.json``
    - 設定ファイル: ``~/.paper-manager/config.json``
    - PDFファイル: ``~/.paper-manager/pdf/``

    環境変数 ``PAPER_MANAGER_DATA_DIR`` を設定することで、
    データディレクトリを変更できます。

例:
    >>> from paper_manager import __version__
    >>> print(__version__)
    2.0.0

"""

__version__ = "2.0.0"
__license__ = "MIT"
__author__ = "yu9824"
__copyright__ = "Copyright © 2024 yu9824"

# __all__ = ()
