"""Color helpers ported from src/led_strand.cpp (namespace-local functions + genGradient/genRainbow).

Integer math is kept C-like (truncating division, explicit byte packing)
rather than "pythonic": this module has to reproduce the firmware's pixel
output bit-for-bit.
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
    return pack_rgb(pos * 3, 255 - pos * 3, 0)


def gradient_pixel(c1: int, c2: int, i: int, length: int, seamless: bool) -> int:
    """Port of LedStrand::gradientPixel() - one color off a two-color ramp.

    Extracted from gen_gradient()'s loop body so a custom-animation layer can
    sample a gradient without building the whole buffer, exactly as the
    firmware does. gen_gradient() is written in terms of this, so the two
    cannot disagree.
    """
    r1, g1, b1 = unpack_rgb(c1)
    r2, g2, b2 = unpack_rgb(c2)
    if length <= 1:
        t = 0
    elif seamless:
        x = trunc_div(i * 2 * 255, length)
        t = 255 - abs(x - 255)
    else:
        t = trunc_div(i * 255, length - 1)
    r = r1 + trunc_div((r2 - r1) * t, 255)
    g = g1 + trunc_div((g2 - g1) * t, 255)
    b = b1 + trunc_div((b2 - b1) * t, 255)
    return pack_rgb(r, g, b)


def gen_gradient(c1: int, c2: int, length: int, seamless: bool = False) -> list[int]:
    """Port of LedStrand::genGradient() (led_strand.cpp:1382-1404). Linear RGB lerp, no gamma.

    `seamless` traces a triangle wave (c1 at i=0, c2 at the midpoint, back
    toward c1 by i=length-1) instead of an open ramp, so a circular shift of
    the result loops with no hard cut at the wrap.
    """
    return [gradient_pixel(c1, c2, i, length, seamless) for i in range(length)]


def scale_color_at(stops, step: bool, bg_color: int, level: int) -> int:
    """Port of LedStrand::scaleColorAt() - the color a stop scale gives for a
    0-255 level. Stops are already sorted and already on that axis, so this is
    a bracket and at most one lerp.

    Extracted from the gauge lookup so a custom-animation layer can carry a
    scale of its own; Strand._gauge_color_at() is a thin wrapper over it.
    `step` holds each stop until the next one is reached, which is the
    honest reading when the stops are thresholds something crosses rather
    than points on a ramp. A level exactly on a stop has reached it.
    """
    if not stops:
        return bg_color
    # Past either end the scale holds, so it never has to cover a range the
    # gauge does not care about.
    if level <= stops[0][0]:
        return stops[0][1]
    if level >= stops[-1][0]:
        return stops[-1][1]

    if step:
        # The last stop at or below the level; stops[0] is below, see above.
        j = len(stops) - 1
        while stops[j][0] > level:
            j -= 1
        return stops[j][1]

    i = 1
    while i < len(stops) and stops[i][0] < level:
        i += 1
    lo_at, lo_color = stops[i - 1]
    hi_at, hi_color = stops[i]

    span = hi_at - lo_at
    if span == 0:
        return hi_color
    return lerp_color(lo_color, hi_color, trunc_div((level - lo_at) * 255, span))


def sample_track(samples, frame_count: int, frame_ms: int, position_ms: int,
                 sensitivity: int, loop: bool) -> int:
    """Port of LedStrand::sampleTrackNL() - read an envelope at an arbitrary
    millisecond offset, interpolating between the two frames it falls between,
    so a coarse frame rate still renders as a smooth fill at the strand's
    faster refresh rate.

    Takes the track's parts rather than the strand's fields so a
    custom-animation driver can play a track of its own;
    Strand._sample_music() is a thin wrapper over it.
    """
    if not samples or frame_count == 0 or frame_ms <= 0:
        return 0

    total_ms = frame_count * frame_ms
    if position_ms >= total_ms:
        if not loop:
            return 0  # song over, let the meter drain
        position_ms %= total_ms
    if position_ms < 0:
        position_ms = 0

    idx, frac = divmod(position_ms, frame_ms)
    a = samples[idx]
    if idx + 1 < frame_count:
        b = samples[idx + 1]
    else:
        b = samples[0] if loop else a

    raw = a + trunc_div((b - a) * frac, frame_ms)
    return max(0, min(trunc_div(raw * sensitivity, 100), 255))


def gen_rainbow(length: int) -> list[int]:
    """Port of LedStrand::genRainbow() (led_strand.cpp:782-789)."""
    out = []
    for i in range(length):
        hue = trunc_div(i * 256, max(length, 1))
        out.append(wheel(hue))
    return out
