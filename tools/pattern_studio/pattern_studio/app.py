from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .main_window import MainWindow

_ICON_REL_PATH = Path("pattern_studio") / "resources" / "hitliblogo.ico"


def _icon_path() -> Path:
    # A normal install/run resolves the icon relative to this file. A frozen
    # PyInstaller build extracts its `datas` under sys._MEIPASS instead, where
    # __file__ no longer sits next to a real resources/ directory.
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / _ICON_REL_PATH


def main() -> None:
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(str(_icon_path())))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
