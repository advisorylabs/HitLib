"""Drawing a layer frame by frame.

The grid is **x = pixel, y = time**, not x/y space. A HitLib strip is a line
of about twenty LEDs on its own port, never a matrix, so there is no second
spatial axis to draw on. What the vertical axis buys instead is animation: a
horizontal streak is a pixel staying lit, and a diagonal one is a pixel
moving along the strip.

Palette index 0 is transparent rather than black, so a drawing sits over
whatever the layers below it are doing instead of punching a hole in them.
Five bits of index means 32 colours; the rail enforces that.

Each frame either holds still, showing exactly what was drawn, or scrolls at
the layer's Speed. The choice lives in a gutter left of the row it belongs to,
so it is read in the same glance as the drawing it applies to.
"""

from __future__ import annotations

import weakref

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..frame_codec import MAX_DRAWN_FRAMES, MAX_FRAME_COLORS
from ..models import MAX_LEDS, LayerKind
from ..widgets import ColorPicker

#: How wide a cell is drawn. Small enough that twenty pixels across and a
#: dozen frames down fit without scrolling.
CELL = 18

#: The column of scroll toggles left of the cells: one cell, and a gap so the
#: toggles do not read as pixel 0.
GUTTER = CELL + 6

#: What "+" adds, in order, skipping any the drawing already has: bright,
#: saturated and far apart, because that is what reads on an LED.
_FRESH_COLORS = (0xFF0000, 0x00FF00, 0x0000FF, 0xFFFF00, 0x00FFFF, 0xFF00FF,
                 0xFF8000, 0xFFFFFF, 0x8000FF, 0xFF0080)

#: Bounds on how long a frame holds. The runtime stores it in 16 bits, and
#: rounds it to the strand's refresh interval either way.
FRAME_MS_MIN = 10
FRAME_MS_MAX = 10000

#: Most frames a drawing can hold. The buttons stop there rather than the
#: export, which would otherwise be the first to notice.
MAX_FRAMES = MAX_DRAWN_FRAMES


