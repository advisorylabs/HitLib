"""Theme integrity checks.

The stylesheet references icon files by path at runtime, so a renamed or
unbundled SVG doesn't raise. Qt just draws nothing and the control silently
loses its arrow/checkmark. These tests fail loudly instead.
"""

import re
import struct

from PySide6.QtGui import QImage, QImageReader, QPixmap

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


def _art_aspect(image: QImage) -> float:
    """Width/height of everything in `image` that isn't transparent."""
    image = image.convertToFormat(QImage.Format_ARGB32)
    stride = image.bytesPerLine()
    data = bytes(image.constBits())
    left, top, right, bottom = image.width(), image.height(), -1, -1
    for y in range(image.height()):
        alpha = data[y * stride : y * stride + image.width() * 4][3::4]
        if max(alpha) <= 8:
            continue
        top = min(top, y)
        bottom = max(bottom, y)
        left = min(left, next(x for x, a in enumerate(alpha) if a > 8))
        right = max(right, max(x for x, a in enumerate(alpha) if a > 8))
    return (right - left + 1) / (bottom - top + 1)


def test_taskbar_icon_is_not_squished(qapp):
    """The .ico frames are square and the logo art isn't, so an icon built by
    scaling the art to fill a frame comes out visibly narrow. Rebuild it with
    tools/make_icon.py, which fits the art instead of stretching it."""
    ico = theme.resource_dir() / "hitliblogo.ico"
    reader = QImageReader(str(ico))
    reader.jumpToImage(reader.imageCount() - 1)  # the 256px frame
    icon_aspect = _art_aspect(reader.read())
    source_aspect = _art_aspect(theme.logo_pixmap().toImage())
    assert abs(icon_aspect - source_aspect) < 0.05 * source_aspect, (
        f"icon art is {icon_aspect:.3f} wide-to-tall, art is {source_aspect:.3f}"
    )


#: The 8-byte PNG signature, spelled in hex so the non-printing bytes it
#: starts and ends with stay readable.
_PNG_MAGIC = bytes.fromhex("89504e470d0a1a0a")


def test_bundle_icon_has_the_frames_macos_asks_for(qapp):
    """The .app's icon comes from the .icns, and the Dock reads the 1024px
    frame -- four times past anything an .ico can hold, which is why there are
    two icons rather than one. Rebuild it with tools/make_icns.py.

    Parsed by hand rather than through QImageReader: Qt has no .icns plugin on
    Windows, so a test that loaded it would pass by skipping everywhere the
    icon is actually built.
    """
    blob = (theme.resource_dir() / "hitliblogo.icns").read_bytes()
    assert blob[:4] == b"icns"
    assert struct.unpack(">I", blob[4:8])[0] == len(blob), "length header disagrees"

    types, offset = [], 8
    while offset < len(blob):
        ostype = blob[offset : offset + 4]
        size = struct.unpack(">I", blob[offset + 4 : offset + 8])[0]
        assert size >= 8, f"{ostype!r} chunk claims {size} bytes"
        assert blob[offset + 8 : offset + 16] == _PNG_MAGIC, (
            f"{ostype!r} is not a PNG frame"
        )
        types.append(ostype)
        offset += size
    assert offset == len(blob), "chunk lengths do not tile the file"
    # ic10 is the 512@2x Dock icon; icp4 the 16px one Finder lists it by.
    assert b"ic10" in types and b"icp4" in types


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
