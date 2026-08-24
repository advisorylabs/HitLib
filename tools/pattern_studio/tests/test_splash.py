"""Splash screen behaviour.

The splash gates the main window. If its `finished` signal never fires, the
app starts up to nothing at all. These tests run its real timeline (with the
durations shrunk) rather than poking at internals.
"""

from PySide6.QtCore import QElapsedTimer, QEventLoop, QTimer
from PySide6.QtGui import QColor

from pattern_studio.splash import _RING_STOPS, SplashScreen


def _make(qapp, total_ms=90):
    """A splash whose whole timeline fits in a test."""
    splash = SplashScreen()
    splash.fade_in_ms = total_ms // 3
    splash.hold_ms = total_ms // 3
    splash.fade_out_ms = total_ms // 3
    return splash


def _run_until_finished(qapp, splash, timeout_ms=5000):
    """Spin the event loop until `finished` fires. Returns how many times it
    did. A skip racing the hold timer could otherwise fire it twice."""
    fired = []
    loop = QEventLoop()
    splash.finished.connect(lambda: fired.append(True))
    splash.finished.connect(loop.quit)
    QTimer.singleShot(timeout_ms, loop.quit)
    elapsed = QElapsedTimer()
    elapsed.start()
    loop.exec()
    # Keep pumping briefly so a duplicate emission would be caught.
    settle = QEventLoop()
    QTimer.singleShot(60, settle.quit)
    settle.exec()
    return len(fired), elapsed.elapsed()


def test_finishes_on_its_own(qapp):
    splash = _make(qapp)
    splash.start()
    count, _ = _run_until_finished(qapp, splash)
    assert count == 1
    assert not splash.isVisible()


def test_skip_finishes_early_and_only_once(qapp):
    # A long hold that the skip should cut through.
    splash = SplashScreen()
    splash.fade_in_ms = 20
    splash.hold_ms = 4000
    splash.fade_out_ms = 20
    splash.start()

    QTimer.singleShot(40, splash.skip)
    count, elapsed = _run_until_finished(qapp, splash)
    assert count == 1
    assert elapsed < 3000, "skip did not cut the hold short"


def test_paints_something(qapp):
    splash = _make(qapp)
    image = splash.grab().toImage()
    assert not image.isNull()
    # The ring and logo must actually land on the widget: a blank grab means a
    # missing logo resource or a painter that drew nothing.
    colors = {image.pixelColor(x, y).name() for x in range(0, image.width(), 7)
              for y in range(0, image.height(), 7)}
    assert len(colors) > 20, "splash appears to be a flat fill"


def test_spin_advances_and_wraps(qapp):
    splash = _make(qapp)
    start = splash._angle
    splash._advance()
    assert splash._angle != start

    splash._angle = 359.0
    splash._advance()
    assert 0.0 <= splash._angle < 360.0


def test_ring_gradient_is_seamless_and_valid(qapp):
    assert all(QColor(c).isValid() for c in _RING_STOPS)
    # First and last stop must match, or the conical gradient shows a hard
    # edge travelling around the ring.
    assert _RING_STOPS[0] == _RING_STOPS[-1]
