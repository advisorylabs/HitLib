"""Theme integrity checks.

The stylesheet references icon files by path at runtime, so a renamed or
unbundled SVG doesn't raise -- Qt just draws nothing and the control silently
loses its arrow/checkmark. These tests fail loudly instead.
"""

import re

from PySide6.QtGui import QPixmap

from pattern_studio import theme

# Every icon stem the app loads through theme.icon().
_ICON_NAMES = [
    "play",
    "pause",
    "reset",
    "plus",
    "minus",
    "arrow-up",
    "arrow-down",
    "spin-up",
    "spin-down",
    "chevron-down",
    "check",
]


def test_every_icon_file_exists():
    missing = [n for n in _ICON_NAMES if not (theme.resource_dir() / "icons" / f"{n}.svg").is_file()]
    assert not missing, f"missing icon files: {missing}"


def test_icons_actually_render(qapp):
    """A present-but-unparseable SVG is as bad as a missing one, and only
    shows up as a blank control at runtime."""
    blank = [n for n in _ICON_NAMES if theme.icon(n).pixmap(16, 16).isNull()]
    assert not blank, f"icons failed to render: {blank}"


def test_logo_loads(qapp):
    assert not theme.logo_pixmap().isNull()


def test_stylesheet_has_no_unsubstituted_placeholders():
    qss = theme.stylesheet()
    assert "$" not in qss, "stylesheet still contains a $placeholder"
    assert qss.strip()


def test_stylesheet_urls_all_resolve():
    for url in re.findall(r'url\("([^"]+)"\)', theme.stylesheet()):
        assert not QPixmap(url).isNull(), f"stylesheet url does not load: {url}"


def test_apply_theme_installs_sheet_and_palette(qapp):
    previous = qapp.styleSheet()
    try:
        theme.apply_theme(qapp)
        assert "QPushButton" in qapp.styleSheet()
        assert qapp.palette().window().color().name().lower() == theme.BG_BASE.lower()
        assert qapp.font().families()[0] == theme.FONT_STACK[0]
    finally:
        qapp.setStyleSheet(previous)
