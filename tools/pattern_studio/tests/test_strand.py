from hitlib_sim.colors import gen_rainbow
from hitlib_sim.strand import BitScrollSegment, Strand


def test_rainbow_scrolls_by_shift_step():
    s = Strand(adi_port=1, length=4, refresh_ms=20)
    s.rainbow(speed=1)
    expected_buffer = gen_rainbow(4)
    assert s.buffer == expected_buffer

    s.tick()
    assert s.shift_step == 1
    assert s.pixels == [expected_buffer[(i + 1) % 4] for i in range(4)]


def test_flash_block_scrolls_one_pixel_per_tick():
    s = Strand(adi_port=1, length=4, refresh_ms=20)
    s.flash(color=0xFF0000, speed=2, bg_color=0)
    assert len(s.buffer) == 12  # length * (1 + min(speed, 32))

    s.tick()
    assert s.pixels == [0xFF0000, 0xFF0000, 0xFF0000, 0x000000]


def test_pulse_bounce_reflects_at_strip_ends():
    s = Strand(adi_port=1, length=5, refresh_ms=20)
    s.pulse(color=0xFF0000, run_length=1, speed=1, bounce=True)

    offsets = []
    for _ in range(8):
        s.tick()
        offsets.append(s.pulse_offset)

    # max_offset = length - run_length = 4; bounces 1,2,3,4,3,2,1,0
    assert offsets == [1, 2, 3, 4, 3, 2, 1, 0]
    assert s.pulse_dir == 1


def test_splice_mask_bin_distribution():
    s = Strand(adi_port=1, length=7, refresh_ms=20)
    s.splice_mask(sections=2)  # 3 bins: sizes 3,2,2 -- bin 1 (odd) shows animation
    assert s.splice_show_anim == [False, False, False, True, True, False, False]


def test_bitscroll_repeating_tiles_and_shifts():
    s = Strand(adi_port=1, length=7, refresh_ms=20)
    s.bitscroll(
        segments=[BitScrollSegment(color=0xFF0000, width=2)],
        speed=1,
        bg_color=0,
        spacing=1,
    )
    assert s.buffer[:3] == [0xFF0000, 0xFF0000, 0x000000]

    s.tick()
    assert s.pixels == [0xFF0000, 0x000000, 0xFF0000, 0xFF0000, 0x000000, 0xFF0000, 0xFF0000]


def test_twinkle_single_pixel_fade_cycle():
    s = Strand(adi_port=1, length=1, refresh_ms=20)
    s.twinkle(colors=[0xFF0000], density_pct=100, fade_step=128, bg_color=0)

    s.tick()  # spawns, fades in to 128
    assert s.pixels == [0x800000]

    s.tick()  # fades to full, enters 8-tick hold
    assert s.pixels == [0xFF0000]

    for _ in range(8):  # remaining hold ticks -- the tick hold_ticks hits 0 sets
        s.tick()         # target=0 but (elif chain) doesn't fade level same tick
        assert s.pixels == [0xFF0000]

    s.tick()  # first tick with hold_ticks==0: fade-out begins
    assert s.pixels == [0x7F0000]

    s.tick()  # fades fully out
    assert s.pixels == [0x000000]


def test_center_spread_grows_from_middle_then_swaps_layers():
    s = Strand(adi_port=1, length=6, refresh_ms=20)
    s.overlay_set_color(0x00FF00)
    s.center_spread(tick_interval=1)

    s.tick()
    assert s.spread_mask == [False, False, False, True, False, False]
    s.tick()
    assert s.spread_mask == [False, False, True, True, True, False]
    s.tick()
    assert s.spread_mask == [False, True, True, True, True, True]

    s.tick()  # spread_pos reaches max_pos (4) -> layer swap, mask resets
    assert s.spread_mask == [False] * 6
    assert s.buffer == [0x00FF00] * 6  # promoted from the overlay we set
    assert s.overlay_buffer == [0] * 6  # no stacked layers -> backfilled black


def test_brightness_scales_linearly_without_touching_buffer():
    s = Strand(adi_port=1, length=1, refresh_ms=20)
    s.set_color(0xFF8040)
    s.set_brightness(50)
    s.tick()
    assert s.pixels == [0x7F4020]
    assert s.buffer == [0xFF8040]  # buffer itself is untouched
