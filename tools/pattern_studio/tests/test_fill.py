"""The Fill meter's live source: mapping a reading onto the strip, and the
Studio's config -> engine bridge that previews it.

These mirror src/led_strand.cpp's levelSource()/advanceLevel() path, so a
change to the firmware that this file stops agreeing with is a change that
would make the preview lie about the robot.
"""

import math

from hitlib_sim import AnimMode, Strand

from pattern_studio.engine import make_strand
from pattern_studio.models import AnimationConfig, AnimationKind, StrandConfig


def _meter(length: int = 10, refresh_ms: int = 25) -> Strand:
    s = Strand(adi_port=1, length=length, refresh_ms=refresh_ms)
    s.level_fill(0xFFFFFF)
    return s


def _reading(*values: float):
    """A reader that returns each value in turn, then repeats the last one."""
    remaining = list(values)

    def read() -> float:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return read


# ============================================================================
# Mapping a reading onto the strip
# ============================================================================


def test_a_reading_lands_where_it_sits_between_empty_and_full():
    s = _meter()
    s.level_source(_reading(45.0), 20.0, 70.0)  # 45 is halfway
    s.tick()
    assert s.level_value == 128


def test_readings_outside_the_range_clamp_to_empty_and_full():
    s = _meter()
    s.level_source(_reading(-40.0), 0.0, 100.0)
    s.tick()
    assert s.level_value == 0

    s.level_source(_reading(400.0), 0.0, 100.0)
    s.tick()
    assert s.level_value == 255


def test_wrap_cycles_instead_of_pinning_at_full():
    # What a continuously turning motor wants: 450 degrees of a 0-360 range is
    # a quarter of the way round again, not a bar stuck at full.
    s = _meter()
    s.level_source(_reading(450.0), 0.0, 360.0, wrap=True)
    s.tick()
    assert s.level_value == 64

    # And it works backwards through zero too.
    s.level_source(_reading(-90.0), 0.0, 360.0, wrap=True)
    s.tick()
    assert s.level_value == 191


def test_a_backwards_range_reverses_the_meter():
    # "Full at 0, empty at 100" is how a bar that drains as a number climbs is
    # written, with no arithmetic of the caller's own.
    s = _meter()
    s.level_source(_reading(25.0), 100.0, 0.0)
    s.tick()
    assert s.level_value == 191


def test_an_empty_range_reads_as_empty_rather_than_dividing_by_zero():
    s = _meter()
    s.level_source(_reading(50.0), 50.0, 50.0)
    s.tick()
    assert s.level_value == 0


def test_the_meter_fills_the_strip_from_the_reading():
    s = _meter(length=10)
    s.level_source(_reading(50.0), 0.0, 100.0)
    s.tick()

    assert s.anim_mode == AnimMode.LEVEL
    assert s.pixels[:5] == [0xFFFFFF] * 5
    assert s.pixels[6:] == [0x000000] * 4


def test_an_unreadable_device_holds_the_fill_instead_of_slamming_it_full():
    # An unplugged device reports PROS_ERR_F, which is infinity. Clamping it
    # would light the whole strip and read as "everything is at maximum".
    s = _meter()
    s.level_source(_reading(50.0, math.inf), 0.0, 100.0)
    s.tick()
    assert s.level_value == 128

    s.tick()
    assert s.level_value == 128


# ============================================================================
# Smoothing
# ============================================================================


def test_the_first_reading_lands_outright_rather_than_gliding_up_to_it():
    # Otherwise every mode that opens on a smoothed meter would start with the
    # bar crawling up from empty, which looks like an animation and isn't one.
    s = _meter()
    s.level_source(_reading(100.0), 0.0, 100.0, smoothing=90)
    s.tick()
    assert s.level_value == 255


