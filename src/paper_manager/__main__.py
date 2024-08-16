import argparse
import sys
from pathlib import Path
from typing import Optional

if sys.version_info >= (3, 9):
    from collections.abc import Sequence
else:
    from typing import Sequence

from streamlit.web import cli

from paper_manager import __version__

__all__ = ["main"]

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

    st_valid_args = {"-v", "--version", "-h", "--help"}
    streamlit_args = tuple(set(cli_args) - st_valid_args)
    parser.parse_args(tuple(set(cli_args) & st_valid_args))

    cli.main_run((str(DIRPATH_ROOT / "app" / "_app.py"),) + streamlit_args)


def entrypoint() -> None:
    main(sys.argv[1:])


if __name__ == "__main__":
    main(sys.argv[1:], prog="paper-manager")
