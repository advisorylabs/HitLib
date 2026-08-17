"""Small reusable widgets shared across panels."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QPushButton


class ColorButton(QPushButton):
    """A swatch button that opens a color picker and reports 0xRRGGBB ints."""

    color_changed = Signal(int)

    def __init__(self, initial: int = 0xFFFFFF, parent=None):
        super().__init__(parent)
        self._color = initial & 0xFFFFFF
        self.setFixedWidth(48)
        self.clicked.connect(self._pick)
        self._refresh_style()

    def color(self) -> int:
        return self._color

    def set_color(self, value: int) -> None:
        self._color = value & 0xFFFFFF
        self._refresh_style()

    def _refresh_style(self) -> None:
        r, g, b = (self._color >> 16) & 0xFF, (self._color >> 8) & 0xFF, self._color & 0xFF
        self.setStyleSheet(f"background-color: rgb({r},{g},{b}); border: 1px solid #555;")

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
