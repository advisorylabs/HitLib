from hitlib_sim.colors import gen_gradient, gen_rainbow
from hitlib_sim.strand import BitScrollSegment, SpliceRegion, SpliceRegionAnimKind, Strand


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


def test_overlay_rainbow_animates_across_ticks():
    # Regression test: overlay_shift_step used to be advanced every tick by
    # _shift_overlay_buffer() but never actually read back in _flush_buffer(),
    # so overlay animations rendered as a single frozen frame.
    s = Strand(adi_port=1, length=4, refresh_ms=20)
    s.overlay_rainbow(speed=1)
    expected_overlay = gen_rainbow(4)
    assert s.overlay_buffer == expected_overlay
    s.splice_mask(sections=1, use_overlay=True)  # halves: [False, False, True, True] -- pixels 0,1 masked

    s.tick()
    assert s.overlay_shift_step == 1
    assert s.pixels[0] == expected_overlay[(0 + 1) % 4]
    assert s.pixels[1] == expected_overlay[(1 + 1) % 4]

    s.tick()
    assert s.overlay_shift_step == 2
    assert s.pixels[0] == expected_overlay[(0 + 2) % 4]
    assert s.pixels[1] == expected_overlay[(1 + 2) % 4]


def test_splice_mask_overlay_does_not_require_center_spread():
    # Overlay display in masked bins used to be gated on CENTER_SPREAD being
    # the active base animation; it's now driven purely by useOverlay, so a
    # plain rainbow + splice(useOverlay=True) should reveal the overlay too.
    s = Strand(adi_port=1, length=4, refresh_ms=20)
    s.rainbow(speed=1)
    s.overlay_set_color(0x00FF00)
    s.splice_mask(sections=1, bg_color=0x000000, use_overlay=True)  # halves: [False, False, True, True]

    s.tick()
    assert s.pixels[0] == 0x00FF00
    assert s.pixels[1] == 0x00FF00


def test_splice_mask_custom_regions_override_arbitrary_spans():
    s = Strand(adi_port=1, length=10, refresh_ms=20)
    s.set_color(0xFFFFFF)
    s.splice_mask_custom([
        SpliceRegion(start=0, width=2, kind=SpliceRegionAnimKind.SOLID, color=0xFF0000),
        SpliceRegion(start=7, width=3, kind=SpliceRegionAnimKind.SOLID, color=0x0000FF),
    ])

    s.tick()
    assert s.pixels == [
        0xFF0000, 0xFF0000, 0xFFFFFF, 0xFFFFFF, 0xFFFFFF,
        0xFFFFFF, 0xFFFFFF, 0x0000FF, 0x0000FF, 0x0000FF,
    ]


def test_splice_mask_custom_regions_animate_independently_and_simultaneously():
    # The whole point of custom regions over the shared overlay: two regions
    # can run different animations, at different speeds, at the same time.
    s = Strand(adi_port=1, length=8, refresh_ms=20)
    s.set_color(0x000000)
    s.splice_mask_custom([
        SpliceRegion(start=0, width=3, kind=SpliceRegionAnimKind.RAINBOW, speed=1),
        SpliceRegion(start=4, width=4, kind=SpliceRegionAnimKind.FLOW,
                     color=0xFF0000, color2=0x0000FF, speed=2),
    ])
    # Each region's buffer is generated over just its own width, not the full strip.
    expected_rainbow = gen_rainbow(3)
    expected_flow = gen_gradient(0xFF0000, 0x0000FF, 4)

    s.tick()
    assert s.pixels[0:3] == [expected_rainbow[(i + 1) % 3] for i in range(3)]
    assert s.pixels[3] == 0x000000  # gap between regions still shows the base animation
    assert s.pixels[4:8] == [expected_flow[(i + 2) % 4] for i in range(4)]

    s.tick()
    assert s.pixels[0:3] == [expected_rainbow[(i + 2) % 3] for i in range(3)]
    assert s.pixels[4:8] == [expected_flow[(i + 4) % 4] for i in range(4)]


def test_splice_mask_custom_does_not_alternate():
    s = Strand(adi_port=1, length=4, refresh_ms=20)
    s.splice_mask_custom([SpliceRegion(start=0, width=2)])
    assert s.splice_alternating is False

    for _ in range(20):
        s.tick()
    assert s.splice_show_anim == [False, False, True, True]


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
