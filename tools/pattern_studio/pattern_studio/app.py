from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .main_window import MainWindow

_ICON_REL_PATH = Path("pattern_studio") / "resources" / "hitliblogo.ico"

# Identifies this app to the Windows shell. Reverse-DNS-ish and stable:
# changing it makes Windows treat the app as a brand new one, dropping any
# existing taskbar pin.
_APP_USER_MODEL_ID = "AdvisoryLabs.HitLib.PatternStudio"


def _icon_path() -> Path:
    # A normal install/run resolves the icon relative to this file. A frozen
    # PyInstaller build extracts its `datas` under sys._MEIPASS instead, where
    # __file__ no longer sits next to a real resources/ directory.
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / _ICON_REL_PATH


def _claim_taskbar_identity() -> None:
    """Make the taskbar button use this app's own icon rather than its host's.

    Windows groups taskbar buttons -- and picks their icon -- by the process's
    AppUserModelID, which defaults to the executable that started it. Run from
    source that executable is python.exe, so the taskbar shows the Python icon
    no matter what setWindowIcon() says. Claiming an explicit ID detaches us
    from the host and lets the window icon through. Must run before the first
    window is created.
    """
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        # Cosmetic only -- worst case the taskbar keeps the host's icon.
        pass


def main() -> None:
    _claim_taskbar_identity()
    app = QApplication(sys.argv)
    icon = QIcon(str(_icon_path()))
    app.setWindowIcon(icon)
    window = MainWindow()
    # Also set per-window, not just app-wide: the taskbar reads the window's
    # own icon first, and only falls back to the application's.
    window.setWindowIcon(icon)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
