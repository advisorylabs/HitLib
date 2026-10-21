from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QEventLoop
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from . import theme
from .main_window import MainWindow
from .splash import SplashScreen

# Identifies this app to the Windows shell. Reverse-DNS-ish and stable:
# changing it makes Windows treat the app as a brand new one, dropping any
# existing taskbar pin.
_APP_USER_MODEL_ID = "AdvisoryLabs.HitLib.PatternStudio"


def _icon_path() -> Path:
    # theme.resource_dir() handles the frozen-vs-source split: PyInstaller
    # extracts `datas` under sys._MEIPASS, where __file__ does not sit beside
    # a real resources/ directory.
    return theme.resource_dir() / "hitliblogo.ico"


def _claim_taskbar_identity() -> None:
    """Make the taskbar button use this app's own icon rather than its host's.

    Windows groups taskbar buttons (and picks their icon) by the process's
    AppUserModelID, which defaults to the executable that started it. Run from
    source that executable is python.exe, so the taskbar shows the Python icon
    no matter what setWindowIcon() says. Claiming an explicit ID detaches this
    from the host and lets the window icon through. Must run before the first
    window is created.
    """
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        # Cosmetic only, worst case the taskbar keeps the host's icon.
        pass


def main() -> None:
    _claim_taskbar_identity()
    app = QApplication(sys.argv)
    # Before any window exists, so nothing ever paints in the default style
    # and then restyles itself a frame later.
    theme.apply_theme(app)
    icon = QIcon(str(_icon_path()))
    # Not in the macOS bundle: there the application icon *is* the Dock tile,
    # and this .ico is full-bleed Windows art that would replace the .icns -
    # inset to Apple's icon grid - the moment the app finished launching. Run
    # from source on a Mac there is no bundle icon, so it still applies.
    if not (sys.platform == "darwin" and getattr(sys, "frozen", False)):
        app.setWindowIcon(icon)

    if "--no-splash" in sys.argv:
        window = MainWindow()
        window.setWindowIcon(icon)
        window.show()
        sys.exit(app.exec())

    splash = SplashScreen()
    splash.start()
    # The window waits for the fade-in to land. MainWindow's constructor
    # blocks the event loop, so built any earlier it ran behind a splash still
    # at zero opacity - an empty screen for the whole build - and the fade-in
    # only began after it, pushing the whole animation out behind startup.
    # Built here it lands in the hold, with the splash fully in view; the ring
    # just stands still while it runs. A skip that cuts the fade-in short gets
    # here through finished instead.
    fade_in = QEventLoop()
    splash.faded_in.connect(fade_in.quit)
    splash.finished.connect(fade_in.quit)
    fade_in.exec()

    # Out here rather than in a slot, so a window that fails to build still
    # raises out of main() instead of into the event loop, which would print
    # it and idle on with nothing left to show.
    window = MainWindow()
    # Also set per-window, not just app-wide: the taskbar reads the window's
    # own icon first, and only falls back to the application's.
    window.setWindowIcon(icon)
    if splash.isVisible():
        splash.finished.connect(window.show)
    else:
        window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
