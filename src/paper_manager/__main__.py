import argparse
import sys
from datetime import datetime
from logging import DEBUG
from pathlib import Path
from typing import Optional

if sys.version_info >= (3, 9):
    from collections.abc import Sequence
else:
    from typing import Sequence

from streamlit.web import cli

from paper_manager import __version__
from paper_manager.backup import (
    create_backup_zip,
    get_backup_info,
    restore_from_zip,
)
from paper_manager.helper import dummy_func
from paper_manager.logging import get_library_root_logger

__all__ = ("main",)

root_logger = get_library_root_logger()

DIRPATH_ROOT = Path(__file__).parent


def main(cli_args: Sequence[str], prog: Optional[str] = None) -> None:
    parser = argparse.ArgumentParser(prog=prog, description="")
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        help="show current version",
        version=f"%(prog)s: {__version__}",
    )
    parser.set_defaults(func=dummy_func)

    subparsers = parser.add_subparsers()
    parser_run = subparsers.add_parser(
        "run",
        help="wrapper of 'streamlit run'",
        add_help=False,
    )
    parser_run.add_argument("--debug", action="store_true", help="debug mode")
    parser_run.set_defaults(func=run)

    parser_version = subparsers.add_parser(
        "version",
        help="wrapper of 'streamlit version'",
        add_help=False,
    )
    parser_version.set_defaults(func=version)

    parser_backup = subparsers.add_parser(
        "backup",
        help="create a backup of paper-manager data",
    )
    parser_backup.add_argument(
        "-o",
        "--output",
        type=str,
        help="output file path (default: paper-manager-backup_YYYYMMDD_HHMMSS.zip)",
    )
    parser_backup.set_defaults(func=backup)

    parser_restore = subparsers.add_parser(
        "restore",
        help="restore paper-manager data from a backup file",
    )
    parser_restore.add_argument(
        "backup_file",
        type=str,
        help="path to the backup zip file",
    )
    parser_restore.add_argument(
        "--force",
        action="store_true",
        help="restore without confirmation",
    )
    parser_restore.set_defaults(func=restore)

    args, unknown = parser.parse_known_args(cli_args)
    if getattr(args, "debug", False):
        root_logger.setLevel(DEBUG)

    if "--client.showSidebarNavigation" not in set(unknown):
        unknown.extend(["--client.showSidebarNavigation", "false"])

    args.func(unknown)


def run(cli_args: Sequence[str]):
    cli.main_run((str(DIRPATH_ROOT / "app/_list.py"),) + tuple(cli_args))


def version(cli_args: Sequence[str]):
    cli.main_version(cli_args)


def backup(cli_args: Sequence[str]) -> None:
    """バックアップを作成する。"""
    parser = argparse.ArgumentParser(prog="paper-manager backup")
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        help="output file path",
    )
    args = parser.parse_args(cli_args)

    try:
        if args.output:
            output_path = Path(args.output)
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = Path(f"paper-manager-backup_{timestamp}.zip")

        root_logger.info(f"Creating backup to {output_path}...")
        create_backup_zip(output_path)
        root_logger.info(f"Backup created successfully: {output_path}")

    except Exception:
        root_logger.exception("Failed to create backup")
        sys.exit(1)


def restore(cli_args: Sequence[str]) -> None:
    """バックアップから復元する。"""
    parser = argparse.ArgumentParser(prog="paper-manager restore")
    parser.add_argument(
        "backup_file", type=str, help="path to the backup zip file"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="restore without confirmation",
    )
    args = parser.parse_args(cli_args)

    backup_path = Path(args.backup_file)
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
    if not args.force:
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
