"""The level meter and music playback, in the sim engine and through the
Studio's config -> engine bridge.

These mirror src/led_strand.cpp's levelFill/musicSync path, so a change to the
firmware that this file stops agreeing with is a change that would make the
preview lie about the robot.
"""

from hitlib_sim import AnimMode, MusicTrack, Strand

from pattern_studio.engine import apply_strand_config, make_music_binding, make_strand
from pattern_studio.models import AnimationConfig, AnimationKind, MusicConfig, StrandConfig


def _meter(length: int = 10, refresh_ms: int = 25) -> Strand:
    return Strand(adi_port=1, length=length, refresh_ms=refresh_ms)


# ============================================================================
# Level meter
# ============================================================================


def test_level_fills_from_the_first_pixel():
    s = _meter()
    s.level_fill(0x00FF00, bg_color=0x000000)
    s.set_level(128)
    s.render()

    # 128/255 of ten pixels: five full, the sixth part-lit, the rest dark.
    assert s.pixels[:5] == [0x00FF00] * 5
    assert 0 < s.pixels[5] < 0x00FF00
    assert s.pixels[6:] == [0x000000] * 4


def test_full_and_empty_are_exactly_that():
    s = _meter()
    s.level_fill(0x00FF00, bg_color=0x111111)

    s.set_level(255)
    s.render()
    assert s.pixels == [0x00FF00] * 10

    s.set_level(0)
    s.render()
    assert s.pixels == [0x111111] * 10


def test_the_pixel_at_the_edge_of_the_fill_is_dimmed_proportionally():
    # What buys a 30-LED strand more than 30 distinguishable levels: two levels
    # inside the same pixel have to look different.
    s = _meter(length=4)
    s.level_fill(0xFFFFFF)

    s.set_level(70)
    s.render()
    low = s.pixels[1]

    s.set_level(100)
    s.render()
    assert s.pixels[1] > low


def test_invert_fills_from_the_far_end_with_the_gradient_following():
    s = _meter(length=4)
    s.level_fill(0xFF0000, 0x0000FF, gradient=True, invert=True)
    s.set_level(255)
    s.render()

    forward = _meter(length=4)
    forward.level_fill(0xFF0000, 0x0000FF, gradient=True)
    forward.set_level(255)
    forward.render()

    # Same gradient, laid down the other way: the start color sits at the end
    # the fill starts from in both cases.
    assert s.pixels == list(reversed(forward.pixels))


def test_gradient_spans_the_strip_not_the_lit_part():
    # A meter's colors are a scale, so a given pixel must be the same color at
    # every level - a gradient stretched over the lit part would recolor the
    # whole fill every frame.
    s = _meter(length=8)
    s.level_fill(0xFF0000, 0x0000FF, gradient=True)

    s.set_level(255)
    s.render()
    full = list(s.pixels)

    s.set_level(128)
    s.render()
    assert s.pixels[:4] == full[:4]


# ============================================================================
# Music playback
# ============================================================================


def _ramp_track() -> MusicTrack:
    return MusicTrack(samples=(0, 128, 255, 0), frame_ms=100)


def test_music_sync_drives_the_level_from_the_envelope():
    s = _meter(refresh_ms=100)
    s.music_sync(_ramp_track(), 0xFFFFFF)
    assert s.anim_mode == AnimMode.LEVEL

    levels = []
    for _ in range(4):
        s.tick()
        levels.append(s.level_value)
    assert levels == [128, 255, 0, 0]


def test_frames_are_interpolated_so_a_coarse_envelope_stays_smooth():
    # The whole reason the export can afford a small table: the firmware fills
    # in between samples rather than stepping.
    s = _meter(refresh_ms=25)
    s.music_sync(_ramp_track(), 0xFFFFFF)

    levels = []
    for _ in range(4):
        s.tick()
        levels.append(s.level_value)
    assert levels == [32, 64, 96, 128]


