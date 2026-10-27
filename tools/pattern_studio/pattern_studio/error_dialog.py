"""Puts an error nothing else caught in front of the user, traceback and all.

The release builds have no console (console=False in the spec). Qt hands an
exception raised in a slot or an event handler to sys.excepthook, and the
default hook writes it to a stderr that isn't there, so the action stops
part-way and nothing says so: a Deploy that writes no file and shows nothing.
install() adds a dialog to that hook, with the traceback and a Copy button,
which is what a bug report needs. The old hook still runs, so a run from
source prints the traceback as it always did.
"""

from __future__ import annotations

import platform
import sys
import traceback

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from . import __version__, theme


def report_text(exc_type, exc, tb) -> str:
    """The traceback, headed by what a bug report needs beside it."""
    header = (
        f"Pattern Studio {__version__}, Python {platform.python_version()}, "
        f"{platform.platform()}"
    )
    return header + "\n\n" + "".join(traceback.format_exception(exc_type, exc, tb))


def _origin(exc_type, tb) -> str:
    """Where an exception came from, less what it says.

    A paint error recurs every frame, often with a different value in its
    message each time; the frames it passed through stay the same.
    """
    frames = "".join(f"{f.filename}:{f.lineno}\n" for f in traceback.extract_tb(tb))
    return frames + exc_type.__qualname__


class ErrorDialog(QDialog):
    """Says something failed, and shows the traceback to copy."""

    def __init__(self, details: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Unexpected Error")
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        headline = QLabel("Something went wrong, so what you just did may not have finished.")
        headline.setWordWrap(True)
        headline.setStyleSheet(f"color: {theme.DANGER_HI};")
        layout.addWidget(headline)

        caption = QLabel("If you report this, include the details below.")
        caption.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        layout.addWidget(caption)

        self.details_box = QPlainTextEdit(details)
        self.details_box.setReadOnly(True)
        self.details_box.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.details_box.setFont(QFont(theme.MONO_STACK, theme.FONT_POINT_SIZE))
        self.details_box.setMinimumSize(560, 220)
        layout.addWidget(self.details_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.copy_button = QPushButton("Copy")
        theme.HoverBloom(self.copy_button, theme.ACCENT)
        self.copy_button.clicked.connect(self._copy)
        buttons.addButton(self.copy_button, QDialogButtonBox.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._details = details

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self._details)
        # The label change is the only confirmation shown.
        self.copy_button.setText("Copied")


class ErrorReporter(QObject):
    """Turns a call to the hook into a dialog, opened from the event loop."""

    # Queued, so the dialog opens once the failing handler has unwound rather
    # than inside it, where a paint error would repaint behind the dialog and
    # raise again. Queuing also carries an error raised off the GUI thread
    # over to it, the only thread a dialog can be built on.
    raised = Signal(str, str)

    def __init__(self, app: QApplication):
        super().__init__(app)
        self.dialog: ErrorDialog | None = None
        self._shown: set[str] = set()
        self.raised.connect(self._show, Qt.QueuedConnection)

    def hook(self, exc_type, exc, tb) -> None:
        """For sys.excepthook. Formats the report and queues the dialog, and
        nothing more: the hook runs inside whatever just failed."""
        # KeyboardInterrupt or SystemExit is someone stopping the app on
        # purpose, not a failure to report.
        if issubclass(exc_type, Exception):
            self.raised.emit(_origin(exc_type, tb), report_text(exc_type, exc, tb))

    def _show(self, origin: str, details: str) -> None:
        # Once per origin, and one at a time: an error that recurs every
        # frame would otherwise bury the design under dialogs, and leave no
        # way back to it to save.
        if self.dialog is not None or origin in self._shown:
            return
        self._shown.add(origin)
        self.dialog = ErrorDialog(details, _main_window())
        self.dialog.finished.connect(self._closed)
        # open() rather than exec(): no nested event loop, so nothing still
        # unwinding from the error runs again inside the dialog.
        self.dialog.open()

    def _closed(self) -> None:
        self.dialog.deleteLater()
        self.dialog = None


def _main_window() -> QMainWindow | None:
    """What the dialog sits over. Not activeWindow(): that may be a popup,
    which would take the dialog with it when it closes."""
    return next(
        (w for w in QApplication.topLevelWidgets() if isinstance(w, QMainWindow) and w.isVisible()),
        None,
    )


def install(app: QApplication) -> ErrorReporter:
    """Show every exception nothing caught in a dialog, while @p app runs."""
    reporter = ErrorReporter(app)
    previous = sys.excepthook

    def hook(exc_type, exc, tb) -> None:
        # Ours first: the previous hook is the one that can fail, writing to
        # a stderr a windowed build doesn't have.
        reporter.hook(exc_type, exc, tb)
        previous(exc_type, exc, tb)

    sys.excepthook = hook
    return reporter