class PixelGrid(QWidget):
    """The drawing surface. Left button paints, right button erases; in the
    gutter, a click toggles whether that frame scrolls."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.CrossCursor)
        # Tracked so the cursor can say which of the two a click will do.
        self.setMouseTracking(True)
        self._rows: list[list[int]] = []
        self._scroll: list[bool] = []
        self._palette: list[int] = [0x000000]
        self._width = 20
        self._brush = 1
        self._onion = True
        self._reverse = False
        self._painting = 0  # 0 none, 1 paint, 2 erase

    # -- contents ---------------------------------------------------------

    def load(self, rows: list[list[int]], palette: list[int], width: int,
             scroll: list[bool] | None = None) -> None:
        """Show @p rows. @p scroll is one flag per row; None keeps the flags
        already here, which is what a recolour or a resize wants."""
        self._width = max(1, width)
        self._palette = list(palette) or [0x000000]
        self._rows = [list(r[:self._width]) + [0] * max(0, self._width - len(r))
                      for r in rows]
        if not self._rows:
            self._rows = [[0] * self._width]
        flags = list(self._scroll if scroll is None else scroll)[:len(self._rows)]
        self._scroll = [bool(f) for f in flags] + [False] * (len(self._rows) - len(flags))
        self._brush = min(self._brush, len(self._palette) - 1) or (
            1 if len(self._palette) > 1 else 0)
        self.updateGeometry()
        self.update()

    def rows(self) -> list[list[int]]:
        return [list(r) for r in self._rows]

    def scroll_flags(self) -> list[bool]:
        return list(self._scroll)

    def set_scroll(self, row: int, on: bool) -> None:
        if 0 <= row < len(self._scroll) and self._scroll[row] != on:
            self._scroll[row] = on
            self.update()
            self.changed.emit()

    def set_all_scroll(self, on: bool) -> None:
        if any(f != on for f in self._scroll):
            self._scroll = [on] * len(self._rows)
            self.update()
            self.changed.emit()

    def set_reverse(self, on: bool) -> None:
        """Which way the gutter's arrows point - the layer's Reverse."""
        self._reverse = on
        self.update()

    def set_brush(self, index: int) -> None:
        self._brush = max(0, min(index, MAX_FRAME_COLORS - 1))

    def set_onion(self, on: bool) -> None:
        self._onion = on
        self.update()

    def add_frame(self, copy_current: bool = False) -> None:
        if len(self._rows) >= MAX_FRAMES:
            return
        source = self._rows[-1] if (copy_current and self._rows) else [0] * self._width
        self._rows.append(list(source))
        # A duplicate is the same frame again, so it moves the same way. A new
        # one holds still, the way a drawing is expected to until asked not to.
        self._scroll.append(bool(copy_current and self._scroll and self._scroll[-1]))
        self.updateGeometry()
        self.update()
        self.changed.emit()

    def remove_frame(self, index: int) -> None:
        # One frame always remains: an empty grid has nothing to draw on, and
        # the layer would validate as "nothing drawn in it" anyway.
        if len(self._rows) > 1 and 0 <= index < len(self._rows):
            self._rows.pop(index)
            if index < len(self._scroll):
                self._scroll.pop(index)
            self.updateGeometry()
            self.update()
            self.changed.emit()

    def clear(self) -> None:
        self._rows = [[0] * self._width for _ in self._rows]
        self.update()
        self.changed.emit()

    def is_blank(self) -> bool:
        return not any(any(row) for row in self._rows)

    # -- geometry ---------------------------------------------------------

    def sizeHint(self):  # noqa: N802 (Qt override)
        return QSize(GUTTER + self._width * CELL + 1, max(1, len(self._rows)) * CELL + 1)

    minimumSizeHint = sizeHint

    def _cell_at(self, pos) -> tuple[int, int] | None:
        if pos.x() < GUTTER:
            return None
        col = int((pos.x() - GUTTER) // CELL)
        row = int(pos.y() // CELL)
        if 0 <= col < self._width and 0 <= row < len(self._rows):
            return row, col
        return None

    def _toggle_at(self, pos) -> int:
        """The frame whose scroll toggle is under @p pos, or -1."""
        if not 0 <= pos.x() < GUTTER - 4:
            return -1
        row = int(pos.y() // CELL)
        return row if 0 <= row < len(self._rows) else -1

    def toggle_rect(self, row: int) -> QRectF:
        return QRectF(1, row * CELL + 1, GUTTER - 6, CELL - 2)

    # -- painting ---------------------------------------------------------

    def _paint_toggle(self, painter: QPainter, row: int) -> None:
        """An arrow the way the frame travels when it scrolls; two short bars,
        a pause, when it holds. Flat, and in the app's live colour when on."""
        rect = self.toggle_rect(row)
        on = row < len(self._scroll) and self._scroll[row]
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(theme.FOCUS if on else theme.BORDER), 1))
        fill = QColor(theme.FOCUS)
        fill.setAlpha(40)
        painter.setBrush(fill if on else Qt.NoBrush)
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)

        c = rect.center()
        if on:
            # Content moves toward pixel 0 unless the layer is reversed.
            d = 1 if self._reverse else -1
            painter.setPen(QPen(QColor(theme.FOCUS), 1.6, Qt.SolidLine, Qt.RoundCap,
                                Qt.RoundJoin))
            painter.drawLine(QPointF(c.x() - 4 * d, c.y()), QPointF(c.x() + 4 * d, c.y()))
            painter.drawLine(QPointF(c.x() + 4 * d, c.y()), QPointF(c.x() + d, c.y() - 3))
            painter.drawLine(QPointF(c.x() + 4 * d, c.y()), QPointF(c.x() + d, c.y() + 3))
        else:
            painter.setPen(QPen(QColor(theme.TEXT_MUTED), 1.6, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(QPointF(c.x() - 2, c.y() - 3), QPointF(c.x() - 2, c.y() + 3))
            painter.drawLine(QPointF(c.x() + 2, c.y() - 3), QPointF(c.x() + 2, c.y() + 3))
        painter.setRenderHint(QPainter.Antialiasing, False)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.CANVAS_BG))

        for row in range(len(self._rows)):
            self._paint_toggle(painter, row)

        for row, frame in enumerate(self._rows):
            for col in range(self._width):
                idx = frame[col] if col < len(frame) else 0
                rect = QRectF(GUTTER + col * CELL, row * CELL, CELL, CELL)
                if idx == 0:
                    # Transparent, drawn as the empty surface rather than as
                    # black - the two mean different things here and a user
                    # has to be able to tell them apart.
                    painter.fillRect(rect.adjusted(1, 1, -1, -1),
                                     QColor(theme.BG_INPUT))
                else:
                    color = self._palette[idx] if idx < len(self._palette) else 0
                    painter.fillRect(rect.adjusted(1, 1, -1, -1), QColor(color))

        # Onion skin: where the frame before was lit, marked faintly on this
        # one, so a shape that is meant to travel can be lined up against
        # where it just was. Drawn as an outline rather than a fill - a fill
        # is indistinguishable from an actual pixel, which is worse than no
        # guide at all.
        #
        # The first frame's "before" is the last one: the strip plays the
        # frames round and round, so that seam is a step like any other and
        # a loop only looks seamless if it can be lined up like the rest.
        if self._onion and len(self._rows) > 1:
            ghost = QPen(QColor(theme.TEXT_MUTED), 1, Qt.DashLine)
            painter.setPen(ghost)
            painter.setBrush(Qt.NoBrush)
            for row in range(len(self._rows)):
                before = self._rows[row - 1]  # row 0 wraps to the last frame
                for col in range(self._width):
                    if before[col] == 0:
                        continue
                    painter.drawRect(
                        QRectF(GUTTER + col * CELL + 2, row * CELL + 2, CELL - 4, CELL - 4))

        painter.setPen(QPen(QColor(theme.BORDER), 1))
        right = GUTTER + self._width * CELL
        for col in range(self._width + 1):
            x = GUTTER + col * CELL
            painter.drawLine(x, 0, x, len(self._rows) * CELL)
        for row in range(len(self._rows) + 1):
            painter.drawLine(GUTTER, row * CELL, right, row * CELL)
        painter.end()

    # -- input ------------------------------------------------------------

    def _apply(self, pos) -> None:
        cell = self._cell_at(pos)
        if cell is None:
            return
        row, col = cell
        value = 0 if self._painting == 2 else self._brush
        if self._rows[row][col] != value:
            self._rows[row][col] = value
            self.update()
            self.changed.emit()

    def event(self, event) -> bool:  # noqa: A003 (Qt's name)
        if event.type() == QEvent.ToolTip:
            row = self._toggle_at(QPointF(event.pos()))
            if row >= 0:
                if self._scroll[row]:
                    text = (f"Frame {row + 1} scrolls at the layer's Speed. Click to hold "
                            "it still, as drawn.")
                else:
                    text = (f"Frame {row + 1} holds still, as drawn. Click to scroll it "
                            "at the layer's Speed.")
                QToolTip.showText(event.globalPos(), text, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        toggle = self._toggle_at(event.position())
        if toggle >= 0:
            if event.button() == Qt.LeftButton:
                self.set_scroll(toggle, not self._scroll[toggle])
            elif event.button() == Qt.RightButton:
                self._toggle_menu(event.globalPosition().toPoint())
            return
        if event.button() == Qt.LeftButton:
            self._painting = 1
        elif event.button() == Qt.RightButton:
            self._painting = 2
        else:
            return
        self._apply(event.position())

    def _toggle_menu(self, where) -> None:
        menu = QMenu(self)
        menu.addAction("Scroll Every Frame", lambda: self.set_all_scroll(True))
        menu.addAction("Hold Every Frame", lambda: self.set_all_scroll(False))
        menu.exec(where)
        # Built per click, so freed rather than kept as a child for good.
        menu.deleteLater()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._painting:
            self._apply(event.position())
            return
        over_toggle = self._toggle_at(event.position()) >= 0
        self.setCursor(Qt.PointingHandCursor if over_toggle else Qt.CrossCursor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._painting = 0


class PaletteRail(QWidget):
    """The drawing's colours, as chips.

    Click a chip to paint with it; double-click it (or right-click it) to
    change what colour it is. Choosing and editing used to be one click on
    the same swatch, so picking a brush meant dismissing a colour dialog
    every time - fine once, and a chore on the fortieth frame.

    One painted widget rather than a row of buttons: up to 31 chips wrap onto
    as many lines as the column needs, which a QHBoxLayout would instead
    push out past the right edge.
    """

    chosen = Signal(int)
    edit_requested = Signal(int)
    remove_requested = Signal(int)
    add_requested = Signal()

    CHIP = 22
    GAP = 7
    #: Room around the chips for the selection ring and the resting glow.
    MARGIN = 4
    RADIUS = 6
    #: Resting glow in each chip's own colour: (grow_px, alpha). The same
    #: idea as ColorButton's bloom - a swatch reads as a light source.
    GLOW = ((3.0, 26), (1.5, 44))

    #: Stand-in index for the "+" cell in hit tests.
    ADD = -2

    def __init__(self, parent=None):
        super().__init__(parent)
        self._colors: list[int] = []  # palette entries 1.., index 0 is transparent
        self._selected = 1
        self._hover = -1
        self._can_add = True
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    # -- contents ---------------------------------------------------------

    def set_palette(self, palette: list[int], can_add: bool) -> None:
        self._colors = list(palette[1:])
        self._can_add = can_add
        self.updateGeometry()
        self.update()

    def set_selected(self, index: int) -> None:
        self._selected = index
        self.update()

    def selected(self) -> int:
        return self._selected

    def cell_rect(self, index: int) -> QRectF:
        """Where palette @p index (or ADD) is drawn."""
        slot = len(self._colors) if index == self.ADD else index - 1
        per_row = self._per_row(self.width())
        row, col = divmod(slot, per_row)
        step = self.CHIP + self.GAP
        return QRectF(self.MARGIN + col * step, self.MARGIN + row * step, self.CHIP, self.CHIP)

    # -- geometry ---------------------------------------------------------

    def _per_row(self, width: int) -> int:
        usable = width - 2 * self.MARGIN + self.GAP
        return max(1, usable // (self.CHIP + self.GAP))

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt override)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt override)
        cells = len(self._colors) + 1
        rows = -(-cells // self._per_row(width))
        return rows * (self.CHIP + self.GAP) - self.GAP + 2 * self.MARGIN

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        width = 2 * self.MARGIN + 10 * (self.CHIP + self.GAP) - self.GAP
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(self.CHIP + 2 * self.MARGIN, self.CHIP + 2 * self.MARGIN)

    def _index_at(self, pos) -> int:
        for index in range(1, len(self._colors) + 1):
            if self.cell_rect(index).contains(pos):
                return index
        if self.cell_rect(self.ADD).contains(pos):
            return self.ADD
        return -1

    # -- painting ---------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        for index, value in enumerate(self._colors, start=1):
            rect = self.cell_rect(index)
            color = QColor((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)

            # Light, not a fill: added onto the panel so it brightens what is
            # behind the chip the way a lit LED would.
            painter.setPen(Qt.NoPen)
            painter.setCompositionMode(QPainter.CompositionMode_Plus)
            for grow, alpha in self.GLOW:
                glow = QColor(color)
                glow.setAlpha(alpha)
                painter.setBrush(glow)
                painter.drawRoundedRect(rect.adjusted(-grow, -grow, grow, grow),
                                        self.RADIUS + grow, self.RADIUS + grow)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

            if index == self._selected:
                # Cyan: the app's "this is live" colour, and the one hue a
                # user's own chip colours are least likely to drown out.
                painter.setPen(QPen(QColor(theme.FOCUS), 2))
                painter.setBrush(Qt.NoBrush)
                painter.drawRoundedRect(rect.adjusted(-3, -3, 3, 3),
                                        self.RADIUS + 3, self.RADIUS + 3)

            border = theme.ACCENT if index == self._hover else theme.BORDER_STRONG
            painter.setPen(QPen(QColor(border), 1))
            painter.setBrush(color)
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), self.RADIUS, self.RADIUS)

        add = self.cell_rect(self.ADD)
        hovered = self._hover == self.ADD and self._can_add
        edge = QColor(theme.ACCENT if hovered else theme.BORDER_STRONG)
        ink = QColor(theme.TEXT if hovered else theme.TEXT_MUTED)
        if not self._can_add:
            edge, ink = QColor(theme.BORDER), QColor(theme.TEXT_DIM)
        painter.setPen(QPen(edge, 1, Qt.DashLine))
        painter.setBrush(QColor(theme.BG_HOVER) if hovered else Qt.NoBrush)
        painter.drawRoundedRect(add.adjusted(0.5, 0.5, -0.5, -0.5), self.RADIUS, self.RADIUS)
        painter.setPen(QPen(ink, 1.5, Qt.SolidLine, Qt.RoundCap))
        c = add.center()
        painter.drawLine(c.x() - 5, c.y(), c.x() + 5, c.y())
        painter.drawLine(c.x(), c.y() - 5, c.x(), c.y() + 5)
        painter.end()

    # -- input ------------------------------------------------------------

    def event(self, event) -> bool:  # noqa: A003 (Qt's name)
        if event.type() == QEvent.ToolTip:
            index = self._index_at(QPointF(event.pos()))
            if index == self.ADD:
                text = ("Add a color" if self._can_add
                        else f"A drawing holds at most {MAX_FRAME_COLORS - 1} colors")
            elif index > 0:
                text = (f"#{self._colors[index - 1]:06X} - click to paint with it, "
                        "double-click to change it")
            else:
                text = ""
            if text:
                QToolTip.showText(event.globalPos(), text, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        hover = self._index_at(event.position())
        if hover != self._hover:
            self._hover = hover
            self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._hover = -1
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        index = self._index_at(event.position())
        if event.button() == Qt.LeftButton:
            if index == self.ADD:
                if self._can_add:
                    self.add_requested.emit()
            elif index > 0:
                self.set_selected(index)
                self.chosen.emit(index)
        elif event.button() == Qt.RightButton and index > 0:
            self.set_selected(index)
            self.chosen.emit(index)
            self._menu(index, event.globalPosition().toPoint())

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 (Qt override)
        index = self._index_at(event.position())
        if event.button() == Qt.LeftButton and index > 0:
            self.edit_requested.emit(index)

    def _menu(self, index: int, where) -> None:
        menu = QMenu(self)
        menu.addAction("Change Color...", lambda: self.edit_requested.emit(index))
        remove = menu.addAction("Remove Color", lambda: self.remove_requested.emit(index))
        # One colour always stays: with none there is nothing to paint with.
        remove.setEnabled(len(self._colors) > 1)
        menu.exec(where)
        # Built per click, so freed rather than kept as a child for good.
        menu.deleteLater()


class PixelGridEditor(QWidget):
    """The grid, its palette rail, and the frame controls."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self._palette: list[int] = [0x000000]
        # How wide this layer is on the strip being previewed - what Fit, and
        # a drawing with nothing in it yet, take their width from. Until the
        # width is set by hand, which a blank drawing then keeps.
        self._fit_width = 20
        self._width_touched = False
        # The layer loaded, and every layer this editor has seen still
        # following the strip. A blank drawing refitted here is not saved
        # until the next edit, so on its next load its stored width can
        # differ from the strip's without anyone having set it - and this is
        # how that is told apart from a width that was. Weak, so a layer
        # that is gone drops out.
        self._layer = None
        self._following: weakref.WeakValueDictionary = weakref.WeakValueDictionary()

        self.grid = PixelGrid()
        self.grid.changed.connect(self._on_grid_changed)

        hint = QLabel("Across = pixel, down = frame. Left-click paints with the "
                      "selected color, right-click erases. The button left of a "
                      "frame makes it scroll at the layer's Speed; frames hold "
                      "still until you do.")
        hint.setProperty("role", "hint")
        # Wrapped, or its one long line sets the minimum width of the whole
        # right-hand column and clips every panel in it.
        hint.setWordWrap(True)

        # The loop's one classic mistake, said where it happens: drawing back
        # to the starting pose shows that picture twice in a row at the wrap.
        self.seam_hint = QLabel(
            "The last frame is a copy of the first, so that picture shows twice "
            "in a row each time the frames loop - a visible hitch. A loop ends one "
            "step before it gets back to the start: press − Frame to drop the copy.")
        self.seam_hint.setProperty("role", "hint")
        self.seam_hint.setWordWrap(True)
        self.seam_hint.setStyleSheet(f"color: {theme.BRAND_PINK};")
        self.seam_hint.hide()

        self.rail = PaletteRail()
        self.rail.chosen.connect(self._choose)
        self.rail.edit_requested.connect(self._edit_color)
        self.rail.remove_requested.connect(self._remove_color)
        self.rail.add_requested.connect(self._add_color)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        buttons2 = QHBoxLayout()
        buttons2.setSpacing(6)
        self.add_frame_btn = QPushButton(theme.icon("plus"), " Frame")
        self.dup_frame_btn = QPushButton("Duplicate")
        self.del_frame_btn = QPushButton(theme.icon("minus"), " Frame")
        self.del_frame_btn.setProperty("role", "danger")
        # Where the Erase brush was. Erasing one pixel is a right-click; what
        # a button is worth having for is erasing all of them.
        self.clear_btn = QPushButton("Clear All")
        self.clear_btn.setProperty("role", "danger")
        self.clear_btn.setToolTip("Erase every pixel in every frame. Asks first.")
        self.onion_btn = QPushButton("Onion Skin")
        self.onion_btn.setCheckable(True)
        self.onion_btn.setChecked(True)
        self.onion_btn.setToolTip(
            "Outline where the frame before was lit, so a shape that travels "
            "can be lined up against where it just was. The first frame shows "
            "the last one, which is the step a loop wraps around on.")
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, MAX_LEDS)
        self.width_spin.setValue(20)
        self.width_spin.setSuffix(" px wide")
        self.width_spin.setToolTip(
            "How many pixels each frame is. A new drawing starts as wide as the "
            "layer is on the strip; wider gives you something to scroll through it.")
        self.fit_btn = QPushButton("Fit")
        self.fit_btn.clicked.connect(lambda: self.width_spin.setValue(self._fit_width))
        self.frame_ms_spin = QSpinBox()
        self.frame_ms_spin.setRange(FRAME_MS_MIN, FRAME_MS_MAX)
        self.frame_ms_spin.setSingleStep(10)
        self.frame_ms_spin.setValue(100)
        self.frame_ms_spin.setSuffix(" ms per frame")
        self.frame_ms_spin.setToolTip(
            "How long each frame stays up before the next one. On the robot "
            "it is rounded to the strand's refresh interval.")
        # Two rows rather than one: seven controls across wanted more width
        # than the column has, and the panel clipped rather than wrapped.
        for b in (self.add_frame_btn, self.dup_frame_btn, self.del_frame_btn):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(self.clear_btn)
        buttons2.addWidget(self.onion_btn)
        buttons2.addWidget(self.width_spin)
        buttons2.addWidget(self.fit_btn)
        buttons2.addWidget(self.frame_ms_spin)
        buttons2.addStretch(1)

        self.add_frame_btn.clicked.connect(lambda: self.grid.add_frame(False))
        self.dup_frame_btn.clicked.connect(lambda: self.grid.add_frame(True))
        self.del_frame_btn.clicked.connect(
            lambda: self.grid.remove_frame(len(self.grid.rows()) - 1))
        self.clear_btn.clicked.connect(self._clear_all)
        self.onion_btn.toggled.connect(self.grid.set_onion)
        self.width_spin.valueChanged.connect(self._on_width)
        self.frame_ms_spin.valueChanged.connect(self._emit)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(hint)
        layout.addWidget(self.rail)
        layout.addWidget(self.grid, 1)
        layout.addWidget(self.seam_hint)
        layout.addLayout(buttons)
        layout.addLayout(buttons2)

    def _emit(self) -> None:
        if not self._loading:
            self.changed.emit()

    def _sync_state(self) -> None:
        """What the controls around the grid say about what is drawn in it."""
        self.clear_btn.setEnabled(not self.grid.is_blank())
        rows = self.grid.rows()
        room = len(rows) < MAX_FRAMES
        for button in (self.add_frame_btn, self.dup_frame_btn):
            button.setEnabled(room)
            button.setToolTip("" if room else
                              f"A drawing holds at most {MAX_FRAMES} frames")
        self.seam_hint.setVisible(len(rows) > 2 and any(rows[0]) and rows[-1] == rows[0])

    def _on_grid_changed(self) -> None:
        self._sync_state()
        self._emit()

    def _on_width(self, width: int) -> None:
        """Resize every frame. Widening pads with transparency, narrowing
        drops the pixels past the new edge - so it is not silently lossy
        either way round, and undo puts them back."""
        if self._loading:
            return
        self._width_touched = True
        if self._layer is not None:
            self._following.pop(id(self._layer), None)
        rows = [list(r[:width]) + [0] * max(0, width - len(r))
                for r in self.grid.rows()]
        self.grid.load(rows, self._palette, width)
        self._sync_state()
        self._emit()

    # -- width ------------------------------------------------------------

    def set_fit_width(self, width: int, strip_length: int | None = None) -> None:
        """How wide this layer is on the strip. A drawing with nothing in it
        yet follows it, so a new drawing is as wide as the strip it plays on;
        one that has been drawn, or given a width by hand, keeps its width
        until Fit is pressed."""
        self._fit_width = max(1, min(width, MAX_LEDS))
        where = f"on the {strip_length} px preview strip" if strip_length else "on the strip"
        self.fit_btn.setToolTip(f"Make every frame {self._fit_width} px wide - as wide "
                                f"as this layer is {where}")
        self._fit_if_blank()

    def _fit_if_blank(self) -> None:
        """Resize an untouched drawing to the strip, quietly: nothing is drawn,
        so nothing changes that the preview or an undo step could show. The
        next real edit saves the width along with it."""
        if (self._width_touched or not self.grid.is_blank()
                or self.width_spin.value() == self._fit_width):
            return
        was_loading, self._loading = self._loading, True
        self.width_spin.setValue(self._fit_width)
        self._loading = was_loading
        self.grid.load([[0] * self._fit_width for _ in self.grid.rows()], self._palette,
                       self._fit_width)

    def set_reverse(self, on: bool) -> None:
        self.grid.set_reverse(on)

    # -- clearing ---------------------------------------------------------

    def _clear_all(self) -> None:
        if self.grid.is_blank() or not self._confirm_clear():
            return
        self.grid.clear()

    def _confirm_clear(self) -> bool:
        """Ask before wiping the drawing. Separate so tests can answer it."""
        frames = len(self.grid.rows())
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Clear Drawing")
        plural = "s" if frames != 1 else ""
        box.setText(f"Clear all {frames} frame{plural}?")
        box.setInformativeText(
            "Every pixel goes back to transparent. The frames and colors stay, "
            "and Edit > Undo brings the drawing back.")
        clear = box.addButton("Clear All", QMessageBox.DestructiveRole)
        cancel = box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        confirmed = box.clickedButton() is clear
        # Built per click, so freed rather than kept as a child for good.
        box.deleteLater()
        return confirmed

    # -- palette ----------------------------------------------------------

    def _refresh_rail(self) -> None:
        self.rail.set_palette(self._palette, len(self._palette) < MAX_FRAME_COLORS)

    def selected_color(self) -> int:
        """The palette index the left button paints with."""
        return self.rail.selected()

    def _choose(self, index: int) -> None:
        index = max(1, min(index, len(self._palette) - 1))
        self.grid.set_brush(index)
        self.rail.set_selected(index)

    def _recolor(self, index: int, color: int) -> None:
        """Because the grid stores indices, changing a swatch recolours every
        pixel drawn with it at once."""
        if 0 < index < len(self._palette):
            self._palette[index] = color
            self.grid.load(self.grid.rows(), self._palette, self.grid._width)
            self._refresh_rail()
            self._emit()

    def _edit_color(self, index: int) -> ColorPicker | None:
        if not 0 < index < len(self._palette):
            return None
        self._choose(index)
        picker = ColorPicker(self._palette[index], self.rail)
        picker.color_changed.connect(lambda c, i=index: self._recolor(i, c))
        picker.popup_under(self.rail, self.rail.cell_rect(index).toAlignedRect())
        return picker

    def _add_color(self) -> ColorPicker | None:
        """Add a colour, select it, and open it for adjusting - adding one
        and then choosing what it is are the same moment."""
        if len(self._palette) >= MAX_FRAME_COLORS:
            return None
        fresh = next((c for c in _FRESH_COLORS if c not in self._palette), 0xFFFFFF)
        self._palette.append(fresh)
        # The grid draws from its own copy of the palette, and one entry
        # short it drew the new colour's cells black until a recolour.
        self.grid.load(self.grid.rows(), self._palette, self.grid._width)
        self._refresh_rail()
        index = len(self._palette) - 1
        self._choose(index)
        self._emit()
        return self._edit_color(index)

    def _remove_color(self, index: int) -> None:
        """Drop a colour. Pixels drawn with it go transparent, and every
        index above it moves down one so the rest of the drawing keeps its
        colours."""
        if not 0 < index < len(self._palette) or len(self._palette) <= 2:
            return
        rows = [[0 if v == index else (v - 1 if v > index else v) for v in row]
                for row in self.grid.rows()]
        self._palette.pop(index)
        self.grid.load(rows, self._palette, self.grid._width)
        self._refresh_rail()
        selected = self.rail.selected()
        self._choose(selected - 1 if selected > index else selected)
        self._sync_state()
        self._emit()

    # -- load / save ------------------------------------------------------

    def load(self, layer) -> None:
        self._loading = True
        self._layer = layer
        # A drawing stored at another width than the strip gives it had that
        # width set by hand - unless it is one this editor refitted itself
        # and has not saved yet. Taken for untouched, every reload refitted
        # it, and the next edit saved the fit over the width the user chose.
        # Only a drawing: every other layer passes through here too, with a
        # width nobody has seen.
        self._width_touched = (id(layer) not in self._following
                               and layer.kind == LayerKind.FRAMES
                               and layer.frame_width != self._fit_width)
        if not self._width_touched:
            self._following[id(layer)] = layer
        self._palette = list(layer.frame_palette) or [0x000000]
        if len(self._palette) < 2:
            self._palette.append(0xFF0000)
        self._refresh_rail()
        self.width_spin.setValue(max(1, layer.frame_width))
        self.frame_ms_spin.setValue(max(FRAME_MS_MIN, min(FRAME_MS_MAX, layer.frame_ms)))
        self.grid.load(layer.frames, self._palette, layer.frame_width, layer.scroll_flags())
        self.grid.set_reverse(layer.invert)
        self._choose(1)
        self._sync_state()
        self._fit_if_blank()
        self._loading = False

    def save(self, layer) -> None:
        layer.frames = self.grid.rows()
        layer.frame_scroll = self.grid.scroll_flags()
        layer.frame_palette = list(self._palette)
        layer.frame_width = self.width_spin.value()
        layer.frame_ms = self.frame_ms_spin.value()

