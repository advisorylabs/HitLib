import os

# Must be set before PySide6.QtWidgets is imported anywhere -- lets the GUI
# tests run headless without a real display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def themed(qapp):
    """The app's real look, installed for one test and taken back off after.

    Anything measuring where things land or what color they are needs this:
    padding lives in the stylesheet, and the painted surfaces assume the dark
    palette. Restored afterwards so a test that expects Qt's defaults isn't
    quietly measuring ours.
    """
    from pattern_studio import theme

    sheet, palette, font = qapp.styleSheet(), qapp.palette(), qapp.font()
    theme.apply_theme(qapp)
    yield qapp
    qapp.setStyleSheet(sheet)
    qapp.setPalette(palette)
    qapp.setFont(font)
