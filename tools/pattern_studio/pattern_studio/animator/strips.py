"""Dragging a layer's span and its timing, instead of typing numbers.

Both surfaces exist for the same reason. The Design tab's splice panel asks
for a start and a width as spin boxes, and that is bad enough that a "Divide"
button had to be invented to do the arithmetic for people. A layer stack has
the same problem twice over - once in space, once in time - so the Animator
shows both as bars you drag.

`SpanRuler` sits directly under the preview and uses the canvas's own pixel
geometry, so a bar lines up with the LEDs it owns. `TimelineStrip` is the
composition's loop across the width, with each layer's window as a bar and
its fades as ramps at the ends. Dragging two windows until they overlap and
pulling their fades out *is* a crossfade; there is nothing else to learn.

Esc cancels a drag in progress and puts the value back, which matters because
a stray drag is the single most likely way to lose work in a tab made of
draggable things.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .. import theme
from ..models import LayerConfig, LayerKind

#: Height of one layer's bar, and the gap under it.
ROW_H = 15
ROW_GAP = 3
#: How close to an end counts as grabbing that end rather than the body.
EDGE_PX = 5
#: Left/right inset. Matches StripCanvas.MARGIN so a span bar lines up with
#: the LED it names.
MARGIN = 12
#: What a timeline drag snaps to. A window placed to the millisecond by
#: dragging is false precision, and the round numbers are what people mean.
SNAP_MS = 10


def _layer_color(layer: LayerConfig) -> QColor:
    """A bar's colour: whatever the layer mostly paints with.

    A muted layer greys out rather than disappearing - it is still in the
    stack, and its span still matters when it comes back.
    """
    if layer.muted:
        return QColor(theme.BORDER_STRONG)
    if layer.kind == LayerKind.TWINKLE and layer.palette:
        return QColor(layer.palette[0])
    if layer.kind == LayerKind.BITSCROLL and layer.segments:
        return QColor(layer.segments[0].color)
    if layer.kind == LayerKind.FRAMES and len(layer.frame_palette) > 1:
        return QColor(layer.frame_palette[1])
    if layer.kind == LayerKind.OFF:
        return QColor(theme.BORDER_STRONG)
    return QColor(layer.color)


class _DragStrip(QWidget):
    """Shared mechanics: rows of bars, one per layer, dragged by body or end.

    Subclasses say where a layer's bar goes (`_bar_range`), what a dragged
    end means (`_commit`) and what moving the whole bar does (`_shift`).

    A bar is only a picture of the layer: clipped to the track, so a span
    longer than the preview and a window across the loop point both draw
    shorter than they are. So a drag works from the layer's own values
    (`_fields`), never from the bar it drew, and Esc puts those back.
    """

    #: A drag finished, or a bar was nudged. One undo step per burst upstream.
    changed = Signal()
    #: A bar was clicked. The panel selects that layer.
    selected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._layers: list[LayerConfig] = []
        self._current = -1
        self._drag: dict | None = None

    def set_layers(self, layers: list[LayerConfig], current: int = -1) -> None:
        self._layers = layers
        self._current = current
        self.setFixedHeight(max(ROW_H + ROW_GAP,
                                len(layers) * (ROW_H + ROW_GAP) + ROW_GAP))
        self.update()

    def set_current(self, row: int) -> None:
        self._current = row
        self.update()

    # -- geometry ---------------------------------------------------------

    def _track(self) -> QRectF:
        return QRectF(MARGIN, 0, max(1.0, self.width() - 2 * MARGIN), self.height())

    def _row_top(self, index: int) -> float:
        return ROW_GAP + index * (ROW_H + ROW_GAP)

    def _row_at(self, y: float) -> int:
        for i in range(len(self._layers)):
            top = self._row_top(i)
            if top <= y < top + ROW_H:
                return i
        return -1

    def _bar_range(self, layer: LayerConfig) -> tuple[float, float]:
        """(start, end) as fractions of the track, 0..1."""
        raise NotImplementedError

    def _commit(self, layer: LayerConfig, lo: float, hi: float) -> None:
        """Write a dragged (start, end) fraction pair back onto the layer.
        An end the drag has not moved keeps the value the layer has."""
        raise NotImplementedError

    def _shift(self, layer: LayerConfig, delta: float) -> None:
        """Move the layer @p delta of the track along without resizing it."""
        raise NotImplementedError

    def _fields(self, layer: LayerConfig) -> tuple:
        """The layer's own values this strip edits, exactly as stored."""
        raise NotImplementedError

    def _restore(self, layer: LayerConfig, fields: tuple) -> None:
        """Put back what `_fields` read."""
        raise NotImplementedError

    def _extra_bars(self, layer: LayerConfig) -> list[tuple[float, float]]:
        """Any ghost bars to draw at low alpha - a stride's repeats."""
        return []

    def _empty_note(self) -> str:
        return ""

    # -- painting ---------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(theme.BG_PANEL))
        track = self._track()

        note = self._empty_note()
        if note:
            painter.setPen(QColor(theme.TEXT_DIM))
            painter.drawText(self.rect(), Qt.AlignCenter, note)
            painter.end()
            return

        for i, layer in enumerate(self._layers):
            top = self._row_top(i)
            painter.fillRect(QRectF(track.left(), top, track.width(), ROW_H),
                             QColor(theme.BG_INPUT))

            color = _layer_color(layer)
            for lo, hi in self._extra_bars(layer):
                ghost = QColor(color)
                ghost.setAlpha(70)
                painter.fillRect(self._bar_rect(track, top, lo, hi), ghost)

            lo, hi = self._bar_range(layer)
            rect = self._bar_rect(track, top, lo, hi)
            # Flat fill plus a border, never a graded one: a 15px bar in a
            # dark ramp bands badly, and flat reads as deliberate.
            fill = QColor(color)
            fill.setAlpha(210 if i == self._current else 150)
            painter.fillRect(rect, fill)

            if i == self._current:
                painter.setPen(QPen(QColor(theme.FOCUS), 1))
                painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
            self._paint_extra(painter, layer, rect)
        painter.end()

    @staticmethod
    def _bar_rect(track: QRectF, top: float, lo: float, hi: float) -> QRectF:
        x0 = track.left() + track.width() * max(0.0, min(1.0, lo))
        x1 = track.left() + track.width() * max(0.0, min(1.0, hi))
        return QRectF(x0, top, max(3.0, x1 - x0), ROW_H)

    def _paint_extra(self, painter: QPainter, layer: LayerConfig,
                     rect: QRectF) -> None:
        return

    # -- dragging ---------------------------------------------------------

    def _hit(self, pos) -> tuple[int, str] | None:
        row = self._row_at(pos.y())
        if row < 0 or row >= len(self._layers):
            return None
        track = self._track()
        lo, hi = self._bar_range(self._layers[row])
        rect = self._bar_rect(track, self._row_top(row), lo, hi)
        if abs(pos.x() - rect.left()) <= EDGE_PX:
            return row, "left"
        if abs(pos.x() - rect.right()) <= EDGE_PX:
            return row, "right"
        if rect.left() <= pos.x() <= rect.right():
            return row, "body"
        return row, "none"

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._drag is None:
            hit = self._hit(event.position())
            edge = hit is not None and hit[1] in ("left", "right")
            self.setCursor(Qt.SizeHorCursor if edge else Qt.ArrowCursor)
            return

        track = self._track()
        frac = (event.position().x() - track.left()) / track.width()
        lo, hi = self._drag["lo"], self._drag["hi"]
        delta = frac - self._drag["grab"]

        layer = self._layers[self._drag["row"]]
        # Every move starts over from what the press found, so a drag that
        # comes back to where it began leaves the layer as it was.
        self._restore(layer, self._drag["undo"])
        if self._drag["edge"] == "body":
            self._shift(layer, delta)
        else:
            if self._drag["edge"] == "left":
                lo = min(lo + delta, hi - 1e-6)
            else:
                hi = max(hi + delta, lo + 1e-6)
            self._commit(layer, max(0.0, lo), min(1.0, hi))
        self.update()
        self.changed.emit()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() != Qt.LeftButton:
            return
        hit = self._hit(event.position())
        if hit is None:
            return
        row, edge = hit
        self.selected.emit(row)
        if edge == "none":
            return
        track = self._track()
        lo, hi = self._bar_range(self._layers[row])
        self._drag = {
            "row": row, "edge": edge, "lo": lo, "hi": hi,
            "grab": (event.position().x() - track.left()) / track.width(),
            # Kept so Esc can put it back. A stray drag is the likeliest way
            # to lose work in a tab made of draggable things. The layer's
            # values, not the bar's: putting back what a clipped bar showed
            # did not put back what was there.
            "undo": self._fields(self._layers[row]),
        }

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._drag = None

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.key() == Qt.Key_Escape and self._drag is not None:
            layer = self._layers[self._drag["row"]]
            self._restore(layer, self._drag["undo"])
            self._drag = None
            self.update()
            self.changed.emit()
            return
        super().keyPressEvent(event)


