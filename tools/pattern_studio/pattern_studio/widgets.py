"""Small reusable widgets shared across panels."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QColor, QGradient, QLinearGradient, QPainter
from PySide6.QtWidgets import QColorDialog, QPushButton, QWidget

from . import theme


class ColorButton(QPushButton):
    """A swatch button that opens a color picker and reports 0xRRGGBB ints."""

    color_changed = Signal(int)

    #: Halo radius at rest and under the pointer. The resting glow is what
    #: makes a swatch read as a light source rather than a paint chip; the
    #: hover value is only about twice that, so pointing at one is a nudge
    #: rather than an event.
    BLOOM_REST = 7
    BLOOM_HOVER = 15
    BLOOM_ALPHA = 150

    def __init__(self, initial: int = 0xFFFFFF, parent=None):
        super().__init__(parent)
        self._color = initial & 0xFFFFFF
        self.setFixedSize(52, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to pick a color")
        self.clicked.connect(self._pick)
        # Tinted with the swatch's own color, and re-tinted on every change --
        # a swatch spilling *its* light is the one glow in the app that
        # carries information rather than just polish.
        self._bloom = theme.HoverBloom(
            self,
            self._qcolor(),
            radius=self.BLOOM_HOVER,
            alpha=self.BLOOM_ALPHA,
            resting=self.BLOOM_REST,
        )
        self._refresh_style()

    def color(self) -> int:
        return self._color

    def _qcolor(self) -> QColor:
        return QColor(
            (self._color >> 16) & 0xFF, (self._color >> 8) & 0xFF, self._color & 0xFF
        )

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
        self._bloom.set_color(self._qcolor())

    def _pick(self) -> None:
        r, g, b = (self._color >> 16) & 0xFF, (self._color >> 8) & 0xFF, self._color & 0xFF
        picked = QColorDialog.getColor(QColor(r, g, b), self, "Pick Color")
        if picked.isValid():
            self._color = (picked.red() << 16) | (picked.green() << 8) | picked.blue()
            self._refresh_style()
            self.color_changed.emit(self._color)


#: One tile of the drifting rule. theme.BRAND_SWEEP ends on the color it
#: starts with, which is what lets the tile repeat without a visible seam.
_RULE_STOPS = theme.BRAND_SWEEP


class BrandRule(QWidget):
    """The hairline of brand color under the menu bar, drifting and lit.

    Replaces a flat QSS gradient frame with two things a stylesheet can't do:
    the gradient travels (one full pass takes half a minute, so it's motion
    you notice only if you look for it), and the crisp line sheds a short
    falloff underneath, as if it were casting onto the window below.
    """

    #: Crisp line, then the falloff beneath it. Alphas step down fast -- the
    #: glow should suggest spill, not a second, fuzzier rule.
    LINE_H = 2
    FALLOFF_ALPHAS = (105, 58, 30, 14)

    FRAME_MS = 40  # 25fps
    #: One tile of the sweep, in pixels, and how fast it rolls.
    #:
    #: A fixed tile rather than "one tile per window width": stretched across
    #: a wide window the hue gradient gets so shallow that the roll reads as
    #: static, because what the eye picks up is hue change per second --
    #: speed times the steepness of the gradient it's travelling through. At
    #: these numbers a color stop passes any given point about every 1.3s.
    TILE_PX = 560
    SPEED_PX_PER_SEC = 55

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.LINE_H + len(self.FALLOFF_ALPHAS))
        #: Where the current tile starts, in pixels. Wraps at TILE_PX.
        self._offset = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(self.FRAME_MS)
        self._timer.timeout.connect(self._advance)

    # Driven off visibility rather than started in __init__: no reason to
    # repaint a 6px strip while the window is closed or minimized.
    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().hideEvent(event)
        self._timer.stop()

    def _advance(self) -> None:
        travel = self.SPEED_PX_PER_SEC * self.FRAME_MS / 1000
        self._offset = (self._offset + travel) % self.TILE_PX
        self.update()

    def _gradient(self, alpha: int) -> QLinearGradient:
        """One tile of the wordmark sweep, at the current offset.

        RepeatSpread tiles it across the rest of the widget, so the offset can
        run off the end without leaving a gap -- and because the stops are a
        palindrome, tile boundaries are invisible.
        """
        gradient = QLinearGradient(self._offset, 0, self._offset + self.TILE_PX, 0)
        gradient.setSpread(QGradient.RepeatSpread)
        for i, color in enumerate(_RULE_STOPS):
            tint = QColor(color)
            tint.setAlpha(alpha)
            gradient.setColorAt(i / (len(_RULE_STOPS) - 1), tint)
        return gradient

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        width = self.width()
        painter.fillRect(0, 0, width, self.LINE_H, self._gradient(255))
        for i, alpha in enumerate(self.FALLOFF_ALPHAS):
            painter.fillRect(0, self.LINE_H + i, width, 1, self._gradient(alpha))


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
