from hitlib_sim.colors import gen_gradient, gen_rainbow, lerp_color, trunc_div, wheel


def test_trunc_div_truncates_toward_zero():
    assert trunc_div(7, 2) == 3
    assert trunc_div(-7, 2) == -3
    assert trunc_div(7, -2) == -3
    assert trunc_div(-7, -2) == 3


def test_wheel_known_points():
    # The `pos = 255 - pos` reversal in wheel() shifts where each hue lands,
    # so only 0/255 (both pure red) are "obvious". The rest are pinned to
    # directly-computed values rather than assumed R/G/B boundaries.
    assert wheel(0) == 0xFF0000
    assert wheel(255) == 0xFF0000
    assert wheel(85) == 0x00FF00
    assert wheel(128) == 0x007E81
    assert wheel(170) == 0x0000FF


def test_wheel_has_no_seam():
    # One step around the wheel never moves a channel by more than one 3-unit
    # step. The last third once blended blue to red instead of red to green,
    # which skipped yellow and jumped straight from blue to green.
    for pos in range(256):
        a, b = wheel(pos), wheel((pos + 1) % 256)
        for shift in (16, 8, 0):
            assert abs(((a >> shift) & 0xFF) - ((b >> shift) & 0xFF)) <= 3, pos


def test_gen_rainbow_starts_red():
    buf = gen_rainbow(12)
    assert len(buf) == 12
    assert buf[0] == 0xFF0000


def test_gen_gradient_endpoints_and_midpoint():
    buf = gen_gradient(0x000000, 0xFFFFFF, 3)
    assert buf[0] == 0x000000
    assert buf[1] == 0x7F7F7F
    assert buf[2] == 0xFFFFFF


def test_lerp_color_halfway():
    assert lerp_color(0x000000, 0xFF0000, 128) == 0x800000
    assert lerp_color(0x000000, 0xFF0000, 0) == 0x000000
    assert lerp_color(0x000000, 0xFF0000, 255) == 0xFF0000
