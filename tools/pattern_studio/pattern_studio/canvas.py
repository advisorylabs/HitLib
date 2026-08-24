"""Live strip preview: one horizontal row of LEDs per strand session."""

from __future__ import annotations

import math

from PySide6.QtCore import QElapsedTimer, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPen
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

    #: Bloom layers behind each lit pixel, outermost first: (grow_px, alpha).
    #: Two layers instead of one buys a falloff -- a single halo has a hard
    #: outer edge that reads as a colored box around a colored box, whereas a
    #: wide-and-faint layer under a tight-and-brighter one reads as light.
    #: Drawn additively, so halos from neighbouring pixels sum the way real
    #: light does rather than flatly overpainting each other.
    LED_BLOOM = ((6.0, 24), (2.5, 58))

    #: The wash a lit row throws onto the panel around its track, outermost
    #: first: (grow_x, grow_y, alpha). This is the pass that sells the whole
    #: preview as emitted light -- without it every row is a lamp in a
    #: vacuum, lit inside its own bezel and dark 1px outside it.
    SPILL_LAYERS = ((17.0, 12.0, 9), (10.0, 7.0, 16), (4.0, 3.0, 28))
    #: Cap on how many color stops that wash is built from. Strands shorter
    #: than this get one stop per pixel, so the glow sits exactly under the
    #: pixel casting it; longer ones group pixels and anchor each group's
    #: stop on its brightest member, so a lone lit LED still lines up.
    SPILL_BUCKETS = 64

    #: Group-selection outline: a slow breath rather than a static ring, so
    #: "these rows move together" is visible without another label.
    PULSE_MS = 2600
    PULSE_FRAME_MS = 40

    #: Glow passes on that outline, widest first: (pen_width, alpha). Drawn
    #: additively *over* the filled track so the halo spreads both ways from
    #: the line -- an outline that emits, rather than a fatter outline.
    SELECT_GLOW = ((18.0, 15), (10.0, 26), (4.5, 42))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sessions: list = []
        self.selected: set[int] = set()
        self.setMinimumHeight(100)

        # Wall clock rather than a frame counter: the pulse then runs at the
        # same speed whether the canvas is repainting at the engine's rate or
        # only when this timer fires.
        self._clock = QElapsedTimer()
        self._clock.start()
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(self.PULSE_FRAME_MS)
        self._pulse_timer.timeout.connect(self.update)

    def set_sessions(self, sessions: list) -> None:
        self.sessions = sessions
        self.update()

    def set_selected(self, indices) -> None:
        """Rows to outline as the current group-edit selection. Only drawn
        when more than one strand is selected -- with a single selection the
        outline would just be visual noise."""
        self.selected = set(indices)
        # Nothing pulsing means nothing to repaint on a timer; paused strands
        # would otherwise keep the canvas redrawing forever.
        if len(self.selected) > 1:
            self._pulse_timer.start()
        else:
            self._pulse_timer.stop()
        self.update()

    def _reference_row_height(self) -> float:
        usable = (
            self.height()
            - 2 * self.MARGIN
            - self.REFERENCE_ROWS * self.LABEL_H
            - (self.REFERENCE_ROWS - 1) * self.ROW_GAP
        )
        return max(self.MIN_ROW_H, usable / self.REFERENCE_ROWS)

    def _pulse(self) -> float:
        """0..1, breathing on a PULSE_MS cycle."""
        phase = (self._clock.elapsed() % self.PULSE_MS) / self.PULSE_MS
        return 0.5 - 0.5 * math.cos(2 * math.pi * phase)

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def _spill_bands(self, pixels) -> list[tuple[float, int, int, int]] | None:
        """The row's light as (position, r, g, b) stops across its width.

        Position is the fraction of the LED run the stop belongs at. Each band
        takes the per-channel *maximum* of the pixels under it, not their
        mean: one lit pixel in a dark stretch should still throw light, and a
        mean would average it away to nothing.

        Returns None when the whole row is too dark to cast anything, so the
        caller can skip the wash entirely.
        """
        n = len(pixels)
        if n == 0:
            return None
        buckets = min(self.SPILL_BUCKETS, n)
        bands = []
        brightest = 0
        for b in range(buckets):
            lo = b * n // buckets
            hi = max(lo + 1, (b + 1) * n // buckets)
            r = g = bl = 0
            peak = -1
            peak_i = lo
            for i in range(lo, hi):
                color = pixels[i]
                cr, cg, cb = (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF
                r = max(r, cr)
                g = max(g, cg)
                bl = max(bl, cb)
                level = max(cr, cg, cb)
                if level > peak:
                    peak = level
                    peak_i = i
            # Anchored on the band's brightest pixel rather than its midpoint:
            # with a lone lit LED in a band the wash would otherwise sit up to
            # half a band to the side of the thing casting it, which reads as
            # the glow being off-center.
            bands.append(((peak_i + 0.5) / n, r, g, bl))
            brightest = max(brightest, r, g, bl)
        return bands if brightest >= self.GLOW_THRESHOLD else None

    @staticmethod
    def _spill_gradient(bands, x0: float, x1: float) -> QLinearGradient:
        """Full-strength; callers dim it with painter opacity per layer.

        x0/x1 are the ends of the LED run itself, not of the track panel --
        the track is wider by TRACK_PAD on both sides, and stretching the
        stops over that would shift every one of them outward.
        """
        gradient = QLinearGradient(x0, 0, x1, 0)
        for pos, r, g, bl in bands:
            gradient.setColorAt(pos, QColor(r, g, bl))
        return gradient

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # The canvas is its own surface, a shade darker than the window, so
        # the preview reads as a screen rather than as more chrome. Flat, not
        # graded: a gradient this dark spans only a few levels, so Qt renders
        # it as a couple of wide bands with a visible seam between them.
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
        pulse = self._pulse() if highlight else 0.0

        for row, session in enumerate(self.sessions):
            strand = session.strand
            n = max(strand.length, 1)
            available_w = self.width() - 2 * self.MARGIN
            led_w = available_w / n
            pad = min(3.0, led_w * 0.15)

            selected = row in highlight
            accent = QColor(theme.FOCUS)
            if selected:
                # Breathe between "clearly outlined" and "lit up", never all
                # the way down -- the selection has to stay readable at every
                # point in the cycle.
                accent.setAlpha(int(165 + 90 * pulse))

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

            self._paint_spill(
                painter, strand.pixels, track, self.MARGIN, self.MARGIN + available_w
            )
            self._paint_track(painter, track, accent if selected else None, pulse)

            self._paint_leds(painter, strand.pixels, row_top, led_w, pad, row_h)

            y += self.LABEL_H + row_h + self.ROW_GAP

    def _paint_track(
        self, painter: QPainter, track: QRectF, accent: QColor | None, pulse: float
    ) -> None:
        """The unlit strip the LEDs sit on, and its group-selection outline.

        Track first, glow second: the panel fill is opaque, so a halo painted
        underneath would lose everything inside the line and read as a glow
        that only leaks outward.
        """
        painter.setPen(QPen(QColor(theme.BORDER), 1))
        painter.setBrush(QBrush(QColor(theme.BG_PANEL)))
        painter.drawRoundedRect(track, 6, 6)
        if accent is None:
            return

        painter.save()
        painter.setBrush(Qt.NoBrush)
        painter.setCompositionMode(QPainter.CompositionMode_Plus)
        # Breathes along with the line: a constant halo behind a pulsing
        # outline reads as two separate things happening at once.
        strength = 0.55 + 0.45 * pulse
        for width, alpha in self.SELECT_GLOW:
            tint = QColor(accent)
            tint.setAlpha(int(alpha * strength))
            painter.setPen(QPen(tint, width))
            painter.drawRoundedRect(track, 6, 6)
        painter.restore()

        painter.setPen(QPen(accent, 1.4))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(track, 6, 6)

    def _paint_spill(
        self, painter: QPainter, pixels, track: QRectF, x0: float, x1: float
    ) -> None:
        """Wash the row's own light onto the panel around its track."""
        bands = self._spill_bands(pixels)
        if bands is None:
            return
        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode_Plus)
        painter.setPen(Qt.NoPen)
        # One gradient, three passes at different strengths: painter opacity
        # scales the whole brush, so the stop list is built once per row
        # rather than once per layer.
        painter.setBrush(QBrush(self._spill_gradient(bands, x0, x1)))
        for grow_x, grow_y, alpha in self.SPILL_LAYERS:
            painter.setOpacity(alpha / 255)
            painter.drawRoundedRect(
                track.adjusted(-grow_x, -grow_y, grow_x, grow_y), 6 + grow_y, 6 + grow_y
            )
        painter.restore()

    def _paint_leds(
        self, painter: QPainter, pixels, row_top: float, led_w: float, pad: float, row_h: float
    ) -> None:
        """Halos for every lit pixel first, then the pixels themselves.

        Two passes rather than halo-then-body per pixel: it keeps the additive
        composition mode switched once per row instead of twice per LED, and
        it lets adjacent halos sum underneath the whole row rather than being
        clipped by the next pixel's body.
        """
        rects = []
        for i, color in enumerate(pixels):
            x = self.MARGIN + i * led_w
            rects.append((color, QRectF(x + pad / 2, row_top, led_w - pad, row_h)))

        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode_Plus)
        painter.setPen(Qt.NoPen)
        for color, rect in rects:
            r, g, b = (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF
            if max(r, g, b) < self.GLOW_THRESHOLD:
                continue
            for grow, alpha in self.LED_BLOOM:
                glow = QColor(r, g, b)
                glow.setAlpha(alpha)
                painter.setBrush(QBrush(glow))
                painter.drawRoundedRect(rect.adjusted(-grow, -grow, grow, grow), grow + 3, grow + 3)
        painter.restore()

        bezel = QPen(QColor(theme.CANVAS_LED_BEZEL))
        for color, rect in rects:
            r, g, b = (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF
            # Flat: a pixel's body is exactly the color the engine put in the
            # buffer, so the preview stays readable as data. What makes it
            # look emitted is the bloom behind it, not shading on top of it.
            painter.setBrush(QBrush(QColor(r, g, b)))
            painter.setPen(bezel)
            painter.drawRoundedRect(rect, 3, 3)
