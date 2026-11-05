import argparse
import sys
from datetime import datetime
from functools import partial
from logging import DEBUG
from pathlib import Path
from typing import Optional

if sys.version_info >= (3, 9):
    from collections.abc import Sequence
else:
    from typing import Sequence

from streamlit.web import cli

from paper_manager import __version__
from paper_manager.app.helper._backup import (
    create_backup_zip,
    get_backup_info,
    restore_from_zip,
)
from paper_manager.helper import dummy_func
from paper_manager.logging import get_library_root_logger

__all__ = ("main",)

root_logger = get_library_root_logger()

DIRPATH_ROOT = Path(__file__).parent

NAVIGATION_OPTION = "--client.showSidebarNavigation"

# ``streamlit run`` で、これ以降をアプリの引数として扱う区切り
_APP_ARGS_SEPARATOR = "--"


def main(cli_args: Sequence[str], prog: Optional[str] = None) -> None:
    """コマンド全体の引数を解析し、サブコマンドの本体を実行する。

    Parameters
    ----------
    cli_args : Sequence[str]
        コマンド名を除いた引数。
    prog : Optional[str], optional
        ヘルプなどに表示するコマンド名。
    """
    parser = argparse.ArgumentParser(prog=prog, description="")
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        help="show current version",
        version=f"%(prog)s: {__version__}",
    )
    parser.set_defaults(func=_forward_to_dummy)

    subparsers = parser.add_subparsers()
    parser_run = subparsers.add_parser(
        "run",
        help="wrapper of 'streamlit run'",
        add_help=False,
    )
    parser_run.add_argument("--debug", action="store_true", help="debug mode")
    parser_run.set_defaults(func=_forward_to_run)

    parser_version = subparsers.add_parser(
        "version",
        help="wrapper of 'streamlit version'",
        add_help=False,
    )
    parser_version.set_defaults(func=_forward_to_version)

    parser_backup = subparsers.add_parser(
        "backup",
        help="create a backup of paper-manager data",
    )
    _add_backup_arguments(parser_backup)
    parser_backup.set_defaults(func=partial(_forward_to_backup, parser_backup))

    parser_restore = subparsers.add_parser(
        "restore",
        help="restore paper-manager data from a backup file",
    )
    _add_restore_arguments(parser_restore)
    parser_restore.set_defaults(
        func=partial(_forward_to_restore, parser_restore)
    )

    args, unknown = parser.parse_known_args(cli_args)
    if getattr(args, "debug", False):
        root_logger.setLevel(DEBUG)

    args.func(args, unknown)


def with_navigation_default(cli_args: Sequence[str]) -> list[str]:
    """サイドバーのナビゲーションを無効にする既定の指定を付ける。

    ``--client.showSidebarNavigation`` が空白区切り（``--... true``）または
    ``=`` 形式（``--...=true``）で明示されていれば、何も足さずにそのまま
    返す。``--`` 以降はアプリの引数なので、既定の指定はその前に付け、
    ``--`` 以降の指定は明示指定とみなさない。

    Parameters
    ----------
    cli_args : Sequence[str]
        ``streamlit run`` に渡す引数（起動対象を除く）。

    Returns
    -------
    list[str]
        既定の指定を付けた（または付けない）引数の新しいリスト。
    """
    args = list(cli_args)
    if _APP_ARGS_SEPARATOR in args:
        split = args.index(_APP_ARGS_SEPARATOR)
    else:
        split = len(args)
    options, app_args = args[:split], args[split:]

    if any(
        arg == NAVIGATION_OPTION or arg.startswith(NAVIGATION_OPTION + "=")
        for arg in options
    ):
        return args
    return [*options, NAVIGATION_OPTION, "false", *app_args]


def _add_backup_arguments(parser: argparse.ArgumentParser) -> None:
    """``backup`` の引数の定義を ``parser`` に追加する。

    ``main`` のサブパーサーと、単独の ``backup`` のパーサーで共用する。

    Parameters
    ----------
    parser : argparse.ArgumentParser
        引数を追加するパーサー。
    """
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        help=(
            "output file path "
            "(default: paper-manager-backup_YYYYMMDD_HHMMSS.zip)"
        ),
    )


def _add_restore_arguments(parser: argparse.ArgumentParser) -> None:
    """``restore`` の引数の定義を ``parser`` に追加する。

    ``main`` のサブパーサーと、単独の ``restore`` のパーサーで共用する。

    Parameters
    ----------
    parser : argparse.ArgumentParser
        引数を追加するパーサー。
    """
    parser.add_argument(
        "backup_file",
        type=str,
        help="path to the backup zip file",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="restore without confirmation",
    )


