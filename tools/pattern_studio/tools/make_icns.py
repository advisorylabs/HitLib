"""Rebuild pattern_studio/resources/hitliblogo.icns from hitliblogo.png.

Run after the logo art changes, alongside make_icon.py:

    python tools/make_icns.py

macOS reads an app bundle's icon from an .icns, and Qt's .ico is no
substitute: the Dock, Finder and the app switcher all want frames well past
the 256px an .ico tops out at, and a bundle without one falls back to the
blank generic-application tile.

Cropping and framing are make_icon.py's, unchanged - the same aspect-preserving
fit into a transparent square, off the same source - so the two icons are the
same artwork at different resolutions. Only the container differs.

Runs anywhere: the frames are PNG and the wrapper is a length-prefixed chunk
table, so this needs no macOS-only `iconutil`.
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_icon import RESOURCES, SOURCE, as_png, frame, opaque_bounds  # noqa: E402

TARGET = RESOURCES / "hitliblogo.icns"

#: (OSType, pixel size), in the order and under the type codes Apple's own
#: iconutil emits for a full .iconset. Each pair of codes at one size is the
#: 1x and 2x pair for a Retina display: ic11 is 16x16@2x, ic12 is 32x32@2x,
#: and so on up to ic10, the 512x512@2x that fills the Dock at rest.
ENTRIES = (
    (b"icp4", 16),
    (b"ic11", 32),
    (b"icp5", 32),
    (b"ic12", 64),
    (b"ic07", 128),
    (b"ic13", 256),
    (b"ic08", 256),
    (b"ic14", 512),
    (b"ic09", 512),
    (b"ic10", 1024),
)


def build(chunks: list[tuple[bytes, bytes]]) -> bytes:
    """Wrap encoded frames in the 'icns' container.

    One flat table: an 8-byte file header, then each frame behind its own
    4-byte OSType and length. Both lengths count their own header, which is
    the one thing easy to get wrong here.
    """
    body = b"".join(
        ostype + struct.pack(">I", len(blob) + 8) + blob for ostype, blob in chunks
    )
    return b"icns" + struct.pack(">I", len(body) + 8) + body


def main() -> int:
    app = QApplication.instance() or QApplication([])  # noqa: F841 - QImage needs one
    source = QImage(str(SOURCE))
    if source.isNull():
        print(f"could not read {SOURCE}", file=sys.stderr)
        return 1

    left, top, right, bottom = opaque_bounds(source)
    art = source.copy(left, top, right - left + 1, bottom - top + 1)

    # Cached per size: the table names 256 and 512 twice each (once as a 1x
    # frame, once as the 2x of the size below), and those are the same image.
    rendered: dict[int, bytes] = {}
    chunks = []
    for ostype, size in ENTRIES:
        if size not in rendered:
            rendered[size] = as_png(frame(art, size))
        chunks.append((ostype, rendered[size]))

    TARGET.write_bytes(build(chunks))
    print(f"wrote {TARGET.name}: {len(ENTRIES)} frames, {TARGET.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
