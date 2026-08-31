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


def test_flash_blinks_whole_strip_on_and_off():
    # Every LED must light together, then go dark together. Advancing the
    # buffer per tick would scroll the lit block and render as a pulse.
    s = Strand(adi_port=1, length=4, refresh_ms=20)
    s.flash(color=0xFF0000, on_ms=40, off_ms=40)

    on = [0xFF0000] * 4
    off = [0x000000] * 4
    frames = [(s.tick(), s.pixels)[1] for _ in range(8)]
    assert frames == [on, on, off, off, on, on, off, off]


def test_flash_on_and_off_times_are_independent():
    # on_ms and off_ms set duty cycle and rate separately: changing one must
    # not move the other.
    s = Strand(adi_port=1, length=1, refresh_ms=25)
    s.flash(color=0xFF0000, on_ms=100, off_ms=200)
    assert (s.flash_on_ticks, s.flash_off_ticks) == (4, 8)

    seq = []
    for _ in range(24):
        s.tick()
        seq.append(1 if s.pixels[0] else 0)
    # 4 ticks lit, 8 ticks blank, repeating exactly, with no drift.
    assert seq == [1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0] * 2


def test_flash_durations_below_one_tick_clamp_up():
    # A duration shorter than the refresh interval can't be honoured; it must
    # round up to a single tick rather than collapsing to zero and toggling
    # the phase every frame.
    s = Strand(adi_port=1, length=1, refresh_ms=25)
    s.flash(color=0x00FF00, on_ms=1, off_ms=1)
    assert (s.flash_on_ticks, s.flash_off_ticks) == (1, 1)

    seq = []
    for _ in range(6):
        s.tick()
        seq.append(1 if s.pixels[0] else 0)
    assert seq == [1, 0, 1, 0, 1, 0]


def test_overlay_flash_blinks_whole_strip():
    s = Strand(adi_port=1, length=4, refresh_ms=20)
    s.overlay_flash(color=0xFF0000, on_ms=20, off_ms=20)
    s.splice_mask(sections=1, use_overlay=True)  # pixels 0,1 show the overlay

    s.tick()
    assert s.pixels[:2] == [0xFF0000, 0xFF0000]
    s.tick()
    assert s.pixels[:2] == [0x000000, 0x000000]
    s.tick()
    assert s.pixels[:2] == [0xFF0000, 0xFF0000]


def test_splice_region_flash_blinks_only_its_own_region():
    s = Strand(adi_port=1, length=4, refresh_ms=20)
    s.set_color(0x0000FF)
    s.splice_mask_custom([
        SpliceRegion(start=0, width=2, kind=SpliceRegionAnimKind.FLASH,
                     color=0xFF0000, bg_color=0x000000, on_ms=20, off_ms=20),
    ])

    s.tick()
    assert s.pixels == [0xFF0000, 0xFF0000, 0x0000FF, 0x0000FF]
    s.tick()
    assert s.pixels == [0x000000, 0x000000, 0x0000FF, 0x0000FF]
    s.tick()
    assert s.pixels == [0xFF0000, 0xFF0000, 0x0000FF, 0x0000FF]


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
    s.splice_mask(sections=2)  # 3 bins: sizes 3,2,2. Bin 1 (odd) shows animation
    assert s.splice_show_anim == [False, False, False, True, True, False, False]


def test_overlay_rainbow_animates_across_ticks():
    # _flush_buffer() must read overlay_shift_step back, or every overlay
    # animation renders as a single frozen frame.
    s = Strand(adi_port=1, length=4, refresh_ms=20)
    s.overlay_rainbow(speed=1)
    expected_overlay = gen_rainbow(4)
    assert s.overlay_buffer == expected_overlay
    s.splice_mask(sections=1, use_overlay=True)  # halves: [False, False, True, True], pixels 0,1 masked

    s.tick()
    assert s.overlay_shift_step == 1
    assert s.pixels[0] == expected_overlay[(0 + 1) % 4]
    assert s.pixels[1] == expected_overlay[(1 + 1) % 4]

    s.tick()
    assert s.overlay_shift_step == 2
    assert s.pixels[0] == expected_overlay[(0 + 2) % 4]
    assert s.pixels[1] == expected_overlay[(1 + 2) % 4]


def test_splice_mask_overlay_does_not_require_center_spread():
    # Overlay display in masked bins is driven purely by useOverlay, not by
    # which base animation is active, so a plain rainbow plus
    # splice(useOverlay=True) reveals the overlay.
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
    # Custom regions, unlike the shared overlay, can run different animations
    # at different speeds at the same time.
    s = Strand(adi_port=1, length=8, refresh_ms=20)
    s.set_color(0x000000)
    s.splice_mask_custom([
        SpliceRegion(start=0, width=3, kind=SpliceRegionAnimKind.RAINBOW, speed=1),
        SpliceRegion(start=4, width=4, kind=SpliceRegionAnimKind.FLOW,
                     color=0xFF0000, color2=0x0000FF, speed=2),
    ])
    # Each region's buffer is generated over just its own width, not the full strip.
    expected_rainbow = gen_rainbow(3)
    expected_flow = gen_gradient(0xFF0000, 0x0000FF, 4, seamless=True)

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


