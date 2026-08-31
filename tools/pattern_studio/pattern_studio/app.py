from __future__ import annotations

import sys
from pathlib import Path

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
    app.setWindowIcon(icon)

    if "--no-splash" in sys.argv:
        window = MainWindow()
        window.setWindowIcon(icon)
        window.show()
        sys.exit(app.exec())

    splash = SplashScreen()
    splash.start()
    # Paint the splash before building the window: MainWindow's constructor
    # blocks the event loop, and without this the splash would first appear
    # already partway through its fade.
    app.processEvents()

    # Built while the splash is up rather than after it, so the animation
    # overlaps startup instead of being tacked on in front of it.
    window = MainWindow()
    # Also set per-window, not just app-wide: the taskbar reads the window's
    # own icon first, and only falls back to the application's.
    window.setWindowIcon(icon)
    splash.finished.connect(window.show)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