def test_smoothing_glides_toward_a_change_instead_of_snapping_to_it():
    s = _meter()
    s.level_source(_reading(0.0, 100.0), 0.0, 100.0, smoothing=50)
    s.tick()  # primes at 0
    assert s.level_value == 0

    s.tick()
    assert 0 < s.level_value < 255  # halfway, not there yet
    first_step = s.level_value

    s.tick()
    assert first_step < s.level_value < 255


def test_smoothing_still_reaches_the_target():
    # Integer truncation would otherwise stall the bar a few pixels short of a
    # target it is already close to.
    s = _meter()
    s.level_source(_reading(0.0, 100.0), 0.0, 100.0, smoothing=95)
    for _ in range(400):
        s.tick()
    assert s.level_value == 255


def test_a_smoothed_wrapping_meter_takes_the_short_way_round():
    # Rolling over from full to empty must not sweep back down the whole strip.
    s = _meter()
    s.level_source(_reading(355.0, 5.0), 0.0, 360.0, wrap=True, smoothing=50)
    s.tick()
    assert s.level_value == 251  # primed near the top

    s.tick()
    # Onward past the top and round to the bottom, not backwards through 128.
    assert s.level_value > 251 or s.level_value < 10


# ============================================================================
# Who drives the meter
# ============================================================================


def test_setting_a_level_by_hand_takes_the_source_off_the_meter():
    s = _meter()
    s.level_source(_reading(100.0), 0.0, 100.0)
    s.tick()

    s.set_level(40)
    s.tick()
    assert s.level_value == 40
    assert not s.level_source_active()


def test_clearing_the_source_leaves_the_fill_where_it_was():
    s = _meter()
    s.level_source(_reading(75.0), 0.0, 100.0)
    s.tick()
    held = s.level_value

    s.clear_level_source()
    s.tick()
    assert s.level_value == held


def test_a_song_takes_over_from_a_source():
    from hitlib_sim import MusicTrack

    s = _meter(refresh_ms=100)
    s.level_source(_reading(100.0), 0.0, 100.0)
    s.tick()

    s.music_sync(MusicTrack(samples=(0, 128, 255), frame_ms=100), 0xFFFFFF)
    assert not s.level_source_active()
    s.tick()
    assert s.level_value == 128


def test_relighting_the_meter_by_hand_detaches_the_source():
    s = _meter()
    s.level_source(_reading(100.0), 0.0, 100.0)
    s.tick()

    s.level_fill(0x00FF00)
    assert not s.level_source_active()


# ============================================================================
# Config -> engine bridge
# ============================================================================


def _fill_config(**animation) -> StrandConfig:
    cfg = StrandConfig(length=10, refresh_ms=100)
    cfg.animation = AnimationConfig(
        kind=AnimationKind.FILL, color=0x00FF00, gradient=False,
        source="motor_temp", source_port=11, source_empty=0, source_full=100,
        preview_sweep=False, preview_level=50,
    )
    for key, value in animation.items():
        setattr(cfg.animation, key, value)
    return cfg


def test_a_fill_animation_previews_at_the_panels_preview_level():
    strand = make_strand(_fill_config())
    strand.tick()
    assert strand.level_value == 128


def test_the_preview_level_is_a_percentage_of_the_configured_range():
    # The stand-in reading is in the source's own units, so it goes through the
    # same mapping the robot will use rather than bypassing it.
    strand = make_strand(_fill_config(source_empty=20, source_full=70, preview_level=100))
    strand.tick()
    assert strand.level_value == 255


def test_sweeping_moves_the_meter_over_time():
    strand = make_strand(_fill_config(preview_sweep=True))
    seen = set()
    for _ in range(40):
        strand.tick()
        seen.add(strand.level_value)
    assert len(seen) > 5


def test_a_manual_fill_still_previews_rather_than_sitting_dark():
    # Nothing polls a Manual meter on the robot, but a dark strip in the editor
    # would say nothing about the colors that were just picked.
    strand = make_strand(_fill_config(source="manual", source_empty=0, source_full=255,
                                      preview_level=100))
    strand.tick()
    assert strand.pixels == [0x00FF00] * 10