def test_bitscroll_default_spacing_is_five():
    s = Strand(adi_port=1, length=14, refresh_ms=20)
    s.bitscroll(segments=[BitScrollSegment(color=0xFF0000, width=2)], speed=1, bg_color=0)
    # One tile = 2 lit pixels followed by the default 5-pixel gap.
    assert s.buffer[:7] == [0xFF0000, 0xFF0000, 0, 0, 0, 0, 0]


def test_bitscroll_bounce_honours_repeating_false():
    # With `repeating` off, a single copy must travel the strip and rock back
    # rather than being tiled across the master buffer.
    s = Strand(adi_port=1, length=6, refresh_ms=20)
    s.bitscroll(
        segments=[BitScrollSegment(color=0xFF0000, width=2)],
        speed=1,
        bg_color=0,
        bounce=True,
        spacing=3,
        repeating=False,
    )

    lit = []
    for _ in range(8):
        s.tick()
        lit.append([i for i, p in enumerate(s.pixels) if p])

    # Exactly one 2-pixel run at all times, never a second, tiled copy,
    # travelling to the near end and back.
    assert lit == [[3, 4], [2, 3], [1, 2], [0, 1], [1, 2], [2, 3], [3, 4], [4, 5]]


def test_bitscroll_bounce_repeating_true_still_tiles():
    s = Strand(adi_port=1, length=6, refresh_ms=20)
    s.bitscroll(
        segments=[BitScrollSegment(color=0xFF0000, width=2)],
        speed=1,
        bg_color=0,
        bounce=True,
        spacing=3,
        repeating=True,
    )
    s.tick()
    # Tiled: more than one run of lit pixels is visible at once.
    assert sum(1 for p in s.pixels if p) > 2


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

    for _ in range(8):  # remaining hold ticks. The tick hold_ticks hits 0 sets
        s.tick()         # target=0 but (elif chain) doesn't fade level same tick
        assert s.pixels == [0xFF0000]

    s.tick()  # first tick with hold_ticks==0: fade-out begins
    assert s.pixels == [0x7F0000]

    s.tick()  # fades fully out
    assert s.pixels == [0x000000]


def test_brightness_scales_linearly_without_touching_buffer():
    s = Strand(adi_port=1, length=1, refresh_ms=20)
    s.set_color(0xFF8040)
    s.set_brightness(50)
    s.tick()
    assert s.pixels == [0x7F4020]
    assert s.buffer == [0xFF8040]  # buffer itself is untouched


def test_overlay_twinkle_sparkles_only_in_the_masked_bins():
    s = Strand(adi_port=1, length=8, refresh_ms=20)
    s.set_color(0x00FF00)
    s.overlay_twinkle(colors=[0xFF0000], density_pct=100, fade_step=255)
    s.splice_mask(sections=1, use_overlay=True)  # halves: pixels 0-3 masked

    for _ in range(8):
        s.tick()

    assert any(p for p in s.pixels[:4]), "the masked half should have sparks in it"
    assert s.pixels[4:] == [0x00FF00] * 4, "the unmasked half still shows the base animation"


def test_overlay_bitscroll_scrolls_its_own_buffer():
    s = Strand(adi_port=1, length=4, refresh_ms=20)
    s.overlay_bitscroll(
        segments=[BitScrollSegment(color=0xFF0000, width=1)], speed=1, bg_color=0x000000, spacing=1
    )
    expected = list(s.overlay_buffer)
    s.splice_mask(sections=1, use_overlay=True)  # halves: pixels 0,1 masked

    s.tick()
    assert s.overlay_shift_step == 1
    assert s.pixels[0] == expected[1 % len(expected)]
    assert s.pixels[1] == expected[2 % len(expected)]


def test_a_twinkle_region_sparkles_over_only_its_own_pixels():
    s = Strand(adi_port=1, length=8, refresh_ms=20)
    s.set_color(0x0000FF)
    s.splice_mask_custom([
        SpliceRegion(start=0, width=4, kind=SpliceRegionAnimKind.TWINKLE,
                     palette=(0xFF0000,), density_pct=100, fade_step=255),
    ])

    for _ in range(8):
        s.tick()

    assert any(p for p in s.pixels[:4]), "the region should have sparks in it"
    assert s.pixels[4:] == [0x0000FF] * 4


def test_a_bitscroll_region_scrolls_over_only_its_own_pixels():
    s = Strand(adi_port=1, length=8, refresh_ms=20)
    s.set_color(0x0000FF)
    s.splice_mask_custom([
        SpliceRegion(start=0, width=4, kind=SpliceRegionAnimKind.BITSCROLL,
                     color=0xFF0000, speed=1, segment_width=1, spacing=1),
    ])
    # Region buffers are built over the region's width, not the whole strip.
    region = s.splice_regions[0].buffer
    assert set(region) == {0xFF0000, 0x000000}

    s.tick()
    assert s.pixels[:4] == [region[(i + 1) % len(region)] for i in range(4)]
    assert s.pixels[4:] == [0x0000FF] * 4
