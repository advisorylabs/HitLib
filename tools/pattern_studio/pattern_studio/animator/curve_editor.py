"""The 17-point curve on a binding.

A binding is a straight line from `at_zero` to `at_full` by default. A curve
reshapes the driver's 0-255 before that line is applied, which is what buys
easing - and, when the curve steps, a threshold. A step-shaped curve *is* a
conditional, which is how this design gets branching without a node graph.

Seventeen points because that is 16 equal segments with integer endpoints:
the lookup is `i = value * 16 / 255`, which needs no special case at either
end and no floating point anywhere near the robot.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..models import CURVE_POINTS

#: The shapes worth one click. Anything else is drawn by hand.
PRESETS: dict[str, list[int]] = {
    "Linear": [round(i * 255 / (CURVE_POINTS - 1)) for i in range(CURVE_POINTS)],
    "Ease In": [round((i / (CURVE_POINTS - 1)) ** 2 * 255) for i in range(CURVE_POINTS)],
    "Ease Out": [round((1 - (1 - i / (CURVE_POINTS - 1)) ** 2) * 255)
                 for i in range(CURVE_POINTS)],
    "Threshold": [0] * 9 + [255] * 8,
}


def is_linear(curve: list[int]) -> bool:
    """Whether a curve is the straight line, which is stored as no curve at
    all - seventeen bytes in every binding would be most of a saved file."""
    return not curve or curve == PRESETS["Linear"]


class CurveWidget(QWidget):
    """A draggable 17-knot curve. x is the driver in, y is the value out."""

    changed = Signal()

    PAD = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(200, 150)
        self.setCursor(Qt.CrossCursor)
        self._points = list(PRESETS["Linear"])
        self._dragging = False

    def curve(self) -> list[int]:
        return list(self._points)

    def set_curve(self, values: list[int]) -> None:
        if values and len(values) == CURVE_POINTS:
            self._points = list(values)
        else:
            self._points = list(PRESETS["Linear"])
        self.update()

    # -- painting ---------------------------------------------------------

    def _plot(self) -> QRectF:
        return QRectF(self.PAD, self.PAD,
                      max(1, self.width() - 2 * self.PAD),
                      max(1, self.height() - 2 * self.PAD))

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = self._plot()

        painter.fillRect(self.rect(), QColor(theme.BG_INPUT))
        # A flat grid, not a graded one: it is a backdrop, not a surface.
        grid = QPen(QColor(theme.BORDER), 1)
        painter.setPen(grid)
        for n in range(1, 4):
            x = box.left() + box.width() * n / 4
            y = box.top() + box.height() * n / 4
            painter.drawLine(QPointF(x, box.top()), QPointF(x, box.bottom()))
            painter.drawLine(QPointF(box.left(), y), QPointF(box.right(), y))
        painter.setPen(QPen(QColor(theme.BORDER_STRONG), 1))
        painter.drawRect(box)

        poly = QPolygonF([self._to_screen(i, v) for i, v in enumerate(self._points)])
        painter.setPen(QPen(QColor(theme.ACCENT), 2))
        painter.drawPolyline(poly)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.ACCENT_HI))
        for point in poly:
            painter.drawEllipse(point, 3.0, 3.0)
        painter.end()

    def _to_screen(self, index: int, value: int) -> QPointF:
        box = self._plot()
        x = box.left() + box.width() * index / (CURVE_POINTS - 1)
        y = box.bottom() - box.height() * value / 255
        return QPointF(x, y)

    # -- editing ----------------------------------------------------------

    def _set_from_pos(self, pos) -> None:
        box = self._plot()
        if box.width() <= 0 or box.height() <= 0:
            return
        index = round((pos.x() - box.left()) / box.width() * (CURVE_POINTS - 1))
        index = max(0, min(CURVE_POINTS - 1, index))
        value = round((box.bottom() - pos.y()) / box.height() * 255)
        self._points[index] = max(0, min(255, value))
        self.update()
        self.changed.emit()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._set_from_pos(event.position())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # Dragging across the width draws the whole curve, which is far
        # quicker than placing seventeen knots one at a time.
        if self._dragging:
            self._set_from_pos(event.position())

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._dragging = False


class CurveDialog(QDialog):
    """Pick or draw the curve on one binding."""

    def __init__(self, curve: list[int], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Binding Curve")
        self.setModal(True)

        self.widget = CurveWidget()
        self.widget.set_curve(curve)

        presets = QHBoxLayout()
        presets.setSpacing(6)
        for name, values in PRESETS.items():
            button = QPushButton(name)
            button.setToolTip(
                "A curve that steps turns this binding into a threshold"
                if name == "Threshold" else f"Set the curve to {name.lower()}"
            )
            button.clicked.connect(lambda _c, v=values: self.widget.set_curve(list(v)))
            presets.addWidget(button)
        presets.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(presets)
        layout.addWidget(self.widget, 1)
        layout.addWidget(buttons)
        self.resize(320, 280)

    def curve(self) -> list[int]:
        """The drawn curve, or an empty list when it is the straight line -
        which is how "no curve" is stored."""
        values = self.widget.curve()
        return [] if is_linear(values) else values
