"""Startup splash: the HitLib logo inside a rotating brand-gradient ring.

Purely a brand moment, not a progress indicator -- Pattern Studio starts fast
enough that there's nothing to report. It's deliberately kept honest about
that: no fake progress bar, no "Loading modules..." text. The main window is
built while the splash is up, so the wait it adds is the animation itself.

Click (or press a key) to skip, and pass --no-splash to bypass it entirely.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QBrush, QColor, QConicalGradient, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import theme


#: The ring's color stops. theme.BRAND_SWEEP is the wordmark gradient run out
#: and back, so the wrap-around point is pink meeting pink -- see the token
#: for why the plain left-to-right order won't do here.
_RING_STOPS = theme.BRAND_SWEEP


class SplashScreen(QWidget):
    """Frameless, translucent splash that fades in, spins, and fades out.

    Emits `finished` once it has faded out and closed -- connect that to
    showing the main window.
    """

    #: Fired after the fade-out completes and the widget has closed.
    finished = Signal()

    # Geometry, in logical pixels.
    SIZE = 340
    BACKDROP_RADIUS = 150
    RING_RADIUS = 132
    RING_WIDTH = 8

    #: Bloom passes drawn under the crisp ring, outermost first:
    #: (width_multiple, alpha). Additive, so where the halos overlap the ring
    #: they sum into the brighter core -- the same trick the LED preview uses,
    #: at a scale where it reads as the logo being lit from its own ring.
    RING_BLOOM = ((3.4, 26), (2.0, 44))
    LOGO_BOX = 200

    # Timings. Instance copies are made in __init__ so a test can shrink them.
    FADE_IN_MS = 450
    HOLD_MS = 950
    FADE_OUT_MS = 400

    #: ~60fps, and how far the gradient sweeps per frame.
    FRAME_MS = 16
    DEGREES_PER_FRAME = 2.6

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fade_in_ms = self.FADE_IN_MS
        self.hold_ms = self.HOLD_MS
        self.fade_out_ms = self.FADE_OUT_MS

        # Qt.SplashScreen keeps it out of the taskbar, so the app doesn't
        # briefly show two entries. FramelessWindowHint plus a translucent
        # background is what lets the backdrop be a circle rather than a
        # square with rounded art painted on it.
        self.setWindowFlags(
            Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to skip")

        self._angle = 0.0
        self._logo_cache: tuple[float, object] | None = None
        self._closing = False
        # Animations must outlive the call that starts them; a local would be
        # garbage collected mid-fade and the splash would jump to its end
        # value instantly.
        self._anim: QPropertyAnimation | None = None

        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(self.FRAME_MS)
        self._spin_timer.timeout.connect(self._advance)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Center on screen, fade in, spin, then fade out and emit finished."""
        self._center_on_screen()
        self.setWindowOpacity(0.0)
        self.show()
        self._spin_timer.start()

        self._anim = self._fade_to(1.0, self.fade_in_ms, QEasingCurve.OutCubic)
        self._anim.finished.connect(self._begin_hold)

    def _begin_hold(self) -> None:
        if self._closing:
            return
        QTimer.singleShot(self.hold_ms, self._fade_out)

    def _fade_out(self) -> None:
        # Guarded: a click during the hold can race the hold timer, and
        # starting a second fade would re-show the splash mid-close.
        if self._closing:
            return
        self._closing = True
        self._anim = self._fade_to(0.0, self.fade_out_ms, QEasingCurve.InCubic)
        self._anim.finished.connect(self._finish)

    def _finish(self) -> None:
        self._spin_timer.stop()
        self.close()
        self.finished.emit()

    def skip(self) -> None:
        """Cut straight to the fade-out, wherever the animation is."""
        self._fade_out()

    def _fade_to(self, end: float, duration: int, curve) -> QPropertyAnimation:
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(duration)
        anim.setStartValue(self.windowOpacity())
        anim.setEndValue(end)
        anim.setEasingCurve(curve)
        anim.start()
        return anim

    def _center_on_screen(self) -> None:
        screen = QGuiApplication.screenAt(QPoint(0, 0)) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        center = screen.availableGeometry().center()
        self.move(center.x() - self.width() // 2, center.y() - self.height() // 2)

    def _advance(self) -> None:
        self._angle = (self._angle + self.DEGREES_PER_FRAME) % 360.0
        self.update()

    # ------------------------------------------------------------------
    # Input -- any interaction skips
    # ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.skip()

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.skip()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def _logo(self, dpr: float):
        """Logo scaled for this device pixel ratio, cached.

        Rescaling a ~1400px source every frame would dominate the frame
        budget, and the result only changes if the window moves to a screen
        with a different DPI.
        """
        if self._logo_cache is not None and self._logo_cache[0] == dpr:
            return self._logo_cache[1]
        source = theme.logo_pixmap()
        if source.isNull():
            self._logo_cache = (dpr, source)
            return source
        box = int(self.LOGO_BOX * dpr)
        scaled = source.scaled(box, box, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        scaled.setDevicePixelRatio(dpr)
        self._logo_cache = (dpr, scaled)
        return scaled

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center = QRectF(self.rect()).center()

        # Backdrop disc: gives the logo's transparent margins something to sit
        # on, so the splash reads the same over a light or dark desktop.
        painter.setPen(QPen(QColor(theme.BORDER), 1))
        painter.setBrush(QBrush(QColor(theme.BG_BASE)))
        painter.drawEllipse(center, self.BACKDROP_RADIUS, self.BACKDROP_RADIUS)

        self._paint_ring(painter, center)

        logo = self._logo(self.devicePixelRatioF())
        if not logo.isNull():
            size = logo.deviceIndependentSize()
            painter.drawPixmap(
                QRectF(
                    center.x() - size.width() / 2,
                    center.y() - size.height() / 2,
                    size.width(),
                    size.height(),
                ),
                logo,
                QRectF(logo.rect()),
            )

    def _ring_gradient(self, center, alpha: int = 255) -> QConicalGradient:
        # A conical gradient whose angle advances each frame: the ring itself
        # is a static full circle, and it's the colors that travel around it.
        # Negative angle so the sweep runs clockwise.
        gradient = QConicalGradient(center, -self._angle)
        stops = _RING_STOPS
        for i, color in enumerate(stops):
            tint = QColor(color)
            tint.setAlpha(alpha)
            gradient.setColorAt(i / (len(stops) - 1), tint)
        return gradient

    def _paint_ring(self, painter: QPainter, center) -> None:
        painter.setBrush(Qt.NoBrush)

        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode_Plus)
        for width_multiple, alpha in self.RING_BLOOM:
            pen = QPen(QBrush(self._ring_gradient(center, alpha)), self.RING_WIDTH * width_multiple)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.drawEllipse(center, self.RING_RADIUS, self.RING_RADIUS)
        painter.restore()

        pen = QPen(QBrush(self._ring_gradient(center)), self.RING_WIDTH)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawEllipse(center, self.RING_RADIUS, self.RING_RADIUS)