def test_sensitivity_scales_the_envelope_and_clips_at_full():
    s = _meter(refresh_ms=100)
    s.music_sync(_ramp_track(), 0xFFFFFF, sensitivity=200)

    s.tick()
    assert s.level_value == 255  # 128 * 2, clipped
    quiet = _meter(refresh_ms=100)
    quiet.music_sync(_ramp_track(), 0xFFFFFF, sensitivity=50)
    quiet.tick()
    assert quiet.level_value == 64


def test_the_meter_goes_dark_past_the_end_unless_looping():
    s = _meter(refresh_ms=100)
    s.music_sync(_ramp_track(), 0xFFFFFF)
    s.music_seek(10_000)
    assert s.level_value == 0

    looped = _meter(refresh_ms=100)
    looped.music_sync(_ramp_track(), 0xFFFFFF, loop=True)
    looped.music_seek(400 + 200)  # one full pass plus 200 ms
    assert looped.level_value == 255


def test_seeking_moves_playback_without_stopping_it():
    s = _meter(refresh_ms=100)
    s.music_sync(_ramp_track(), 0xFFFFFF)
    s.music_seek(100)
    assert s.level_value == 128

    s.tick()  # 100 ms later, i.e. position 200
    assert s.level_value == 255


def test_pause_holds_the_level_and_resume_picks_up_where_it_left_off():
    s = _meter(refresh_ms=100)
    s.music_sync(_ramp_track(), 0xFFFFFF)
    s.music_seek(100)
    s.music_pause(True)

    for _ in range(5):
        s.tick()
    assert s.level_value == 128
    assert not s.music_playing()

    s.music_pause(False)
    s.tick()
    assert s.level_value == 255


def test_setting_a_level_by_hand_takes_the_song_off_the_meter():
    # Otherwise the next tick would overwrite whatever the caller just set, and
    # a sensor-driven meter would fight the song for the same pixels.
    s = _meter(refresh_ms=100)
    s.music_sync(_ramp_track(), 0xFFFFFF)
    s.set_level(200)
    s.tick()
    assert s.level_value == 200


# ============================================================================
# Config -> engine bridge
# ============================================================================


def _music_config() -> MusicConfig:
    music = MusicConfig(name="test", bands={"bass": [0, 128, 255, 0], "treble": [255, 0, 0, 0]})
    music.settings.frame_ms = 100
    return music


def test_a_music_animation_plays_the_documents_song():
    cfg = StrandConfig(length=10, refresh_ms=100, animation=AnimationConfig(
        kind=AnimationKind.MUSIC, color=0x00FF00, gradient=False))
    strand = make_strand(cfg, make_music_binding(_music_config()))

    strand.tick()
    assert strand.level_value == 128


def test_a_music_animation_with_no_song_previews_as_an_empty_meter():
    cfg = StrandConfig(length=10, animation=AnimationConfig(kind=AnimationKind.MUSIC))
    strand = make_strand(cfg, make_music_binding(None))

    strand.tick()
    assert strand.anim_mode == AnimMode.LEVEL
    assert strand.pixels == [0x000000] * 10


def test_loop_comes_from_the_song_not_the_strand():
    cfg = StrandConfig(length=4, refresh_ms=100, animation=AnimationConfig(kind=AnimationKind.MUSIC))
    music = _music_config()
    music.loop = True
    strand = make_strand(cfg, make_music_binding(music))

    strand.music_seek(600)  # one full pass plus 200 ms
    assert strand.level_value == 255


def test_reapplying_a_config_keeps_the_meter_wired_to_the_song():
    cfg = StrandConfig(length=10, refresh_ms=100, animation=AnimationConfig(
        kind=AnimationKind.MUSIC, color=0x00FF00))
    binding = make_music_binding(_music_config())
    strand = make_strand(cfg, binding)

    cfg.animation.color = 0xFF0000
    apply_strand_config(strand, cfg, binding)
    strand.tick()
    assert strand.level_value == 128
