"""Color helpers ported from src/led_strand.cpp (namespace-local functions + genGradient/genRainbow).

Integer math is kept intentionally C-like (truncating division, explicit byte
packing) rather than "pythonic", since the whole point of this module is to
reproduce the firmware's pixel output bit-for-bit.
"""

from __future__ import annotations


def trunc_div(a: int, b: int) -> int:
    """C-style integer division: truncates toward zero (Python's // floors)."""
    q, r = divmod(a, b)
    if r != 0 and (a < 0) != (b < 0):
        q += 1
    return q


def unpack_rgb(color: int) -> tuple[int, int, int]:
    return (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF


def pack_rgb(r: int, g: int, b: int) -> int:
    return ((r & 0xFF) << 16) | ((g & 0xFF) << 8) | (b & 0xFF)


def lerp_color(bg: int, fg: int, level: int) -> int:
    """Port of the anonymous-namespace lerpColor() (led_strand.cpp:17-24)."""
    br, bgc, bb = unpack_rgb(bg)
    fr, fgc, fb = unpack_rgb(fg)
    r = br + trunc_div((fr - br) * level, 255)
    g = bgc + trunc_div((fgc - bgc) * level, 255)
    b = bb + trunc_div((fb - bb) * level, 255)
    return pack_rgb(r, g, b)


def wheel(pos: int) -> int:
    """Port of wheel() (led_strand.cpp:27-36). NeoPixel-style hue wheel, S=V=255."""
    pos = (255 - (pos & 0xFF)) & 0xFF
    if pos < 85:
        return pack_rgb(255 - pos * 3, 0, pos * 3)
    if pos < 170:
        pos -= 85
        return pack_rgb(0, pos * 3, 255 - pos * 3)
    pos -= 170
    return pack_rgb(pos * 3, 0, 255 - pos * 3)


def gen_gradient(c1: int, c2: int, length: int) -> list[int]:
    """Port of LedStrand::genGradient() (led_strand.cpp:768-780). Linear RGB lerp, no gamma."""
    r1, g1, b1 = unpack_rgb(c1)
    r2, g2, b2 = unpack_rgb(c2)
    out = []
    for i in range(length):
        t = 0 if length <= 1 else trunc_div(i * 255, length - 1)
        r = r1 + trunc_div((r2 - r1) * t, 255)
        g = g1 + trunc_div((g2 - g1) * t, 255)
        b = b1 + trunc_div((b2 - b1) * t, 255)
        out.append(pack_rgb(r, g, b))
    return out


def gen_rainbow(length: int) -> list[int]:
    """Port of LedStrand::genRainbow() (led_strand.cpp:782-789)."""
    out = []
    for i in range(length):
        hue = trunc_div(i * 256, max(length, 1))
        out.append(wheel(hue))
    return out
