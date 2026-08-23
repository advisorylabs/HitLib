"""Small reusable widgets shared across panels."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QPushButton

from . import theme


class ColorButton(QPushButton):
    """A swatch button that opens a color picker and reports 0xRRGGBB ints."""

    color_changed = Signal(int)

    def __init__(self, initial: int = 0xFFFFFF, parent=None):
        super().__init__(parent)
        self._color = initial & 0xFFFFFF
        self.setFixedSize(52, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to pick a color")
        self.clicked.connect(self._pick)
        self._refresh_style()

    def color(self) -> int:
        return self._color

    def set_color(self, value: int) -> None:
        self._color = value & 0xFFFFFF
        self._refresh_style()

    def _refresh_style(self) -> None:
        # Inline rather than themed: the swatch's whole job is to show an
        # arbitrary user-chosen color, so only its chrome (border, radius,
        # hover) comes from the theme.
        r, g, b = (self._color >> 16) & 0xFF, (self._color >> 8) & 0xFF, self._color & 0xFF
        self.setStyleSheet(
            f"QPushButton {{ background-color: rgb({r},{g},{b});"
            f" border: 1px solid {theme.BORDER_STRONG}; border-radius: 5px; padding: 0px; }}"
            f"QPushButton:hover {{ border: 1px solid {theme.ACCENT}; }}"
        )

    def _pick(self) -> None:
        r, g, b = (self._color >> 16) & 0xFF, (self._color >> 8) & 0xFF, self._color & 0xFF
        picked = QColorDialog.getColor(QColor(r, g, b), self, "Pick Color")
        if picked.isValid():
            self._color = (picked.red() << 16) | (picked.green() << 8) | picked.blue()
            self._refresh_style()
            self.color_changed.emit(self._color)


def parse_palette(text: str) -> list[int]:
    """Parses a comma-separated list of hex colors ("FF0000, 00FF00") into ints."""
    colors = []
    for chunk in text.split(","):
        chunk = chunk.strip().lstrip("#")
        if not chunk:
            continue
        try:
            colors.append(int(chunk, 16) & 0xFFFFFF)
        except ValueError:
            continue
    return colors


def format_palette(colors: list[int]) -> str:
    return ", ".join(f"{c:06X}" for c in colors)
