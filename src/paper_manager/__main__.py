import argparse
import sys
from logging import DEBUG
from pathlib import Path
from typing import Optional

if sys.version_info >= (3, 9):
    from collections.abc import Sequence
else:
    from typing import Sequence

from streamlit.web import cli

from paper_manager import __version__
from paper_manager.logging import get_root_logger
from paper_manager.utils import dummy_func

__all__ = ("main",)

root_logger = get_root_logger()

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

    args, unknown = parser.parse_known_args(cli_args)
    if getattr(args, "debug", False):
        root_logger.setLevel(DEBUG)

    args.func(unknown)


def run(cli_args: Sequence[str]):
    cli.main_run((str(DIRPATH_ROOT / "app" / "_app.py"),) + tuple(cli_args))


def version(cli_args: Sequence[str]):
    cli.main_version(cli_args)


def entrypoint() -> None:
    main(sys.argv[1:])


if __name__ == "__main__":
    main(sys.argv[1:], prog="paper-manager")
