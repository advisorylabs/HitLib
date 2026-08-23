"""Live strip preview: one horizontal row of LEDs per strand session."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import theme


class StripCanvas(QWidget):
    # Row height is fixed at whatever size makes REFERENCE_ROWS strands fill
    # the canvas nicely. The resulting stack is vertically centered instead
    # of pinned to the top, so it grows outward from the middle as strands are added.
    REFERENCE_ROWS = 10
    MARGIN = 12
    # Tall enough that a row's name still clears the track panel drawn
    # TRACK_PAD above the LEDs.
    LABEL_H = 22
    ROW_GAP = 10
    MIN_ROW_H = 8

    #: Padding between a row's LEDs and the track panel drawn behind them.
    TRACK_PAD = 5
    #: A pixel at least this bright gets a glow halo -- below it the halo is
    #: invisible anyway and just costs draw calls.
    GLOW_THRESHOLD = 40

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sessions: list = []
        self.selected: set[int] = set()
        self.setMinimumHeight(100)

    def set_sessions(self, sessions: list) -> None:
        self.sessions = sessions
        self.update()

    def set_selected(self, indices) -> None:
        """Rows to outline as the current group-edit selection. Only drawn
        when more than one strand is selected -- with a single selection the
        outline would just be visual noise."""
        self.selected = set(indices)
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

        # The canvas is its own surface, a shade darker than the window, so
        # the preview reads as a screen rather than as more chrome.
        painter.setPen(QPen(QColor(theme.BORDER), 1))
        painter.setBrush(QBrush(QColor(theme.CANVAS_BG)))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)

        if not self.sessions:
            painter.setPen(QColor(theme.CANVAS_EMPTY_TEXT))
            painter.drawText(self.rect(), Qt.AlignCenter, "Add a strand to preview it here")
            return

        row_h = self._reference_row_height()
        row_count = len(self.sessions)
        stack_h = row_count * (self.LABEL_H + row_h) + max(0, row_count - 1) * self.ROW_GAP
        available_h = self.height() - 2 * self.MARGIN
        y = self.MARGIN + max(0.0, (available_h - stack_h) / 2)

        highlight = self.selected if len(self.selected) > 1 else set()

        for row, session in enumerate(self.sessions):
            strand = session.strand
            n = max(strand.length, 1)
            available_w = self.width() - 2 * self.MARGIN
            led_w = available_w / n
            pad = min(3.0, led_w * 0.15)

            selected = row in highlight
            accent = QColor(theme.FOCUS)

            painter.setPen(accent if selected else QColor(theme.CANVAS_LABEL))
            label = f"{session.config.name}  (group)" if selected else session.config.name
            painter.drawText(
                QRectF(self.MARGIN, y, available_w, self.LABEL_H - self.TRACK_PAD),
                Qt.AlignLeft | Qt.AlignVCenter,
                label,
            )
            row_top = y + self.LABEL_H

            # Track: the unlit strip the LEDs sit on. Gives dark/off pixels a
            # visible body instead of dissolving into the background.
            track = QRectF(
                self.MARGIN - self.TRACK_PAD,
                row_top - self.TRACK_PAD,
                available_w + 2 * self.TRACK_PAD,
                row_h + 2 * self.TRACK_PAD,
            )
            if selected:
                painter.setPen(QPen(accent, 1))
            else:
                painter.setPen(QPen(QColor(theme.BORDER), 1))
            painter.setBrush(QBrush(QColor(theme.BG_PANEL)))
            painter.drawRoundedRect(track, 6, 6)

            for i, color in enumerate(strand.pixels):
                r, g, b = (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF
                x = self.MARGIN + i * led_w
                rect = QRectF(x + pad / 2, row_top, led_w - pad, row_h)

                # Cheap bloom: one low-alpha oversized copy behind a lit
                # pixel. Enough to read as emitted light rather than a
                # colored box, without a real blur's per-frame cost.
                if max(r, g, b) >= self.GLOW_THRESHOLD:
                    glow = QColor(r, g, b)
                    glow.setAlpha(70)
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QBrush(glow))
                    painter.drawRoundedRect(rect.adjusted(-2.5, -2.5, 2.5, 2.5), 5, 5)

                painter.setBrush(QBrush(QColor(r, g, b)))
                painter.setPen(QColor(theme.CANVAS_LED_BEZEL))
                painter.drawRoundedRect(rect, 3, 3)

            y += self.LABEL_H + row_h + self.ROW_GAP
