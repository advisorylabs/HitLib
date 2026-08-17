"""Live strip preview: one horizontal row of LEDs per strand session."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter
from PySide6.QtWidgets import QWidget


class StripCanvas(QWidget):
    # Row height is fixed at whatever size makes REFERENCE_ROWS strands fill
    # the canvas nicely, not stretched to fill however many strands actually
    # exist -- with 1-2 strands that used to mean giant bars. The resulting
    # stack is vertically centered instead of pinned to the top, so it grows
    # outward from the middle as strands are added.
    REFERENCE_ROWS = 10
    MARGIN = 12
    LABEL_H = 16
    ROW_GAP = 10
    MIN_ROW_H = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sessions: list = []
        self.setMinimumHeight(100)

    def set_sessions(self, sessions: list) -> None:
        self.sessions = sessions
        self.update()

    def _reference_row_height(self) -> float:
        usable = (
            self.height()
            - 2 * self.MARGIN
            - self.REFERENCE_ROWS * self.LABEL_H
            - (self.REFERENCE_ROWS - 1) * self.ROW_GAP
        )
        return max(self.MIN_ROW_H, usable / self.REFERENCE_ROWS)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(18, 18, 22))

        if not self.sessions:
            painter.setPen(QColor(140, 140, 148))
            painter.drawText(self.rect(), Qt.AlignCenter, "Add a strand to preview it here")
            return

        row_h = self._reference_row_height()
        row_count = len(self.sessions)
        stack_h = row_count * (self.LABEL_H + row_h) + max(0, row_count - 1) * self.ROW_GAP
        available_h = self.height() - 2 * self.MARGIN
        y = self.MARGIN + max(0.0, (available_h - stack_h) / 2)

        for session in self.sessions:
            strand = session.strand
            n = max(strand.length, 1)
            available_w = self.width() - 2 * self.MARGIN
            led_w = available_w / n
            pad = min(3.0, led_w * 0.15)

            painter.setPen(QColor(210, 210, 216))
            painter.drawText(
                QRectF(self.MARGIN, y, available_w, self.LABEL_H), Qt.AlignLeft, session.config.name
            )
            row_top = y + self.LABEL_H

            for i, color in enumerate(strand.pixels):
                r, g, b = (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF
                x = self.MARGIN + i * led_w
                rect = QRectF(x + pad / 2, row_top, led_w - pad, row_h)
                painter.setBrush(QBrush(QColor(r, g, b)))
                painter.setPen(QColor(40, 40, 44))
                painter.drawRoundedRect(rect, 3, 3)

            y += self.LABEL_H + row_h + self.ROW_GAP
