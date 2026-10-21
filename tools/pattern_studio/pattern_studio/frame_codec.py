"""Run-length coding for a drawn-frame layer's pixels.

One byte per run: the top three bits are the run length minus one (so a run is
1-8 pixels) and the bottom five a palette index (0-31). Index 0 is transparent
whatever colour it names, which is what lets a drawn shape sit over the layers
below it rather than punching a hole in them.

Three consumers share this one body - the save file, the C++ export, and the
preview's decoder - because a frame that decodes differently in any one of
them is a frame the user did not draw.

Identical frames share an offset in the index, so a two-frame blink that
returns to its first frame costs two encodings and three index entries.
"""

from __future__ import annotations

#: Palette indices are five bits, and index 0 is reserved for transparent.
MAX_FRAME_COLORS = 32
#: Run lengths are three bits, stored minus one.
MAX_RUN = 8
#: Most frames one drawing can hold: the firmware counts them in a uint8_t
#: (LayerFrames::count), so a 256th would not compile in the exported header.
MAX_DRAWN_FRAMES = 255


def encode_frame(row: list[int], width: int) -> bytes:
    """One row of palette indices as RLE bytes.

    A run longer than MAX_RUN is split across several bytes rather than
    clamped, so a 20-pixel solid row survives the round trip.
    """
    out = bytearray()
    i = 0
    n = min(len(row), width)
    while i < n:
        idx = row[i] & (MAX_FRAME_COLORS - 1)
        run = 1
        while i + run < n and (row[i + run] & (MAX_FRAME_COLORS - 1)) == idx and run < MAX_RUN:
            run += 1
        out.append(((run - 1) << 5) | idx)
        i += run
    return bytes(out)


def decode_frame(data: bytes, at: int, width: int) -> list[int]:
    """`width` palette indices, starting at byte offset `at`.

    A run is clamped to whatever width is left, so a hand-edited table cannot
    overrun - the same guard the firmware's decodeFrameNL() applies.
    """
    row: list[int] = []
    while len(row) < width and at < len(data):
        byte = data[at]
        at += 1
        run = (byte >> 5) + 1
        idx = byte & (MAX_FRAME_COLORS - 1)
        run = min(run, width - len(row))
        row.extend([idx] * run)
    # A truncated table pads with transparent rather than raising: a partly
    # written file should still open.
    row.extend([0] * (width - len(row)))
    return row


def encode_frames(rows: list[list[int]], width: int) -> tuple[bytes, tuple[int, ...]]:
    """Every frame packed end to end, plus each frame's byte offset.

    Frames that encode identically share one copy, which is what makes a blink
    or a hold cost almost nothing.
    """
    data = bytearray()
    index: list[int] = []
    seen: dict[bytes, int] = {}
    for row in rows:
        blob = encode_frame(row, width)
        at = seen.get(blob)
        if at is None:
            at = len(data)
            seen[blob] = at
            data.extend(blob)
        index.append(at)
    return bytes(data), tuple(index)


def decode_frames(data: bytes, index: tuple[int, ...] | list[int], width: int) -> list[list[int]]:
    return [decode_frame(data, at, width) for at in index]
