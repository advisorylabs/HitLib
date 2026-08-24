"""Rebuild pattern_studio/resources/hitliblogo.ico from hitliblogo.png.

Run after the logo art changes:

    python tools/make_icon.py

The art is 1414x1067 -- wider than it is tall. Scaling that straight into the
square frames an .ico is made of squeezes it horizontally, which is exactly
what the taskbar was showing. So this crops the PNG to its opaque bounds
(the source has wide transparent side margins), fits that into each frame
*preserving aspect*, and centers it on transparency.

Frame encoding matches what the previous icon shipped, and what Windows
expects: 32-bit BMP/DIB for everything up to 128px, PNG for the 256px frame
(a DIB that large is a quarter-megabyte on its own).
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

RESOURCES = Path(__file__).resolve().parent.parent / "pattern_studio" / "resources"
SOURCE = RESOURCES / "hitliblogo.png"
TARGET = RESOURCES / "hitliblogo.ico"

#: Frame sizes, smallest first. The set Windows picks from for the taskbar,
#: the title bar, Explorer's various view modes and Alt-Tab.
SIZES = (16, 24, 32, 48, 64, 72, 96, 128, 256)
#: Sizes at or above this are stored as PNG rather than as a raw DIB.
PNG_FROM = 256


def opaque_bounds(image: QImage) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) of everything that isn't fully transparent.

    Row-at-a-time over the raw ARGB bytes: a per-pixel loop over 1.5M pixels
    in Python takes long enough to notice, and `max(row[3::4])` does the same
    work in C.
    """
    image = image.convertToFormat(QImage.Format_ARGB32)
    stride = image.bytesPerLine()
    data = bytes(image.constBits())
    left, top, right, bottom = image.width(), image.height(), -1, -1
    for y in range(image.height()):
        row = data[y * stride : y * stride + image.width() * 4]
        alpha = row[3::4]
        if max(alpha) <= 8:
            continue
        top = min(top, y)
        bottom = max(bottom, y)
        # Only the rows that carry something get the (slower) column scan.
        for x, a in enumerate(alpha):
            if a > 8:
                left = min(left, x)
                break
        for x in range(len(alpha) - 1, -1, -1):
            if alpha[x] > 8:
                right = max(right, x)
                break
    return left, top, right, bottom


def frame(art: QImage, size: int) -> QImage:
    """`art` fitted into a size x size frame, aspect intact, centered."""
    scaled = art.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    canvas = QImage(size, size, QImage.Format_ARGB32)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.drawImage(
        (size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled
    )
    painter.end()
    return canvas


def as_png(image: QImage) -> bytes:
    buffer = QBuffer(QByteArray())
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def as_dib(image: QImage) -> bytes:
    """A 32-bit icon DIB: BITMAPINFOHEADER, bottom-up BGRA, then an AND mask.

    The mask is all zeros -- every pixel is "not masked out", and the real
    transparency comes from the alpha channel. Windows still expects the mask
    to be there and to be padded to 4-byte rows.
    """
    image = image.convertToFormat(QImage.Format_ARGB32)
    width, height = image.width(), image.height()

    pixels = bytearray()
    for y in range(height - 1, -1, -1):  # DIBs are stored bottom-up
        for x in range(width):
            argb = image.pixel(x, y)
            a = (argb >> 24) & 0xFF
            r = (argb >> 16) & 0xFF
            g = (argb >> 8) & 0xFF
            b = argb & 0xFF
            pixels += bytes((b, g, r, a))

    mask_stride = ((width + 31) // 32) * 4
    mask = bytes(mask_stride * height)

    header = struct.pack(
        "<IiiHHIIiiII",
        40,  # biSize
        width,
        height * 2,  # biHeight covers the XOR image *and* the AND mask
        1,  # biPlanes
        32,  # biBitCount
        0,  # biCompression (BI_RGB)
        len(pixels) + len(mask),  # biSizeImage
        0,
        0,
        0,
        0,
    )
    return header + bytes(pixels) + mask


def build(frames: list[tuple[int, bytes]]) -> bytes:
    """Wrap encoded frames in an ICONDIR + ICONDIRENTRY table."""
    out = bytearray(struct.pack("<HHH", 0, 1, len(frames)))  # reserved, type=icon, count
    offset = 6 + 16 * len(frames)
    for size, blob in frames:
        out += struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,  # 256 is stored as 0 in a byte field
            size if size < 256 else 0,
            0,  # palette entries (none, it's 32-bit)
            0,  # reserved
            1,  # planes
            32,  # bits per pixel
            len(blob),
            offset,
        )
        offset += len(blob)
    for _, blob in frames:
        out += blob
    return bytes(out)


def main() -> int:
    app = QApplication.instance() or QApplication([])  # noqa: F841 -- QImage needs one
    source = QImage(str(SOURCE))
    if source.isNull():
        print(f"could not read {SOURCE}", file=sys.stderr)
        return 1

    left, top, right, bottom = opaque_bounds(source)
    art = source.copy(left, top, right - left + 1, bottom - top + 1)
    print(
        f"{SOURCE.name}: {source.width()}x{source.height()}, "
        f"art {art.width()}x{art.height()} (aspect {art.width() / art.height():.3f})"
    )

    frames = []
    for size in SIZES:
        image = frame(art, size)
        frames.append((size, as_png(image) if size >= PNG_FROM else as_dib(image)))

    TARGET.write_bytes(build(frames))
    print(f"wrote {TARGET.name}: {len(SIZES)} frames, {TARGET.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
