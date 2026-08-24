"""Built-in window chrome: the app's own title bar, caption buttons and
resize edges, for a frameless main window.

Why take the frame over from the OS: with the native caption bar the app gets
a strip of grey system chrome above its own dark UI, and the menu bar sits in
a second strip below it -- two bands of furniture before any content. Folding
the logo, the menus, the title and the caption buttons into one 36px row is
both tidier and one row shorter.

What that costs, and how it's paid back:

* Dragging and resizing are gone with the frame. Both come back through
  QWindow.startSystemMove()/startSystemResize(), which hand the gesture to
  the compositor rather than reimplementing it with mouse deltas. Handing it
  over isn't enough on its own to get Aero Snap back, though: Windows gates
  the snap zones on the window's style bits, which a frameless window loses
  along with its frame. See enable_native_snap().
* Resize edges need something to hit. Eight thin grip widgets sit over the
  window's border, each with its own cursor, instead of an application-wide
  mouse filter -- widgets get cursor handling from Qt for free, a filter
  would have to set and restore the cursor on whatever child is underneath.
* Windows 11 squares off the corners of a frameless window. A DWM attribute
  asks for the rounded ones back.

Snap layouts (hovering the maximize button on Windows 11) need one more
thing: the window has to answer WM_NCHITTEST with HTMAXBUTTON over that
button, which makes Windows treat it as the caption's real maximize control.
The cost is that the button then stops being a Qt widget as far as the mouse
is concerned -- hover and clicks arrive as WM_NC* messages instead -- so
those are translated back. See caption_message().
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QObject, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QHBoxLayout,
    QLabel,
    QMenuBar,
    QSizePolicy,
    QWidget,
)

from . import theme

#: Height of the whole chrome row.
TITLE_H = 36
#: Caption buttons keep the platform's proportions -- 46px wide is what
#: Windows uses, and muscle memory for the close button is real.
CAPTION_W = 46
#: How close to the edge counts as a resize grab.
GRIP_PX = 5

# The slice of Win32 this file needs. Named here rather than inline so the
# message handling below reads as intent instead of as hex.
WM_NCHITTEST = 0x0084
WM_NCMOUSEMOVE = 0x00A0
WM_NCLBUTTONDOWN = 0x00A1
WM_NCLBUTTONUP = 0x00A2
WM_NCMOUSELEAVE = 0x02A2
HTMAXBUTTON = 9
GWL_STYLE = -16
WS_MAXIMIZEBOX = 0x00010000
WS_THICKFRAME = 0x00040000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020


class _CaptionButton(QAbstractButton):
    """Minimize / maximize / restore / close.

    The glyphs are painted rather than loaded: they're three straight-line
    drawings, and at this size a hand-placed 1px stroke stays crisp where a
    scaled SVG picks up half-pixel blur.
    """

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        # Set from outside when Windows owns the mouse over this button --
        # claiming HTMAXBUTTON for snap layouts means Qt stops seeing enter,
        # leave and press events here.
        self.native_hover = False
        self.native_down = False
        self.setFixedSize(CAPTION_W, TITLE_H)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_Hover, True)

    def set_native_state(self, hover: bool | None = None, down: bool | None = None) -> None:
        if hover is not None:
            self.native_hover = hover
        if down is not None:
            self.native_down = down
        self.update()

    @property
    def hot(self) -> bool:
        return self.underMouse() or self.native_hover

    def set_kind(self, kind: str) -> None:
        self.kind = kind
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(CAPTION_W, TITLE_H)

    def _hover_color(self) -> QColor:
        if self.kind == "close":
            return QColor(232, 17, 35, 235)  # the one control worth a red flash
        return QColor(255, 255, 255, 22)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        pressed = self.isDown() or self.native_down
        if pressed or self.hot:
            color = self._hover_color()
            if pressed:
                color.setAlpha(min(255, color.alpha() + 30))
            painter.fillRect(self.rect(), color)

        stroke = QColor("#FFFFFF") if (self.kind == "close" and self.hot) else QColor(theme.TEXT)
        painter.setPen(QPen(stroke, 1.1))
        cx, cy = self.width() / 2, self.height() / 2
        r = 5.0

        if self.kind == "minimize":
            painter.drawLine(int(cx - r), int(cy), int(cx + r), int(cy))
        elif self.kind == "maximize":
            painter.drawRect(QRect(int(cx - r), int(cy - r), int(2 * r), int(2 * r)))
        elif self.kind == "restore":
            # Two offset squares: the front one is the window coming back to
            # its smaller size, the one behind is where it was.
            painter.drawRect(QRect(int(cx - r), int(cy - r + 2), int(2 * r - 2), int(2 * r - 2)))
            painter.drawLine(int(cx - r + 2), int(cy - r), int(cx + r), int(cy - r))
            painter.drawLine(int(cx + r), int(cy - r), int(cx + r), int(cy + r - 2))
        else:  # close
            painter.drawLine(int(cx - r), int(cy - r), int(cx + r), int(cy + r))
            painter.drawLine(int(cx + r), int(cy - r), int(cx - r), int(cy + r))


class TitleBar(QWidget):
    """Logo, menus, title, caption buttons -- left to right, in one row."""

    #: 20px off the .ico's 24px frame -- close enough to the source size that
    #: the shield's linework survives the downscale.
    LOGO_PX = 20

    def __init__(self, window: QWidget, parent=None):
        super().__init__(parent)
        self._window = window
        self.setObjectName("titleBar")
        self.setFixedHeight(TITLE_H)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 0, 0)
        layout.setSpacing(0)

        self.logo = QLabel()
        self.logo.setPixmap(
            QIcon(str(theme.resource_dir() / "hitliblogo.ico")).pixmap(
                self.LOGO_PX, self.LOGO_PX
            )
        )
        # Sized to the artwork: a wider label would pad the gap to the first
        # menu invisibly, which is most of how it got too wide before.
        self.logo.setFixedWidth(self.LOGO_PX)
        # No spacer after it: the menu bar's own margin plus the first item's
        # padding already stand off ~14px, and stacking a third gap on top of
        # those two is how the logo ended up marooned.
        layout.addWidget(self.logo, 0, Qt.AlignVCenter)

        # A plain QMenuBar rather than QMainWindow.menuBar(): the main window
        # would lay its own out in a strip of its own, which is the strip
        # this class exists to get rid of.
        self.menu_bar = QMenuBar(self)
        self.menu_bar.setNativeMenuBar(False)
        # Height-hugging and centered, not stretched: a QMenuBar lays its
        # items out from its top edge, so stretching it to the full bar left
        # "File" and "Export" sitting 5px above the title's baseline. The
        # policy (rather than a fixed height) is because the menus don't
        # exist yet -- the window adds them after this runs.
        self.menu_bar.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        layout.addWidget(self.menu_bar, 0, Qt.AlignVCenter)

        layout.addSpacing(9)
        self.title_label = QLabel()
        self.title_label.setObjectName("appTitle")
        layout.addWidget(self.title_label, 0, Qt.AlignVCenter)
        layout.addSpacing(7)
        self.subtitle_label = QLabel()
        self.subtitle_label.setObjectName("appSubtitle")
        layout.addWidget(self.subtitle_label, 0, Qt.AlignVCenter)

        layout.addStretch(1)

        self.minimize_btn = _CaptionButton("minimize", self)
        self.maximize_btn = _CaptionButton("maximize", self)
        self.close_btn = _CaptionButton("close", self)
        for button in (self.minimize_btn, self.maximize_btn, self.close_btn):
            layout.addWidget(button)

        self.minimize_btn.clicked.connect(self._window.showMinimized)
        self.maximize_btn.clicked.connect(self.toggle_maximized)
        self.close_btn.clicked.connect(self._window.close)

    # ------------------------------------------------------------------
    # Contents
    # ------------------------------------------------------------------

    def set_title(self, name: str, version: str, file_name: str | None = None) -> None:
        self.title_label.setText(name)
        trailer = f"v{version}"
        if file_name:
            trailer += f"  --  {file_name}"
        self.subtitle_label.setText(trailer)

    # ------------------------------------------------------------------
    # Window state
    # ------------------------------------------------------------------

    def toggle_maximized(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

    def maximize_hit(self, global_pos: QPoint) -> bool:
        """Is that screen position over the maximize button?"""
        return self.maximize_btn.rect().contains(
            self.maximize_btn.mapFromGlobal(global_pos)
        )

    def sync_window_state(self) -> None:
        """Point the middle button at whichever action is available now.

        Called by the window from changeEvent rather than watched from an
        event filter here: a filter installed on an object that outlives this
        one keeps receiving events while that object tears down, and by then
        the filter's own Python half may already be gone.
        """
        self.maximize_btn.set_kind(
            "restore" if self._window.isMaximized() else "maximize"
        )

    # ------------------------------------------------------------------
    # Dragging
    # ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # Only reached for parts of the bar no child claimed -- the menus and
        # the caption buttons handle their own presses.
        if event.button() == Qt.LeftButton:
            handle = self._window.windowHandle()
            if handle is not None:
                handle.startSystemMove()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.LeftButton:
            self.toggle_maximized()
            return
        super().mouseDoubleClickEvent(event)


class _Grip(QWidget):
    """One edge or corner of the window, as something the mouse can grab."""

    def __init__(self, window: QWidget, edges: Qt.Edges, cursor: Qt.CursorShape):
        super().__init__(window)
        self._window = window
        self._edges = edges
        self.setCursor(cursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.LeftButton:
            handle = self._window.windowHandle()
            if handle is not None:
                handle.startSystemResize(self._edges)
                return
        super().mousePressEvent(event)


class ResizeGrips(QObject):
    """Eight grips pinned to the window's border.

    The window calls reposition() from its own resizeEvent/changeEvent -- see
    TitleBar.sync_window_state for why this isn't an event filter.

    Hidden while maximized: there's nothing to drag then, and a live grip
    along a screen edge would fight the taskbar.
    """

    def __init__(self, window: QWidget):
        super().__init__(window)
        self._window = window
        self._grips: list[tuple[_Grip, str]] = []
        for edges, cursor, where in (
            (Qt.LeftEdge, Qt.SizeHorCursor, "left"),
            (Qt.RightEdge, Qt.SizeHorCursor, "right"),
            (Qt.TopEdge, Qt.SizeVerCursor, "top"),
            (Qt.BottomEdge, Qt.SizeVerCursor, "bottom"),
            (Qt.LeftEdge | Qt.TopEdge, Qt.SizeFDiagCursor, "topleft"),
            (Qt.RightEdge | Qt.TopEdge, Qt.SizeBDiagCursor, "topright"),
            (Qt.LeftEdge | Qt.BottomEdge, Qt.SizeBDiagCursor, "bottomleft"),
            (Qt.RightEdge | Qt.BottomEdge, Qt.SizeFDiagCursor, "bottomright"),
        ):
            self._grips.append((_Grip(window, edges, cursor), where))
        self.reposition()

    def reposition(self) -> None:
        w, h, g = self._window.width(), self._window.height(), GRIP_PX
        maximized = self._window.isMaximized() or self._window.isFullScreen()
        geometry = {
            "left": QRect(0, g, g, h - 2 * g),
            "right": QRect(w - g, g, g, h - 2 * g),
            "top": QRect(g, 0, w - 2 * g, g),
            "bottom": QRect(g, h - g, w - 2 * g, g),
            "topleft": QRect(0, 0, g, g),
            "topright": QRect(w - g, 0, g, g),
            "bottomleft": QRect(0, h - g, g, g),
            "bottomright": QRect(w - g, h - g, g, g),
        }
        for grip, where in self._grips:
            grip.setGeometry(geometry[where])
            grip.setVisible(not maximized)
            grip.raise_()


# ----------------------------------------------------------------------
# Snap layouts (Windows 11)
# ----------------------------------------------------------------------


def _signed_word(value: int) -> int:
    """One 16-bit half of an lParam, as a signed number.

    Screen coordinates are packed unsigned, and a monitor left of the primary
    one has negative x -- read raw, a click there lands tens of thousands of
    pixels to the right instead.
    """
    value &= 0xFFFF
    return value - 0x10000 if value >= 0x8000 else value


def caption_message(title_bar: TitleBar, message: int, wparam: int, lparam: int):
    """The four messages that make the maximize button a real caption button.

    Returns (handled, result) for Qt's nativeEvent, or None to let Qt carry
    on. Claiming HTMAXBUTTON is what opens the snap layouts flyout on hover;
    everything after that exists because the claim also takes the button's
    mouse input away from Qt, so hover, press and click have to be handed
    back to the widget by hand.
    """
    if message == WM_NCHITTEST:
        over = title_bar.maximize_hit(
            QPoint(_signed_word(lparam), _signed_word(lparam >> 16))
        )
        title_bar.maximize_btn.set_native_state(hover=over)
        return (True, HTMAXBUTTON) if over else None

    if message == WM_NCMOUSELEAVE:
        title_bar.maximize_btn.set_native_state(hover=False, down=False)
        return None

    if wparam != HTMAXBUTTON:
        return None

    if message == WM_NCMOUSEMOVE:
        title_bar.maximize_btn.set_native_state(hover=True)
        return None
    if message == WM_NCLBUTTONDOWN:
        # Swallowed: left to Windows this would start a caption drag.
        title_bar.maximize_btn.set_native_state(down=True)
        return True, 0
    if message == WM_NCLBUTTONUP:
        title_bar.maximize_btn.set_native_state(hover=False, down=False)
        title_bar.toggle_maximized()
        return True, 0
    return None


def handle_native_event(title_bar: TitleBar, event_type, message):
    """Unpack a Windows MSG for caption_message(). None on any other platform."""
    if sys.platform != "win32" or bytes(event_type) != b"windows_generic_MSG":
        return None
    try:
        import ctypes.wintypes

        msg = ctypes.wintypes.MSG.from_address(int(message))
    except (ImportError, TypeError, ValueError):
        return None
    return caption_message(title_bar, msg.message, int(msg.wParam), int(msg.lParam))


def enable_native_snap(window: QWidget) -> None:
    """Make the window admit to being maximizable and sizable.

    Windows decides what snapping a window gets from its style bits, not from
    how it's dragged, and a frameless window is left holding neither of the
    two that matter:

    * WS_MAXIMIZEBOX is what the snap layouts flyout looks for when the mouse
      rests on the maximize button.
    * WS_THICKFRAME is what marks the window sizable, and every drag gesture
      is gated on it -- dragging to the top edge to maximize, to a side to
      half-tile, and the Win+Arrow shortcuts alike. Without it the drag just
      ends wherever the mouse was let go, with no zone preview on the way.

    Neither bit draws anything here. The frame they would normally imply comes
    from the non-client area, which a frameless window doesn't have -- its
    client rect already covers the whole window, and stays that way once these
    are set, maximized included.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = int(window.winId())
        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
        wanted = style | WS_MAXIMIZEBOX | WS_THICKFRAME
        if wanted != style:
            user32.SetWindowLongW(hwnd, GWL_STYLE, wanted)
            # A style change touching the frame isn't read back until the
            # window is asked to recompute one; without this the bits are set
            # but nothing acts on them until the next resize.
            user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
                | SWP_FRAMECHANGED,
            )
    except (AttributeError, OSError, ValueError):
        pass


def round_corners(window: QWidget) -> None:
    """Ask Windows 11 for its rounded corners on a frameless window.

    Cosmetic and best-effort: older Windows (and every other platform) simply
    doesn't have the attribute, and the window stays square.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        DWMWCP_ROUND = 2
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            int(window.winId()),
            DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(ctypes.c_int(DWMWCP_ROUND)),
            ctypes.sizeof(ctypes.c_int),
        )
    except (AttributeError, OSError, ValueError):
        pass


def install(window: QWidget) -> TitleBar:
    """Make `window` frameless and return the title bar to put at its top."""
    window.setWindowFlag(Qt.FramelessWindowHint, True)
    title_bar = TitleBar(window)
    # Parked on the title bar rather than dropped: a QObject that only the C++
    # parent holds can have its Python half collected, and the next event
    # delivered to its filter then arrives at an object with no attributes
    # left. Keeping one Python reference alive is the whole fix.
    title_bar.resize_grips = ResizeGrips(window)
    return title_bar