class SpanRuler(_DragStrip):
    """Which pixels each layer owns, on the strip's own axis.

    Lines up with the preview above it, so "this layer covers those LEDs" is
    something you see rather than something you work out from two numbers.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._length = 20
        self.setToolTip("Drag a bar to move it, an end to resize it. Esc cancels.")

    def set_length(self, length: int) -> None:
        self._length = max(1, length)
        self.update()

    def _span(self, layer: LayerConfig) -> tuple[int, int]:
        start = min(layer.start, self._length)
        width = layer.width if layer.width else (self._length - start)
        return start, max(1, min(width, self._length - start))

    def _bar_range(self, layer: LayerConfig) -> tuple[float, float]:
        start, width = self._span(layer)
        return start / self._length, (start + width) / self._length

    def _extra_bars(self, layer: LayerConfig) -> list[tuple[float, float]]:
        if not layer.stride:
            return []
        start, width = self._span(layer)
        out = []
        at = start + layer.stride
        while at < self._length:
            out.append((at / self._length,
                        min(at + width, self._length) / self._length))
            at += layer.stride
        return out

    def _fields(self, layer: LayerConfig) -> tuple:
        return layer.start, layer.width

    def _restore(self, layer: LayerConfig, fields: tuple) -> None:
        layer.start, layer.width = fields

    def _commit(self, layer: LayerConfig, lo: float, hi: float) -> None:
        length = self._length
        shown_lo, shown_hi = self._bar_range(layer)
        start_moved = round(lo * length) != round(shown_lo * length)
        end_moved = round(hi * length) != round(shown_hi * length)
        if not (start_moved or end_moved):
            # Not a whole pixel either way. Re-reading the bar would still
            # clip a span that runs on past the preview.
            return
        start = max(0, min(length - 1, round(lo * length)))
        end = max(start + 1, min(length, round(hi * length)))
        if layer.width and not end_moved:
            # Only the start moved, so the end stays where the layer has it -
            # past the preview, for a span longer than the preview shows.
            end = max(start + 1, layer.start + layer.width)
        layer.start = start
        # A width of 0 means "to the end of the strand". Dragging an end is a
        # statement that the width is this and not that, so it materializes -
        # otherwise the bar would snap back to the end on the next repaint.
        layer.width = 0 if end == length and start == 0 else end - start

    def _shift(self, layer: LayerConfig, delta: float) -> None:
        start, shown = self._span(layer)
        # A width of its own is kept exactly, even where it runs on past the
        # preview; "to the end" becomes the width the bar shows, as moving it
        # always has made it.
        width = layer.width or shown
        # Kept on the preview, but never pulled back by the move itself: a
        # span already running past the end stays put until moved left.
        to = max(0, min(max(start, self._length - width),
                        start + round(delta * self._length)))
        if to != start:
            layer.start = to
            layer.width = width


class TimelineStrip(_DragStrip):
    """When each layer is on, across the composition's loop.

    A layer with no window draws full width and says so. Two overlapping
    windows with fades pulled out are a crossfade.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loop_ms = 0
        self._position_ms = 0
        self.setToolTip(
            "Drag a bar to move when a layer runs, an end to resize it. "
            "Overlap two with fades to cross-fade. Esc cancels.")

    def set_loop_ms(self, loop_ms: int) -> None:
        self._loop_ms = max(0, loop_ms)
        self.update()

    def set_position_ms(self, position_ms: int) -> None:
        """Where the playhead sits. Driven off the preview's own clock."""
        self._position_ms = position_ms
        if self._loop_ms:
            self.update()

    def _empty_note(self) -> str:
        if self._loop_ms:
            return ""
        return "Set a loop length to place layers in time"

    def _window(self, layer: LayerConfig) -> tuple[int, int]:
        if layer.start_ms == 0 and layer.end_ms == 0:
            return 0, self._loop_ms
        end = layer.end_ms if layer.end_ms else self._loop_ms
        return layer.start_ms, end

    def _bar_range(self, layer: LayerConfig) -> tuple[float, float]:
        if not self._loop_ms:
            return 0.0, 1.0
        start, end = self._window(layer)
        if end <= start:
            # A window that runs across the loop point. Drawn to the end
            # here; the wrapped remainder is a ghost bar.
            return start / self._loop_ms, 1.0
        return start / self._loop_ms, end / self._loop_ms

    def _extra_bars(self, layer: LayerConfig) -> list[tuple[float, float]]:
        if not self._loop_ms:
            return []
        start, end = self._window(layer)
        if 0 < end <= start:
            return [(0.0, end / self._loop_ms)]
        return []

    def _paint_extra(self, painter: QPainter, layer: LayerConfig,
                     rect: QRectF) -> None:
        """The fades, as ramps at the ends of the bar."""
        if not self._loop_ms or (not layer.fade_in_ms and not layer.fade_out_ms):
            return
        track = self._track()
        per_ms = track.width() / self._loop_ms
        shade = QColor(theme.BG_PANEL)
        shade.setAlpha(150)
        if layer.fade_in_ms:
            w = min(layer.fade_in_ms * per_ms, rect.width())
            painter.fillRect(QRectF(rect.left(), rect.top(), w, ROW_H * 0.45), shade)
        if layer.fade_out_ms:
            w = min(layer.fade_out_ms * per_ms, rect.width())
            painter.fillRect(QRectF(rect.right() - w, rect.top(), w, ROW_H * 0.45),
                             shade)

    def _fields(self, layer: LayerConfig) -> tuple:
        return layer.start_ms, layer.end_ms

    def _restore(self, layer: LayerConfig, fields: tuple) -> None:
        layer.start_ms, layer.end_ms = fields

    def _snap(self, frac: float) -> int:
        """A track fraction as a time on the SNAP_MS grid - or the loop point
        itself, a snap target of its own: a loop that is not a whole number
        of steps would otherwise have no way to reach its end, and a window
        dragged there stopped a few ms short of it, dark for those ms of
        every loop."""
        raw = frac * self._loop_ms
        at = round(raw / SNAP_MS) * SNAP_MS
        if at >= self._loop_ms or self._loop_ms - raw <= abs(raw - at):
            return self._loop_ms
        return max(0, at)

    def _commit(self, layer: LayerConfig, lo: float, hi: float) -> None:
        if not self._loop_ms:
            return
        loop = self._loop_ms
        shown_lo, shown_hi = self._bar_range(layer)
        start_moved = self._snap(lo) != self._snap(shown_lo)
        end_moved = self._snap(hi) != self._snap(shown_hi)
        if not (start_moved or end_moved):
            return
        start, end = self._window(layer)
        # Across the loop point, where the bar stops but the window does not.
        wrapped = end <= start
        if start_moved:
            # As late as a window can start is one step before the loop
            # point. One starting on it has its end at or before its start,
            # which the engine reads as running round the whole loop - so
            # dragging a window shut from the left turned it fully on.
            start = min(self._snap(lo), loop - SNAP_MS)
        if end_moved:
            end = self._snap(hi)
        if wrapped and not end_moved:
            # Only its start moved, and that cannot pass the end it wraps to.
            start = max(start, end + SNAP_MS)
        elif end <= start:
            end = min(loop, start + SNAP_MS)
            end_moved = True
        if start_moved:
            layer.start_ms = start
        if end_moved:
            # Reaching the loop point is stored as 0, which is what "to the
            # loop point" means to the firmware - and keeps the window correct
            # if the loop length changes later.
            layer.end_ms = 0 if end >= loop else end
        if layer.start_ms == 0 and layer.end_ms == 0:
            # Dragged back to covering everything: that is "always on", and
            # the panel's checkbox should read that way too.
            pass

    def _shift(self, layer: LayerConfig, delta: float) -> None:
        if not self._loop_ms or (layer.start_ms == 0 and layer.end_ms == 0):
            return  # always on: the bar spans the loop and has nowhere to go
        loop = self._loop_ms
        moved = round(delta * loop / SNAP_MS) * SNAP_MS
        if not moved:
            return
        start, end = self._window(layer)
        if end <= start:
            # Across the loop point: carried round the loop, so it stays the
            # same window. Moved as the bar it draws, it was cut off at the
            # loop point and the part after the wrap was lost.
            layer.start_ms = (start + moved) % loop
            layer.end_ms = (end + moved) % loop
            return
        length = max(1, min(end, loop) - start)
        to = max(0, min(loop - length, start + moved))
        if to != start:
            layer.start_ms = to
            layer.end_ms = 0 if to + length >= loop else to + length

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().paintEvent(event)
        if not self._loop_ms or not self._layers:
            return
        painter = QPainter(self)
        track = self._track()
        at = (self._position_ms % self._loop_ms) / self._loop_ms
        x = track.left() + track.width() * at
        painter.setPen(QPen(QColor(theme.FOCUS), 1))
        painter.drawLine(int(x), 0, int(x), self.height())
        painter.end()
