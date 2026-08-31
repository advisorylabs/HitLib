"""Shows where a deployed header landed and the lines that use it.

The paste block is rendered from the design, so it carries this design's real
identifiers and mode names. It is selectable, and a Copy button writes it to
the clipboard.

Shown only on the deploy that creates the header, not on ones that overwrite
it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from . import theme

_MONO = ["Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Courier New", "monospace"]


class DeployDialog(QDialog):
    """Shows where the header landed and what to paste to use it."""

    def __init__(self, header_path, paste: str, missing_hitlib_lines=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle("Deployed to Project")
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        headline = QLabel(f"Wrote <b>{header_path.name}</b> to<br>{header_path.parent}")
        headline.setTextFormat(Qt.RichText)
        headline.setWordWrap(True)
        layout.addWidget(headline)

        # A warning, not a failure - the file is written either way.
        if missing_hitlib_lines:
            warning = QLabel(
                "HitLib isn't installed in this project yet, so this won't "
                "compile until it is:<br><br>"
                + "<br>".join(line.replace(" ", "&nbsp;") for line in missing_hitlib_lines)
            )
            warning.setTextFormat(Qt.RichText)
            warning.setWordWrap(True)
            warning.setStyleSheet(f"color: {theme.DANGER_HI};")
            layout.addWidget(warning)

        caption = QLabel("Add this to main.cpp once, and you're done:")
        caption.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        layout.addWidget(caption)

        self.paste_box = QPlainTextEdit(paste)
        self.paste_box.setReadOnly(True)
        self.paste_box.setFont(QFont(_MONO, theme.FONT_POINT_SIZE))
        self.paste_box.setMinimumSize(520, 190)
        layout.addWidget(self.paste_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.copy_button = QPushButton("Copy")
        theme.HoverBloom(self.copy_button, theme.ACCENT)
        self.copy_button.clicked.connect(self._copy)
        buttons.addButton(self.copy_button, QDialogButtonBox.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._paste = paste

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self._paste)
        # The label change is the only confirmation shown.
        self.copy_button.setText("Copied")