def _reject_unrecognized(
    parser: argparse.ArgumentParser, unknown: Sequence[str]
) -> None:
    """未知の引数が残っていれば、``parser`` のエラー（終了コード 2）にする。

    Parameters
    ----------
    parser : argparse.ArgumentParser
        エラーの表示と終了に使うサブコマンドのパーサー。
    unknown : Sequence[str]
        解析で消費されずに残った引数。
    """
    if unknown:
        parser.error(f"unrecognized arguments: {' '.join(unknown)}")


def _forward_to_dummy(
    args: argparse.Namespace, unknown: Sequence[str]
) -> None:
    """サブコマンドなしのとき、何もしない。"""
    dummy_func(unknown)


def _forward_to_run(args: argparse.Namespace, unknown: Sequence[str]) -> None:
    """``run`` の未知の引数に既定の指定を付け、Streamlit へ渡す。"""
    run(with_navigation_default(unknown))


def _forward_to_version(
    args: argparse.Namespace, unknown: Sequence[str]
) -> None:
    """``version`` の未知の引数を、何も足さずに Streamlit へ渡す。"""
    version(unknown)


def _forward_to_backup(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    unknown: Sequence[str],
) -> None:
    """解析済みの引数で ``backup`` の本体を実行する。"""
    _reject_unrecognized(parser, unknown)
    _run_backup(args.output)


def _forward_to_restore(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    unknown: Sequence[str],
) -> None:
    """解析済みの引数で ``restore`` の本体を実行する。"""
    _reject_unrecognized(parser, unknown)
    _run_restore(args.backup_file, args.force)


def run(cli_args: Sequence[str]):
    cli.main_run((str(DIRPATH_ROOT / "app/_list.py"),) + tuple(cli_args))


def version(cli_args: Sequence[str]):
    cli.main_version(cli_args)


def backup(cli_args: Sequence[str]) -> None:
    """バックアップを作成する。

    Parameters
    ----------
    cli_args : Sequence[str]
        ``backup`` に続く引数。
    """
    parser = argparse.ArgumentParser(prog="paper-manager backup")
    _add_backup_arguments(parser)
    args = parser.parse_args(cli_args)

    _run_backup(args.output)


def restore(cli_args: Sequence[str]) -> None:
    """バックアップから復元する。

    Parameters
    ----------
    cli_args : Sequence[str]
        ``restore`` に続く引数。
    """
    parser = argparse.ArgumentParser(prog="paper-manager restore")
    _add_restore_arguments(parser)
    args = parser.parse_args(cli_args)

    _run_restore(args.backup_file, args.force)


def _run_backup(output: Optional[str]) -> None:
    """バックアップを作成する本体。失敗したら終了コード 1 で終了する。

    Parameters
    ----------
    output : Optional[str]
        出力先のパス。省略（``None`` または空）なら、作業ディレクトリに
        日時入りの名前で作る。
    """
    try:
        if output:
            output_path = Path(output)
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = Path(f"paper-manager-backup_{timestamp}.zip")

        root_logger.info(f"Creating backup to {output_path}...")
        create_backup_zip(output_path)
        root_logger.info(f"Backup created successfully: {output_path}")

    except Exception:
        root_logger.exception("Failed to create backup")
        sys.exit(1)


def _run_restore(backup_file: str, force: bool) -> None:
    """バックアップから復元する本体。

    失敗したら終了コード 1 で終了する。``force`` でなければ、復元の前に
    確認を求め、``yes`` / ``y`` 以外なら復元せずに終了コード 0 で終了する。

    Parameters
    ----------
    backup_file : str
        バックアップ zip のパス。
    force : bool
        ``True`` なら確認を求めずに復元する。
    """
    backup_path = Path(backup_file)
    if not backup_path.is_file():
        root_logger.error(f"Backup file not found: {backup_path}")
        sys.exit(1)

    # バックアップ情報を表示
    backup_info = get_backup_info(backup_path)
    if backup_info:
        root_logger.info(
            f"Backup version: {backup_info.get('version', 'N/A')}"
        )
        root_logger.info(
            f"Backup date: {backup_info.get('backup_date', 'N/A')}"
        )

    # 確認
    if not force:
        print("\n⚠️  警告: 復元を実行すると、現在のデータが上書きされます。")
        print(f"バックアップファイル: {backup_path}")
        response = input("続行しますか？ (yes/no): ")
        if response.lower() not in ("yes", "y"):
            print("復元をキャンセルしました。")
            sys.exit(0)

    try:
        root_logger.info(f"Restoring from {backup_path}...")
        success, message = restore_from_zip(
            backup_path, update_session_state=False
        )

        if success:
            root_logger.info("Restore completed successfully")
            print("\n" + message)
        else:
            root_logger.error(f"Restore failed: {message}")
            print(f"\nエラー: {message}")
            sys.exit(1)

    except Exception:
        root_logger.exception("Failed to restore from backup")
        sys.exit(1)


def entrypoint() -> None:
    main(sys.argv[1:])


if __name__ == "__main__":
    main(sys.argv[1:], prog="paper-manager")
