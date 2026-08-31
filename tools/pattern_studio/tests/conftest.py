import gc
import os

# Must be set before PySide6.QtWidgets is imported anywhere. Lets the GUI
# tests run headless without a real display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent
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


@pytest.fixture(autouse=True)
def _close_windows(qapp):
    """Destroy the windows a test opened before the next one starts.

    Dropping the last Python reference does not free a window here: measured
    under pytest, the MainWindows a test builds outlive it even through an
    explicit gc.collect(), each leaving ~35 top-level widgets behind. They go
    on animating too - BrandRule repaints at 25fps and the canvas pulses - so
    by the end of a session dozens of abandoned windows are still painting,
    and whichever collection finally takes one runs at an arbitrary
    allocation, including one inside a live window's paintEvent. That is what
    CI crashed on: an access violation inside StripCanvas._paint_leds.

    Closing them here puts the teardown somewhere safe, between tests.
    """
    yield
    for widget in qapp.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    # processEvents() alone will not do it: DeferredDelete is held back for
    # the event loop that posted it, and the tests never run one.
    qapp.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()
    gc.collect()
