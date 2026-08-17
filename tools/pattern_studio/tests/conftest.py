import os

# Must be set before PySide6.QtWidgets is imported anywhere -- lets the GUI
# tests run headless (CI, this sandbox) without a real display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app
