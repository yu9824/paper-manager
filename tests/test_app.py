from pathlib import Path

from streamlit.testing.v1 import AppTest

import paper_manager


def test_app():
    # アプリをテストする準備
    at = AppTest.from_file(
        str(Path(paper_manager.__file__).parent / "app" / "_list.py")
    )
    at.run()
