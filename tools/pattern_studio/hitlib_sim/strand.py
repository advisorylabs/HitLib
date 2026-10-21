"""Port of include/hitlib/led_strand.hpp + src/led_strand.cpp.

This mirrors LedStrand method-for-method so profile authoring in the GUI maps
1:1 onto the real API, and so exported C++ is a direct transliteration rather 
than a re-derivation.

Threading/locking from the original (pros::Mutex, the shared s_adiMutex, and
the release-around-user-callback dance in tick()) is intentionally dropped.
This is a single-threaded simulation object, not a hardware driver.

now_ms is a simulated clock, not wall-clock time: it advances by exactly
refresh_ms on every tick() call, mirroring the embedded assumption that
LedGroup's task calls tick() once per refresh_ms. Any caller (a Sequencer, a
ProfileMode callback) that needs "now" for timing should read strand.now_ms.
"""

from __future__ import annotations

import math
import math
import random
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Sequence

from .colors import (
    gen_gradient,
    gradient_pixel,
    gen_rainbow,
    lerp_color,
    sample_track,
    scale_color_at,
    trunc_div,
    wheel,
)
from .profile import Profile

MAX_LEDS = 64
_TWINKLE_HOLD_TICKS = 8


class AnimMode(Enum):
    STATIC = auto()
    SHIFT = auto()
    TWINKLE = auto()
    FLASH = auto()
    LEVEL = auto()


class SpliceMode(Enum):
    SPLIT = auto()
    CUSTOM = auto()


@dataclass(frozen=True)
class BitScrollSegment:
    color: int
    width: int


@dataclass(frozen=True)
class Speed:
    """Port of LedStrand::Speed - so many pixels, so often.

    A bare int anywhere a speed is taken is pixels per refresh tick, which is
    what `speed` has always meant. `step_ms` is the time between steps,
    rounded to whole refresh ticks the way a flash's durations are, so the
    pace is even; 0 steps on every tick.
    """

    pixels: int = 1
    step_ms: int = 0

    @staticmethod
    def every_ms(ms: int, pixels: int = 1) -> "Speed":
        return Speed(pixels, ms)


def as_speed(speed) -> Speed:
    """A Speed from a Speed or a bare pixels-per-tick int. The firmware takes
    the int through a uint8_t, so this does too."""
    if isinstance(speed, Speed):
        return Speed(speed.pixels & 0xFF, speed.step_ms & 0xFFFF)
    return Speed(int(speed) & 0xFF, 0)


@dataclass
class _StepPacer:
    """Port of LedStrand::StepPacer: due() is true once every `hold` refresh
    ticks. A hold of 1 - what a bare-number Speed gives - moves on every tick,
    which is all `speed` could do before it could be slower."""

    hold: int = 1
    counter: int = 0

    def reset(self, ticks: int) -> None:
        self.hold = ticks if ticks else 1
        self.counter = 0

    def restart(self) -> None:
        self.counter = 0

    def due(self) -> bool:
        self.counter += 1
        if self.counter < self.hold:
            return False
        self.counter = 0
        return True


@dataclass(frozen=True)
class MusicTrack:
    """Port of LedStrand::MusicTrack - a song reduced to one intensity sample
    per frame, baked on the desktop because the V5 can neither hear audio nor
    decode MIDI. See pattern_studio.midi for how one is built from a file.
    """

    samples: tuple[int, ...] = ()
    frame_ms: int = 25

    @property
    def frame_count(self) -> int:
        return len(self.samples)

    @property
    def duration_ms(self) -> int:
        return self.frame_count * self.frame_ms


class SpliceRegionAnimKind(Enum):
    """Mirrors LedStrand::SpliceRegionAnimKind.

    OFF through BITSCROLL share the overlay* animations' vocabulary, since each
    region's buffer is built the same way. GAUGE animates from a reading rather
    than from a clock, making the region an independent meter.
    """

    OFF = auto()
    SOLID = auto()
    PULSE = auto()
    FLASH = auto()
    FLOW = auto()
    RAINBOW = auto()
    TWINKLE = auto()
    BITSCROLL = auto()
    GAUGE = auto()


@dataclass(frozen=True)
class GaugeStop:
    """Port of LedStrand::GaugeStop - one color on a gauge's scale, given in
    the reading's own units rather than in pixels or in 0-255."""

    at: float
    color: int


class GaugeStyle(Enum):
    """Port of LedStrand::GaugeStyle."""

    HEAT = auto()  # the whole region shows one color off the scale
    BAR = auto()   # the region fills proportionally, a miniature level_fill()


class GaugeBlend(Enum):
    """Port of LedStrand::GaugeBlend."""

    LERP = auto()  # blend between stops
    STEP = auto()  # hold each stop until the next is reached


#: The V5 motor's own heat schedule, as a gauge scale. Port of the table in
#: LedStrand::motorHeatGauge(): the stops are the temperatures the motor
#: actually changes behaviour at, not evenly spaced points on a ramp.
MOTOR_HEAT_STOPS: tuple[GaugeStop, ...] = (
    GaugeStop(20.0, 0x00FF00),  # cold, full power
    GaugeStop(45.0, 0xFFFF00),  # warm - the first current cut is coming
    GaugeStop(55.0, 0xFF7000),  # current limited to 50%
    GaugeStop(60.0, 0xFF2000),  # current limited to 25%
    GaugeStop(65.0, 0xFF0000),  # current limited to 12.5%
    GaugeStop(70.0, 0xFF00FF),  # shut down - deliberately off the red ramp
)


@dataclass(frozen=True)
class SpliceRegion:
    """One independently placed override region for a custom splice mask.
    Each region gets its own animation buffer, generated over just that
    region's width, and animates independently of every other region and of
    the base/overlay buffers.
    """

    start: int
    width: int
    kind: SpliceRegionAnimKind = SpliceRegionAnimKind.OFF
    color: int = 0xFFFFFF
    color2: int = 0x0000FF
    bg_color: int = 0x000000
    run_length: int = 5
    speed: int | Speed = 1  # PULSE/FLOW/RAINBOW/BITSCROLL motion, see Speed.
    on_ms: int = 250
    off_ms: int = 250
    seamless: bool = True  # FLOW only.
    # Reverse direction: a BITSCROLL scrolls the other way, a GAUGE BAR fills
    # from the far end of the region.
    invert: bool = False
    # TWINKLE only.
    palette: tuple[int, ...] = ()
    density_pct: int = 30
    fade_step: int = 16
    # BITSCROLL only.
    segment_width: int = 3
    spacing: int = 5
    repeating: bool = True
    # GAUGE only, ignored by every other kind. `read` of None leaves the
    # region hand-driven through set_region_level().
    read: Optional[Callable[[], float]] = None
    empty_at: float = 0.0
    full_at: float = 100.0
    wrap: bool = False
    smoothing: int = 0
    style: GaugeStyle = GaugeStyle.HEAT
    blend: GaugeBlend = GaugeBlend.LERP
    #: The scale, in the reading's own units. Empty falls back to `color` at
    #: `empty_at` blending to `color2` at `full_at`.
    stops: tuple[GaugeStop, ...] = ()


@dataclass
class _TwinkleState:
    """Per-pixel twinkle runtime for one buffer - port of
    LedStrand::TwinkleState.

    Held as its own object rather than as loose strand fields because three
    different buffers animate this way: the base strand, the overlay, and any
    number of custom regions.
    """

    palette: list[int] = field(default_factory=list)
    level: list[int] = field(default_factory=list)
    target: list[int] = field(default_factory=list)
    color_idx: list[int] = field(default_factory=list)
    hold_ticks: list[int] = field(default_factory=list)
    density_pct: int = 30
    fade_step: int = 16
    bg_color: int = 0x000000

    def reset(self, width: int, colors: Sequence[int], density_pct: int, fade_step: int,
              bg_color: int) -> None:
        self.palette = list(colors)
        self.level = [0] * width
        self.target = [0] * width
        self.color_idx = [0] * width
        self.hold_ticks = [0] * width
        self.density_pct = min(density_pct, 100)
        self.fade_step = max(fade_step, 1)
        self.bg_color = bg_color


def _advance_twinkle_into(t: _TwinkleState, buf: list[int], rng: random.Random) -> None:
    """Repaint `buf` from `t`, one tick's worth - mirrors
    LedStrand::advanceTwinkleInto(). The buffer is passed in rather than read
    off the strand so the base strand, the overlay and each custom region can
    all sparkle at once, each over its own pixels."""
    width = min(len(t.level), len(buf))
    if width == 0:
        return
    _step_twinkle(t, width, rng)
    _paint_twinkle_into(t, buf, width)


def _step_twinkle(t: _TwinkleState, width: int, rng: random.Random) -> None:
    """Advance the sparkle state one tick without painting anything - mirrors
    LedStrand::stepTwinkle(). Split out so a custom-animation layer can take
    `t.level` as per-pixel coverage and blend it over whatever is beneath,
    instead of over a single background color."""
    if width == 0:
        return

    active_count = sum(1 for i in range(width) if t.level[i] > 0 or t.hold_ticks[i] > 0)
    target_count = (width * t.density_pct + 50) // 100

    if active_count < target_count and t.palette:
        # Reservoir-sample one idle pixel to spawn, at most one per tick.
        chosen = -1
        idle_seen = 0
        for i in range(width):
            if t.level[i] == 0 and t.hold_ticks[i] == 0:
                idle_seen += 1
                if rng.randrange(idle_seen) == 0:
                    chosen = i
        if chosen >= 0:
            t.color_idx[chosen] = rng.randrange(len(t.palette))
            t.target[chosen] = 255

    for i in range(width):
        if t.hold_ticks[i] > 0:
            t.hold_ticks[i] -= 1
            if t.hold_ticks[i] == 0:
                t.target[i] = 0
        elif t.level[i] < t.target[i]:
            t.level[i] = min(t.level[i] + t.fade_step, 255)
            if t.level[i] >= t.target[i]:
                t.hold_ticks[i] = _TWINKLE_HOLD_TICKS
        elif t.level[i] > t.target[i]:
            t.level[i] = max(t.level[i] - t.fade_step, 0)


def _paint_twinkle_into(t: _TwinkleState, buf: list[int], width: int) -> None:
    """Write the sparkle state into `buf` - mirrors
    LedStrand::paintTwinkleInto()."""
    for i in range(width):
        fg = t.palette[t.color_idx[i]] if t.palette else 0
        buf[i] = lerp_color(t.bg_color, fg, t.level[i])


def _build_bitscroll_unit(segments: Sequence[BitScrollSegment], bg_color: int,
                          spacing: int,
                          coverage: list[int] | None = None) -> tuple[list[int], int]:
    """One tile of a bitscroll pattern, plus its content length - the size up
    to the last lit pixel, i.e. without the trailing gap, which only separates
    one tile from the next. Mirrors LedStrand::buildBitscrollUnit().

    Capped at MAX_LEDS: a pattern wider than the whole strip can't usefully
    tile, and the cap keeps the downstream size math in range.

    `coverage`, when given, is filled in the same pass to the same length as
    the unit - 255 for a lit pixel, 0 for a gap - so a custom-animation layer
    can blend the tile over what is beneath it instead of over one background
    color. Filled here rather than derived afterwards so it cannot drift from
    the colors it describes.
    """
    unit: list[int] = []
    content_len = 0
    if coverage is not None:
        coverage.clear()

    def _extend(color: int, count: int, lit: int) -> None:
        room = min(count, MAX_LEDS - len(unit))
        if room <= 0:
            return
        unit.extend([color] * room)
        if coverage is not None:
            coverage.extend([lit] * room)

    for seg in segments:
        _extend(seg.color, seg.width, 255)
        content_len = len(unit)
        if len(unit) >= MAX_LEDS:
            break
        _extend(bg_color, spacing, 0)
        if len(unit) >= MAX_LEDS:
            break
    if not unit:
        _extend(bg_color, 1, 0)
        content_len = len(unit)
    return unit, content_len


def _fill_bitscroll_buffer(unit: list[int], content_len: int, width: int, bg_color: int,
                           speed: int, invert: bool, repeating: bool) -> tuple[list[int], int]:
    """Lay one tile across `width` pixels - tiled when `repeating`, a single
    copy on `bg_color` when not - and return that buffer with the per-step
    shift that scrolls it. `speed` is pixels per step. Mirrors
    LedStrand::fillBitscrollBuffer()."""
    if repeating:
        reps = (width // len(unit)) + 2
        buf = unit * reps
    else:
        buf = [bg_color] * width
        n = min(content_len, width)
        buf[:n] = unit[:n]
    # A zero-width region that doesn't repeat has nothing to scroll.
    if not buf:
        return buf, 0
    sp = speed % len(buf)
    return buf, ((len(buf) - sp) % len(buf)) if invert else sp


@dataclass
class _SpliceRegionState:
    """Runtime animation state for one CUSTOM-mode region, a scaled-down
    version of the overlay buffer (buffer + shift step/speed), but one per
    region instead of shared.
    """

    start: int
    buffer: list[int]
    shift_step: int = 0
    shift_speed: int = 0
    shift_pacer: _StepPacer = field(default_factory=_StepPacer)
    # Index in the sequence passed to splice_mask_custom(), which
    # set_region_level() addresses. Not the index in splice_regions: SOLID and
    # OFF regions never get a state, so the two disagree.
    user_idx: int = 0
    # TWINKLE regions repaint their buffer every tick rather than shifting it,
    # the same way the strand-wide twinkle does.
    twinkling: bool = False
    twinkle: _TwinkleState = field(default_factory=_TwinkleState)
    # FLASH regions blink their whole buffer on a tick timer instead of
    # shifting it, mirroring the strand-wide flash state.
    flashing: bool = False
    flash_color: int = 0
    flash_bg_color: int = 0
    flash_on_ticks: int = 1
    flash_off_ticks: int = 1
    flash_counter: int = 0
    flash_lit: bool = True
    # GAUGE - a per-region copy of the strand-wide meter's state, so each
    # segment follows its own reading at its own range and smoothing.
    gauge: bool = False
    level_read: Optional[Callable[[], float]] = None
    level_empty_at: float = 0.0
    level_full_at: float = 100.0
    level_wrap: bool = False
    level_smooth: int = 0
    level_value: int = 0
    level_primed: bool = False
    gauge_invert: bool = False
    gauge_style: GaugeStyle = GaugeStyle.HEAT
    gauge_blend: GaugeBlend = GaugeBlend.LERP
    gauge_bg: int = 0x000000
    #: The scale resolved onto the 0-255 axis level_value lives on, sorted, so
    #: a tick only has to bracket a byte.
    gauge_stops: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class _ModeEntry:
    mode_idx: int
    persistent: bool
    end_ms: int


class Strand:
    def __init__(self, adi_port: int, length: int, refresh_ms: int = 20, smart_port: int = 0):
        self.adi_port = adi_port
        self.smart_port = smart_port
        self.length = max(1, min(length, MAX_LEDS))
        self.refresh_ms = refresh_ms
        self.now_ms = 0

        self.buffer: list[int] = [0] * self.length
        self.overlay_buffer: list[int] = [0] * self.length
        self.splice_show_anim: list[bool] = [True] * self.length
        self.splice_pixel_bg: list[int] = [0] * self.length
        self.splice_pixel_use_overlay: list[bool] = [False] * self.length
        self.splice_pixel_region_idx: list[int] = [-1] * self.length
        self.splice_regions: list[_SpliceRegionState] = []

        # Rendered output of the last tick(), what the GUI should draw.
        self.pixels: list[int] = [0] * self.length

        self.anim_mode = AnimMode.STATIC
        self.shift_step = 0
        self.shift_variant = 0  # pixels per step, pre-reversed for invert
        self.shift_pacer = _StepPacer()  # SHIFT, and both bounces

        # Pulse bounce
        self.pulse_run_len = 0
        self.pulse_color = 0
        self.pulse_bg = 0
        self.pulse_speed = 1
        self.pulse_offset = 0
        self.pulse_dir = 1

        # Flash - whole-strip blink driven by tick counts rather than a
        # shifting buffer, so the lit and blank halves have independent
        # durations.
        self.flash_color = 0
        self.flash_bg_color = 0
        self.flash_on_ticks = 1
        self.flash_off_ticks = 1
        self.flash_counter = 0
        self.flash_lit = True

        # Overlay twinkle - mirrors the base twinkle state above.
        self.overlay_twinkle_state = _TwinkleState()

        # Overlay flash - mirrors the base flash state above.
        self.overlay_flash_color = 0
        self.overlay_flash_bg_color = 0
        self.overlay_flash_on_ticks = 1
        self.overlay_flash_off_ticks = 1
        self.overlay_flash_counter = 0
        self.overlay_flash_lit = True

        # Bitscroll bounce
        self.bitscroll_master: list[int] = []
        self.bounce_scroll_pos = 0
        self.bounce_scroll_dir = 1
        self.bounce_speed = 1

        # Twinkle
        self.twinkle_state = _TwinkleState()
        self._rng = random.Random()

        # Level meter - buffer holds the meter's colors across the whole
        # strip and level_value picks how much of it is revealed, so changing
        # the level costs nothing until flush time.
        self.level_value = 0
        self.level_bg = 0x000000
        self.level_invert = False

        # Live fill source - a reader polled once per tick plus the range that
        # maps what it returns onto 0-255. level_primed exists so the first
        # sample after level_source() lands instantly instead of the bar
        # crawling up to it from empty through the smoothing filter.
        self.level_read: Optional[Callable[[], float]] = None
        self.level_empty_at = 0.0
        self.level_full_at = 100.0
        self.level_wrap = False
        self.level_smooth = 0
        self.level_primed = False

        # Music sync - playback is a clock anchor rather than a tick counter,
        # mirroring the firmware. now_ms stands in for pros::millis() here,
        # so Pattern Studio can scrub by seeking.
        self.music_track: Optional[MusicTrack] = None
        self.music_anchor_ms = 0  # now_ms that corresponds to position 0
        self.music_paused_at = 0  # position held while paused
        self.music_paused = False
        self.music_loop = False
        self.music_sensitivity = 100

        # Splice mask
        self.splice_active = False
        self.splice_mode = SpliceMode.SPLIT
        self.splice_sections = 0
        self.splice_invert = False
        self.splice_alternating = False
        self.splice_alt_ms = 100
        self.splice_bg_color = 0
        self.splice_use_overlay = False
        self.splice_last_toggle_ms = 0

        # Overlay buffer
        self.overlay_anim_mode = AnimMode.STATIC
        self.overlay_shift_step = 0
        self.overlay_shift_speed = 0
        self.overlay_shift_pacer = _StepPacer()

        # Brightness
        self.brightness_pct = 100

        # Composition (custom animation) state
        self.composition = None
        self.compose_anchor_ms = 0
        # Bumped by compose() and clear_composition(), see _eval_drivers().
        self.compose_generation = 0
        self.compose_music_paused_at = 0
        self.compose_music_paused = False
        self.layer_states: list = []
        self.driver_value = [0] * _comp.MAX_DRIVERS
        self.driver_primed = [False] * _comp.MAX_DRIVERS

        # Profile state
        self.active_profile: Optional[Profile] = None
        self.mode_stack: list[_ModeEntry] = []
        self.last_mode_idx = -1

    # ========================================================================
    # tick() - call once per refresh_ms.
    # ========================================================================

    def tick(self) -> None:
        self.now_ms += self.refresh_ms
        now = self.now_ms
        self._prune_expired(now)

        # A composition writes the finished frame straight into buffer[] and
        # leaves anim_mode STATIC, so _flush_buffer()'s pass-through branch
        # picks it up and nothing downstream needs to know it is there.
        if self.composition is not None:
            self._advance_composition(now)
        elif self.pulse_run_len > 0:
            self._advance_pulse_bounce()
        elif self.bitscroll_master:
            self._advance_bitscroll_bounce()
        elif self.anim_mode == AnimMode.TWINKLE:
            _advance_twinkle_into(self.twinkle_state, self.buffer, self._rng)
        elif self.anim_mode == AnimMode.FLASH:
            self._advance_flash()
        elif self.anim_mode == AnimMode.LEVEL:
            self._advance_level()
        elif self.anim_mode == AnimMode.SHIFT:
            self._shift_buffer()

        if self.overlay_anim_mode == AnimMode.SHIFT:
            self._shift_overlay_buffer()
        elif self.overlay_anim_mode == AnimMode.FLASH:
            self._advance_overlay_flash()
        elif self.overlay_anim_mode == AnimMode.TWINKLE:
            _advance_twinkle_into(self.overlay_twinkle_state, self.overlay_buffer, self._rng)

        self._advance_splice_alternating(now)
        self._advance_splice_regions()

        effective_idx = self._compute_effective_mode()
        mode_changed = effective_idx != self.last_mode_idx
        self.last_mode_idx = effective_idx

        mode = None
        if effective_idx >= 0 and self.active_profile:
            mode = self.active_profile.modes[effective_idx]

        if mode_changed and mode and mode.on_activate:
            mode.on_activate(self)
        if mode and mode.on_tick:
            mode.on_tick(self)

        self._flush_buffer()

    # ========================================================================
    # Base animations
    # ========================================================================

    def off(self) -> None:
        self.set_color(0)

    def set_color(self, color: int) -> None:
        self.composition = None  # a preset takes the base buffer over
        self.pulse_run_len = 0
        self.bitscroll_master = []
        self.buffer = [color] * self.length
        self.shift_step = 0
        self.shift_variant = 0
        self.anim_mode = AnimMode.STATIC

    def pulse(self, color: int, run_length: int, speed: int | Speed, bg_color: int = 0,
              invert: bool = False, bounce: bool = False) -> None:
        speed = as_speed(speed)
        self.composition = None  # a preset takes the base buffer over
        self.bitscroll_master = []
        self.shift_pacer.reset(self._step_ticks(speed))
        if not bounce:
            self.pulse_run_len = 0
            self.buffer = [bg_color] * self.length
            rl = min(run_length, self.length)
            for i in range(rl):
                self.buffer[i] = color
            self.shift_step = 0
            sp = speed.pixels % self.length
            self.shift_variant = ((self.length - sp) % self.length) if invert else sp
            self.anim_mode = AnimMode.SHIFT
        else:
            self.pulse_color = color
            self.pulse_bg = bg_color
            self.pulse_run_len = max(1, min(run_length, self.length))
            self.pulse_speed = max(speed.pixels, 1)
            self.pulse_offset = (self.length - self.pulse_run_len) if invert else 0
            self.pulse_dir = -1 if invert else 1
            self.anim_mode = AnimMode.STATIC  # content generated per-tick by _advance_pulse_bounce()
            # _advance_pulse_bounce() repaints before the next flush, but a
            # call from a ProfileMode callback lands after this tick's advance,
            # so paint the starting frame here or that flush shows the
            # previous animation.
            self.buffer = [bg_color] * self.length
            for i in range(self.pulse_offset, self.pulse_offset + self.pulse_run_len):
                self.buffer[i] = color

    def _advance_pulse_bounce(self) -> None:
        # Paints on every tick even when it does not move, so the buffer can
        # never disagree with pulse_offset whatever else touched it.
        if self.shift_pacer.due():
            self.pulse_offset += self.pulse_dir * self.pulse_speed
            max_offset = max(0, self.length - self.pulse_run_len)
            if self.pulse_offset >= max_offset:
                self.pulse_offset = max_offset
                self.pulse_dir = -1
            if self.pulse_offset <= 0:
                self.pulse_offset = 0
                self.pulse_dir = 1

        self.buffer = [self.pulse_bg] * self.length
        end = min(self.pulse_offset + self.pulse_run_len, self.length)
        for i in range(self.pulse_offset, end):
            self.buffer[i] = self.pulse_color

    def flash(self, color: int, on_ms: int, off_ms: int, bg_color: int = 0) -> None:
        self.composition = None  # a preset takes the base buffer over
        self.pulse_run_len = 0
        self.bitscroll_master = []
        self.flash_color = color
        self.flash_bg_color = bg_color
        self.flash_on_ticks = self._ms_to_ticks(on_ms)
        self.flash_off_ticks = self._ms_to_ticks(off_ms)
        self.flash_counter = 0
        self.flash_lit = True
        # Start lit, so the first flash lands immediately rather than after a
        # blank interval.
        self.buffer = [color] * self.length
        self.shift_step = 0
        self.shift_variant = 0
        self.anim_mode = AnimMode.FLASH

    def _advance_flash(self) -> None:
        # flash_counter tracks frames already flushed in the current phase.
        # tick() runs this before _flush_buffer(), so the tick that flips the
        # phase also renders the new colour. Count it as that phase's first
        # frame, or every phase comes out one tick short.
        hold = self.flash_on_ticks if self.flash_lit else self.flash_off_ticks
        if self.flash_counter >= hold:
            self.flash_counter = 0
            self.flash_lit = not self.flash_lit
            fill = self.flash_color if self.flash_lit else self.flash_bg_color
            self.buffer = [fill] * self.length
        self.flash_counter += 1

    def flow(self, color1: int, color2: int, speed: int | Speed, invert: bool = False,
             seamless: bool = True) -> None:
        speed = as_speed(speed)
        self.composition = None  # a preset takes the base buffer over
        self.pulse_run_len = 0
        self.bitscroll_master = []
        self.buffer = gen_gradient(color1, color2, self.length, seamless)
        self.shift_step = 0
        self.shift_pacer.reset(self._step_ticks(speed))
        sp = speed.pixels % self.length
        self.shift_variant = ((self.length - sp) % self.length) if invert else sp
        self.anim_mode = AnimMode.SHIFT

    def rainbow(self, speed: int | Speed) -> None:
        speed = as_speed(speed)
        self.composition = None  # a preset takes the base buffer over
        self.pulse_run_len = 0
        self.bitscroll_master = []
        self.buffer = gen_rainbow(self.length)
        self.shift_step = 0
        self.shift_pacer.reset(self._step_ticks(speed))
        self.shift_variant = speed.pixels
        self.anim_mode = AnimMode.SHIFT

    def twinkle(self, colors: Sequence[int], density_pct: int = 30, fade_step: int = 16,
                bg_color: int = 0) -> None:
        self.composition = None  # a preset takes the base buffer over
        self.pulse_run_len = 0
        self.bitscroll_master = []
        self.twinkle_state.reset(self.length, colors, density_pct, fade_step, bg_color)
        # Every spark starts at level 0, so this is exactly the first paint. A
        # call from a ProfileMode callback is flushed before the next advance,
        # and would otherwise show the previous animation for a frame.
        self.buffer = [bg_color] * self.length
        self.anim_mode = AnimMode.TWINKLE

    def bitscroll(self, segments: Sequence[BitScrollSegment], speed: int | Speed,
                  invert: bool = False, bg_color: int = 0, bounce: bool = False,
                  spacing: int = 5, repeating: bool = True) -> None:
        speed = as_speed(speed)
        self.composition = None  # a preset takes the base buffer over
        self.pulse_run_len = 0
        self.shift_pacer.reset(self._step_ticks(speed))

        unit, content_len = _build_bitscroll_unit(segments, bg_color, spacing)

        if not bounce:
            self.bitscroll_master = []
            self.shift_step = 0
            self.buffer, self.shift_variant = _fill_bitscroll_buffer(
                unit, content_len, self.length, bg_color, speed.pixels, invert, repeating
            )
            self.anim_mode = AnimMode.SHIFT
        else:
            self.bitscroll_master = []
            if repeating:
                master_len = max(self.length * 3, len(unit) * 3)
                while len(self.bitscroll_master) < master_len:
                    self.bitscroll_master.extend(unit)
            else:
                # A single copy of the pattern, padded with background on both
                # sides so the visible window can carry it from the far end of
                # the strip to the near end and back.
                n = min(content_len, self.length)
                pad = self.length - n
                self.bitscroll_master = [bg_color] * pad + unit[:n] + [bg_color] * pad
            if len(self.buffer) != self.length:
                self.buffer = [bg_color] * self.length
            self.bounce_scroll_pos = 0
            self.bounce_scroll_dir = 1
            self.bounce_speed = max(speed.pixels, 1)
            self.anim_mode = AnimMode.STATIC
            self._fill_bitscroll_from_master()

    def _fill_bitscroll_from_master(self) -> None:
        max_start = max(0, len(self.bitscroll_master) - self.length)
        start = min(self.bounce_scroll_pos, max_start)
        self.buffer = self.bitscroll_master[start:start + self.length]

    def _advance_bitscroll_bounce(self) -> None:
        # bitscroll() already filled the window, so a tick that does not move
        # has nothing to repaint.
        if not self.shift_pacer.due():
            return
        max_pos = max(0, len(self.bitscroll_master) - self.length)
        self.bounce_scroll_pos += self.bounce_scroll_dir * self.bounce_speed
        if self.bounce_scroll_pos >= max_pos:
            self.bounce_scroll_pos = max_pos
            self.bounce_scroll_dir = -1
        if self.bounce_scroll_pos <= 0:
            self.bounce_scroll_pos = 0
            self.bounce_scroll_dir = 1
        self._fill_bitscroll_from_master()

    def _shift_buffer(self) -> None:
        buf_size = len(self.buffer)
        if buf_size == 0 or not self.shift_pacer.due():
            return
        self.shift_step = (self.shift_step + self.shift_variant) % buf_size

    # ========================================================================
    # Level meter / fill sources / music sync
    # ========================================================================

    def level_fill(self, color: int, color2: int = 0x000000, gradient: bool = False,
                   bg_color: int = 0x000000, invert: bool = False) -> None:
        self._level_fill(color, color2, gradient, bg_color, invert)
        # Manual control from here, see set_level(). Either of the automatic
        # drivers would overwrite it on the next tick.
        self.music_track = None
        self.level_read = None

    def _level_fill(self, color: int, color2: int, gradient: bool, bg_color: int,
                    invert: bool) -> None:
        self.composition = None  # a preset takes the base buffer over
        self.pulse_run_len = 0
        self.bitscroll_master = []
        # The meter's colors are laid across the whole strip once, here. All
        # the per-tick level does is decide how much of that is revealed.
        self.buffer = gen_gradient(color, color2, self.length) if gradient else [color] * self.length
        self.level_bg = bg_color
        self.level_invert = invert
        self.shift_step = 0
        self.shift_variant = 0
        self.anim_mode = AnimMode.LEVEL

    def set_level(self, level: int) -> None:
        self.level_value = max(0, min(level, 255))
        # A song or a live source would overwrite this on the next tick.
        self.music_track = None
        self.level_read = None

    def level_source(self, read: Optional[Callable[[], float]], empty_at: float, full_at: float,
                     wrap: bool = False, smoothing: int = 0) -> None:
        self.level_read = read
        self.level_empty_at = empty_at
        self.level_full_at = full_at
        self.level_wrap = wrap
        self.level_smooth = min(99, max(0, smoothing))  # 100 would never reach the target
        self.level_primed = False  # first sample lands outright, see _advance_level()
        self.music_track = None  # a song would overwrite the reader on the next tick

    def clear_level_source(self) -> None:
        self.level_read = None

    def level_source_active(self) -> bool:
        return self.level_read is not None

    def get_level(self) -> int:
        return self.level_value

    def music_sync(self, track: MusicTrack, color: int, color2: int = 0x000000,
                   gradient: bool = False, bg_color: int = 0x000000, invert: bool = False,
                   sensitivity: int = 100, loop: bool = False) -> None:
        self._level_fill(color, color2, gradient, bg_color, invert)
        self.music_track = track
        self.level_read = None  # the song drives the meter now
        self.music_sensitivity = sensitivity
        self.music_loop = loop
        self.music_paused = False
        self.music_anchor_ms = self.now_ms
        self.music_paused_at = 0
        self.level_value = self._sample_music(0)

    def music_seek(self, position_ms: int) -> None:
        # Move the anchor rather than a position counter, so a seek while
        # playing resumes from the new spot at real-time speed.
        self.music_anchor_ms = self.now_ms - position_ms
        self.music_paused_at = position_ms
        self.level_value = self._sample_music(position_ms)

    def music_pause(self, paused: bool = True) -> None:
        if paused and not self.music_paused:
            self.music_paused_at = self.now_ms - self.music_anchor_ms
            self.music_paused = True
        elif not paused and self.music_paused:
            self.music_anchor_ms = self.now_ms - self.music_paused_at
            self.music_paused = False

    def music_playing(self) -> bool:
        return self.music_track is not None and not self.music_paused

    def music_position_ms(self) -> int:
        return self.music_paused_at if self.music_paused else (self.now_ms - self.music_anchor_ms)

    def set_sensitivity(self, pct: int) -> None:
        self.music_sensitivity = pct

    def _advance_level(self) -> None:
        if self.music_track is not None:
            pos = self.music_paused_at if self.music_paused else (self.now_ms - self.music_anchor_ms)
            self.level_value = self._sample_music(pos)
            return
        if self.level_read is None:
            return

        raw = self.level_read()
        # An unplugged device reports PROS_ERR_F (infinity) on the robot.
        # Holding the fill says "no reading" better than slamming it to full.
        if not math.isfinite(raw):
            return

        target = self._map_level(raw)
        self.level_value = self._smooth_level(target) if self.level_primed else target
        self.level_primed = True

    @staticmethod
    def _map_level_to(raw: float, empty_at: float, full_at: float, wrap: bool) -> int:
        """Where a raw reading sits between empty_at and full_at, as 0-255. A
        range given backwards (full_at below empty_at) falls out of the same
        arithmetic as a meter that drains while the value climbs.

        Static, with the range passed in, so a gauge region can reuse it for
        its own - mirrors LedStrand::mapLevelTo().
        """
        span = full_at - empty_at
        if span == 0:
            return 0

        t = (raw - empty_at) / span
        if wrap:
            t -= math.floor(t)  # 1.25 -> 0.25, -0.25 -> 0.75
        else:
            t = max(0.0, min(t, 1.0))
        return max(0, min(int(t * 255.0 + 0.5), 255))

    @staticmethod
    def _smooth_level_to(target: int, current: int, smoothing: int, wrap: bool) -> int:
        """One tick of the smoothing filter: move part of the way to the target
        rather than snapping to it, so a jittery reading doesn't make the bar
        flicker. Mirrors LedStrand::smoothLevelTo()."""
        if smoothing == 0:
            return target

        delta = target - current
        if delta == 0:
            return target
        # A wrapping meter takes the short way round, otherwise a bar rolling
        # over from full to empty would sweep back down the whole strip.
        if wrap:
            if delta > 128:
                delta -= 256
            elif delta < -128:
                delta += 256

        step = trunc_div(delta * (100 - smoothing), 100)
        # Truncation would otherwise stall the bar a few pixels short of a
        # target it is already close to.
        if step == 0:
            step = 1 if delta > 0 else -1

        nxt = current + step
        if wrap:
            return nxt % 256
        return max(0, min(nxt, 255))

    def _map_level(self, raw: float) -> int:
        return self._map_level_to(raw, self.level_empty_at, self.level_full_at, self.level_wrap)

    def _smooth_level(self, target: int) -> int:
        return self._smooth_level_to(target, self.level_value, self.level_smooth, self.level_wrap)

    def _sample_music(self, position_ms: int) -> int:
        """Read this strand's envelope at an arbitrary millisecond offset.
        Mirrors LedStrand::sampleMusicNL(), which is the same thin wrapper
        over the shared track sampler."""
        track = self.music_track
        if track is None:
            return 0
        return sample_track(track.samples, track.frame_count, track.frame_ms,
                            position_ms, self.music_sensitivity, self.music_loop)

    def _level_pixel(self, i: int, full: int, frac: int) -> int:
        """The color one meter pixel shows, given this frame's fill cutoff:
        `full` whole lit pixels and `frac` into the next one. The partial pixel
        gives a 30-pixel strand more than 30 distinguishable levels."""
        # Index along the direction of the fill, so the gradient always begins
        # where the fill begins.
        f = (self.length - 1 - i) if self.level_invert else i
        if f < full:
            return self.buffer[f]
        if f == full:
            return lerp_color(self.level_bg, self.buffer[f], frac)
        return self.level_bg

    # ========================================================================
    # Splice mask
    # ========================================================================

    def splice_mask(self, sections: int, invert: bool = False, alternating: bool = False,
                     alt_period_ms: int = 100, bg_color: int = 0, use_overlay: bool = False) -> None:
        self.splice_mode = SpliceMode.SPLIT
        self.splice_regions = []
        self.splice_sections = sections
        self.splice_invert = invert
        self.splice_alternating = alternating
        self.splice_alt_ms = alt_period_ms if alt_period_ms > 0 else 1
        self.splice_bg_color = bg_color
        self.splice_use_overlay = use_overlay
        self.splice_active = sections != 0
        self.splice_last_toggle_ms = self.now_ms
        self._rebuild_splice_mask()

    def splice_mask_custom(self, regions: Sequence[SpliceRegion]) -> None:
        self.splice_mode = SpliceMode.CUSTOM
        self.splice_alternating = False
        self.splice_show_anim = [True] * self.length
        self.splice_pixel_region_idx = [-1] * self.length
        self.splice_regions = []

        for user_idx, r in enumerate(regions):
            if r.start >= self.length:
                continue
            end = min(r.start + r.width, self.length)
            region_width = end - r.start

            region_idx = -1
            fallback_color = 0x000000

            if r.kind == SpliceRegionAnimKind.SOLID:
                fallback_color = r.color
            elif r.kind != SpliceRegionAnimKind.OFF:
                state = _SpliceRegionState(start=r.start, buffer=[], user_idx=user_idx)
                if r.kind == SpliceRegionAnimKind.PULSE:
                    state.buffer = [r.bg_color] * region_width
                    rl = min(r.run_length, region_width)
                    for k in range(rl):
                        state.buffer[k] = r.color
                    state.shift_speed = as_speed(r.speed).pixels
                    state.shift_pacer.reset(self._step_ticks(as_speed(r.speed)))
                elif r.kind == SpliceRegionAnimKind.FLASH:
                    # Mirrors flash(), scaled to this region: the whole region
                    # blinks on a tick timer instead of shifting.
                    state.buffer = [r.color] * region_width
                    state.shift_speed = 0
                    state.flashing = True
                    state.flash_color = r.color
                    state.flash_bg_color = r.bg_color
                    state.flash_on_ticks = self._ms_to_ticks(r.on_ms)
                    state.flash_off_ticks = self._ms_to_ticks(r.off_ms)
                    state.flash_counter = 0
                    state.flash_lit = True
                elif r.kind == SpliceRegionAnimKind.FLOW:
                    state.buffer = gen_gradient(r.color, r.color2, region_width, r.seamless)
                    state.shift_speed = as_speed(r.speed).pixels
                    state.shift_pacer.reset(self._step_ticks(as_speed(r.speed)))
                elif r.kind == SpliceRegionAnimKind.RAINBOW:
                    state.buffer = gen_rainbow(region_width)
                    state.shift_speed = as_speed(r.speed).pixels
                    state.shift_pacer.reset(self._step_ticks(as_speed(r.speed)))
                elif r.kind == SpliceRegionAnimKind.TWINKLE:
                    # Repaints its own pixels every tick, like the strand-wide
                    # twinkle, so it never scrolls.
                    state.buffer = [r.bg_color] * region_width
                    state.shift_speed = 0
                    state.twinkling = True
                    state.twinkle.reset(region_width, r.palette, r.density_pct, r.fade_step,
                                        r.bg_color)
                elif r.kind == SpliceRegionAnimKind.BITSCROLL:
                    # No bounce: a region's buffer scrolls, and bouncing needs a
                    # wider master pattern to slide a window over.
                    unit, content_len = _build_bitscroll_unit(
                        [BitScrollSegment(color=r.color, width=r.segment_width)],
                        r.bg_color, r.spacing,
                    )
                    state.buffer, state.shift_speed = _fill_bitscroll_buffer(
                        unit, content_len, region_width, r.bg_color, as_speed(r.speed).pixels,
                        r.invert, r.repeating
                    )
                    state.shift_pacer.reset(self._step_ticks(as_speed(r.speed)))
                elif r.kind == SpliceRegionAnimKind.GAUGE:
                    state.buffer = [r.bg_color] * region_width
                    state.shift_speed = 0  # a gauge repaints, it never scrolls
                    state.gauge = True
                    state.level_read = r.read
                    state.level_empty_at = r.empty_at
                    state.level_full_at = r.full_at
                    state.level_wrap = r.wrap
                    state.level_smooth = min(99, max(0, r.smoothing))  # 100 never arrives
                    state.level_primed = False
                    state.gauge_invert = r.invert
                    state.gauge_style = r.style
                    state.gauge_blend = r.blend
                    state.gauge_bg = r.bg_color
                    # Resolve the scale onto the same 0-255 axis level_value
                    # lives on, once, so a tick only has to bracket a byte. An
                    # empty scale becomes the two-color fallback, so there is always
                    # guarantees there is always something to bracket between.
                    scale = r.stops or (GaugeStop(r.empty_at, r.color), GaugeStop(r.full_at, r.color2))
                    # By level only, and stable: two stops at one reading are
                    # a hard edge, and which side each color is on is the
                    # order they were given in, not which is numerically larger.
                    state.gauge_stops = sorted(
                        # wrap is False here whatever the gauge itself does: a
                        # stop's place on the scale is a clamp, not a lap.
                        ((self._map_level_to(gs.at, r.empty_at, r.full_at, False), gs.color)
                         for gs in scale),
                        key=lambda stop: stop[0],
                    )
                self.splice_regions.append(state)
                region_idx = len(self.splice_regions) - 1
                # A gauge shows the bottom of its scale rather than its
                # background on the very first frame, instead of waiting for
                # the first poll.
                if state.gauge:
                    self._paint_gauge_region(state)

            for i in range(r.start, end):
                self.splice_show_anim[i] = False
                self.splice_pixel_bg[i] = fallback_color
                self.splice_pixel_use_overlay[i] = False
                self.splice_pixel_region_idx[i] = region_idx

        self.splice_active = len(regions) > 0

    def clear_splice_mask(self) -> None:
        self.splice_active = False

    def _rebuild_splice_mask(self) -> None:
        bin_count = self.splice_sections + 1
        base = self.length // bin_count
        rem = self.length % bin_count
        idx = 0
        for b in range(bin_count):
            if idx >= self.length:
                break
            bin_size = base + (1 if b < rem else 0)
            show_anim = ((b % 2) == 1) != self.splice_invert
            for _ in range(bin_size):
                if idx >= self.length:
                    break
                self.splice_show_anim[idx] = show_anim
                self.splice_pixel_bg[idx] = self.splice_bg_color
                self.splice_pixel_use_overlay[idx] = self.splice_use_overlay
                self.splice_pixel_region_idx[idx] = -1
                idx += 1

    def _advance_splice_alternating(self, now_ms: int) -> None:
        if not self.splice_active or self.splice_mode != SpliceMode.SPLIT or not self.splice_alternating:
            return
        if now_ms - self.splice_last_toggle_ms >= self.splice_alt_ms:
            self.splice_invert = not self.splice_invert
            self.splice_last_toggle_ms = now_ms
            self._rebuild_splice_mask()

    def _advance_splice_regions(self) -> None:
        if not self.splice_active or self.splice_mode != SpliceMode.CUSTOM:
            return
        for state in self.splice_regions:
            if state.gauge:
                if state.level_read is not None:
                    raw = state.level_read()
                    # An unplugged device reports PROS_ERR_F (infinity) on the
                    # robot. Holding the color says "no reading" better than
                    # slamming the segment to the top of its scale would.
                    if math.isfinite(raw):
                        target = self._map_level_to(
                            raw, state.level_empty_at, state.level_full_at, state.level_wrap
                        )
                        state.level_value = (
                            self._smooth_level_to(
                                target, state.level_value, state.level_smooth, state.level_wrap
                            )
                            if state.level_primed
                            else target
                        )
                        state.level_primed = True
                self._paint_gauge_region(state)
                continue

            buf_size = len(state.buffer)
            if buf_size == 0:
                continue
            if state.flashing:
                hold = state.flash_on_ticks if state.flash_lit else state.flash_off_ticks
                if state.flash_counter >= hold:
                    state.flash_counter = 0
                    state.flash_lit = not state.flash_lit
                    fill = state.flash_color if state.flash_lit else state.flash_bg_color
                    state.buffer = [fill] * buf_size
                state.flash_counter += 1
                continue
            if state.twinkling:
                _advance_twinkle_into(state.twinkle, state.buffer, self._rng)
                continue
            if state.shift_speed == 0 or not state.shift_pacer.due():
                continue
            state.shift_step = (state.shift_step + state.shift_speed) % buf_size

    @staticmethod
    def _gauge_color_at(state: _SpliceRegionState, level: int) -> int:
        """The color a gauge's scale gives for a 0-255 level. Mirrors
        LedStrand::gaugeColorAt(), which is the same thin wrapper over the
        shared scale lookup."""
        return scale_color_at(state.gauge_stops, state.gauge_blend == GaugeBlend.STEP,
                              state.gauge_bg, level)

    def _paint_gauge_region(self, state: _SpliceRegionState) -> None:
        """Repaint one gauge region from its current level. Called every tick
        rather than only on a change: it is at most 64 writes, and it keeps the
        region correct after a set_region_level() with no dirty flag to get
        wrong. Mirrors LedStrand::paintGaugeRegion()."""
        width = len(state.buffer)
        if width == 0:
            return

        if state.gauge_style == GaugeStyle.HEAT:
            state.buffer = [self._gauge_color_at(state, state.level_value)] * width
            return

        # BAR: the strand-wide meter's cutoff arithmetic scoped to this
        # region's own pixels - whole lit pixels plus a fraction of the next
        # one, so a 10-pixel segment still shows more than 10 levels.
        lit_q8 = state.level_value * width
        full, frac = divmod(lit_q8, 255)
        for k in range(width):
            # Index along the direction of the fill, so the scale always begins
            # where the fill begins - as _level_pixel() does for the whole strip.
            f = (width - 1 - k) if state.gauge_invert else k
            pos = 255 if width <= 1 else trunc_div(f * 255, width - 1)
            scale = self._gauge_color_at(state, pos)
            if f < full:
                state.buffer[k] = scale
            elif f == full:
                state.buffer[k] = lerp_color(state.gauge_bg, scale, frac)
            else:
                state.buffer[k] = state.gauge_bg

    def set_region_level(self, region_idx: int, level: int) -> None:
        """Set how full a hand-driven GAUGE region is - set_level()'s
        counterpart for one region. `region_idx` indexes the sequence last
        passed to splice_mask_custom()."""
        for state in self.splice_regions:
            # A gauge with a reader of its own would overwrite this on the next
            # tick, so setting it would only ever look like a glitch.
            if not state.gauge or state.user_idx != region_idx or state.level_read is not None:
                continue
            state.level_value = max(0, min(level, 255))
            state.level_primed = True
            self._paint_gauge_region(state)
            break

    @staticmethod
    def motor_heat_gauge(start: int, width: int,
                         read: Optional[Callable[[], float]] = None) -> SpliceRegion:
        """A GAUGE region pre-loaded with the V5 motor's heat schedule - port of
        LedStrand::motorHeatGauge(). See MOTOR_HEAT_STOPS."""
        return SpliceRegion(
            start=start,
            width=width,
            kind=SpliceRegionAnimKind.GAUGE,
            read=read,
            empty_at=20.0,
            full_at=70.0,
            # The V5 reports motor temperature in coarse steps rather than as a
            # continuous reading, so without smoothing a segment jumps from one
            # stop color straight to the next.
            smoothing=80,
            stops=MOTOR_HEAT_STOPS,
        )

    # ========================================================================
    # Overlay animations
    # ========================================================================

    def overlay_set_color(self, color: int) -> None:
        self.overlay_buffer = [color] * self.length
        self.overlay_anim_mode = AnimMode.STATIC
        self.overlay_shift_step = 0
        self.overlay_shift_speed = 0

    def overlay_pulse(self, color: int, run_length: int, speed: int | Speed,
                      bg_color: int = 0) -> None:
        speed = as_speed(speed)
        self.overlay_buffer = [bg_color] * self.length
        rl = min(run_length, self.length)
        for i in range(rl):
            self.overlay_buffer[i] = color
        self.overlay_shift_step = 0
        self.overlay_shift_speed = speed.pixels
        self.overlay_shift_pacer.reset(self._step_ticks(speed))
        self.overlay_anim_mode = AnimMode.SHIFT

    def overlay_flash(self, color: int, on_ms: int, off_ms: int, bg_color: int = 0) -> None:
        # Mirrors flash(): tick-timed blink with independent on/off durations.
        self.overlay_flash_color = color
        self.overlay_flash_bg_color = bg_color
        self.overlay_flash_on_ticks = self._ms_to_ticks(on_ms)
        self.overlay_flash_off_ticks = self._ms_to_ticks(off_ms)
        self.overlay_flash_counter = 0
        self.overlay_flash_lit = True
        self.overlay_buffer = [color] * self.length
        self.overlay_shift_step = 0
        self.overlay_shift_speed = 0
        self.overlay_anim_mode = AnimMode.FLASH

    def _advance_overlay_flash(self) -> None:
        hold = (self.overlay_flash_on_ticks if self.overlay_flash_lit
                else self.overlay_flash_off_ticks)
        if self.overlay_flash_counter >= hold:
            self.overlay_flash_counter = 0
            self.overlay_flash_lit = not self.overlay_flash_lit
            fill = (self.overlay_flash_color if self.overlay_flash_lit
                    else self.overlay_flash_bg_color)
            self.overlay_buffer = [fill] * self.length
        self.overlay_flash_counter += 1

    def overlay_flow(self, color1: int, color2: int, speed: int | Speed,
                     seamless: bool = True) -> None:
        speed = as_speed(speed)
        self.overlay_buffer = gen_gradient(color1, color2, self.length, seamless)
        self.overlay_shift_step = 0
        self.overlay_shift_speed = speed.pixels
        self.overlay_shift_pacer.reset(self._step_ticks(speed))
        self.overlay_anim_mode = AnimMode.SHIFT

    def overlay_rainbow(self, speed: int | Speed) -> None:
        speed = as_speed(speed)
        self.overlay_buffer = gen_rainbow(self.length)
        self.overlay_shift_step = 0
        self.overlay_shift_speed = speed.pixels
        self.overlay_shift_pacer.reset(self._step_ticks(speed))
        self.overlay_anim_mode = AnimMode.SHIFT

    def overlay_twinkle(self, colors: Sequence[int], density_pct: int = 30, fade_step: int = 16,
                        bg_color: int = 0) -> None:
        self.overlay_twinkle_state.reset(self.length, colors, density_pct, fade_step, bg_color)
        self.overlay_buffer = [bg_color] * self.length
        self.overlay_shift_step = 0
        self.overlay_shift_speed = 0
        self.overlay_anim_mode = AnimMode.TWINKLE

    def overlay_bitscroll(self, segments: Sequence[BitScrollSegment], speed: int | Speed,
                          invert: bool = False, bg_color: int = 0, spacing: int = 5,
                          repeating: bool = True) -> None:
        # No bounce: the overlay is a single scrolling buffer, and bouncing
        # needs a wider master pattern to slide a window over.
        speed = as_speed(speed)
        unit, content_len = _build_bitscroll_unit(segments, bg_color, spacing)
        self.overlay_shift_step = 0
        self.overlay_buffer, self.overlay_shift_speed = _fill_bitscroll_buffer(
            unit, content_len, self.length, bg_color, speed.pixels, invert, repeating
        )
        self.overlay_shift_pacer.reset(self._step_ticks(speed))
        self.overlay_anim_mode = AnimMode.SHIFT

    def _shift_overlay_buffer(self) -> None:
        buf_size = len(self.overlay_buffer)
        if buf_size == 0 or not self.overlay_shift_pacer.due():
            return
        self.overlay_shift_step = (self.overlay_shift_step + self.overlay_shift_speed) % buf_size

    # ========================================================================
    # Brightness
    # ========================================================================

    def set_brightness(self, pct: int) -> None:
        self.brightness_pct = max(0, min(pct, 100))

    def get_brightness(self) -> int:
        return self.brightness_pct

    def _ms_to_ticks(self, ms: int) -> int:
        # Round a wall-clock duration to whole refresh ticks. Animations can
        # only change state on a tick, so anything shorter than one interval
        # is clamped up to a single tick rather than silently becoming zero
        # (which would spin the phase every frame).
        if self.refresh_ms <= 0:
            return 1
        # The firmware keeps tick counts in a uint16_t and clamps to fit.
        return min(0xFFFF, max(1, (ms + self.refresh_ms // 2) // self.refresh_ms))

    def _step_ticks(self, speed: Speed) -> int:
        """Refresh ticks between a Speed's steps: 1 for a bare number.
        Mirrors LedStrand::stepTicks()."""
        return self._ms_to_ticks(speed.step_ms) if speed.step_ms else 1

    def _apply_brightness(self, color: int) -> int:
        r = ((color >> 16) & 0xFF) * self.brightness_pct // 100
        g = ((color >> 8) & 0xFF) * self.brightness_pct // 100
        b = (color & 0xFF) * self.brightness_pct // 100
        return (r << 16) | (g << 8) | b

    # ========================================================================
    # Profile system
    # ========================================================================

    def attach_profile(self, profile: Optional[Profile]) -> None:
        self.active_profile = profile
        self.mode_stack = []
        self.last_mode_idx = -1

    def detach_profile(self) -> None:
        self.active_profile = None
        self.mode_stack = []
        self.last_mode_idx = -1
        self.set_color(0)

    def activate_mode(self, mode_idx: int) -> None:
        for e in self.mode_stack:
            if e.mode_idx == mode_idx:
                e.persistent = True
                return
        self.mode_stack.append(_ModeEntry(mode_idx, True, 0))

    def activate_mode_timed(self, mode_idx: int, duration_ms: int) -> None:
        now = self.now_ms
        for e in self.mode_stack:
            if e.mode_idx == mode_idx:
                if not e.persistent:
                    e.end_ms = now + duration_ms
                return
        self.mode_stack.append(_ModeEntry(mode_idx, False, now + duration_ms))

    def deactivate_mode(self, mode_idx: int) -> None:
        self.mode_stack = [e for e in self.mode_stack if e.mode_idx != mode_idx]

    def _prune_expired(self, now: int) -> None:
        if not self.mode_stack:
            return
        self.mode_stack = [e for e in self.mode_stack if e.persistent or now < e.end_ms]

    def _compute_effective_mode(self) -> int:
        if not self.active_profile or not self.mode_stack:
            return -1
        winner = -1
        winner_priority = -1
        for e in self.mode_stack:
            if e.mode_idx >= len(self.active_profile.modes):
                continue
            p = self.active_profile.modes[e.mode_idx].priority
            if p >= winner_priority:
                winner_priority = p
                winner = e.mode_idx
        return winner

    # ========================================================================
    # Compositing / flush
    # ========================================================================

    def render(self) -> None:
        """Recomposite the current frame without advancing time.

        No firmware counterpart - it exists so Pattern Studio can scrub a
        paused strand and still see the frame that position lands on.
        """
        self._flush_buffer()

    def _flush_buffer(self) -> None:
        # Meter fill cutoff, in whole pixels plus a fraction of the next one.
        # Constant across the frame, so it is worked out here rather than
        # inside the per-pixel loop.
        level_full, level_frac = divmod(self.level_value * self.length, 255)
        buf_size = len(self.buffer)
        overlay_buf_size = len(self.overlay_buffer)

        pixels = []
        for i in range(self.length):
            if self.anim_mode == AnimMode.LEVEL:
                base_color = self._level_pixel(i, level_full, level_frac)
            elif self.anim_mode == AnimMode.SHIFT and buf_size > 0:
                base_color = self.buffer[(i + self.shift_step) % buf_size]
            else:
                base_color = self.buffer[i]

            # Mirrors base_color above. Without this, overlay_shift_step
            # (advanced every tick by _shift_overlay_buffer()) is computed but
            # never actually read, so overlay animations render as a single
            # frozen frame instead of animating.
            if self.overlay_anim_mode == AnimMode.SHIFT and overlay_buf_size > 0:
                overlay_color = self.overlay_buffer[(i + self.overlay_shift_step) % overlay_buf_size]
            else:
                overlay_color = self.overlay_buffer[i]

            color = base_color

            if self.splice_active and not self.splice_show_anim[i]:
                region_idx = self.splice_pixel_region_idx[i]
                if region_idx >= 0:
                    state = self.splice_regions[region_idx]
                    region_buf_size = len(state.buffer)
                    if region_buf_size > 0:
                        local_offset = i - state.start
                        color = state.buffer[(local_offset + state.shift_step) % region_buf_size]
                    else:
                        color = self.splice_pixel_bg[i]
                else:
                    color = overlay_color if self.splice_pixel_use_overlay[i] else self.splice_pixel_bg[i]

            pixels.append(self._apply_brightness(color))
        self.pixels = pixels

    # ========================================================================
    # Custom animations (Compositions) - mirror of src/led_custom.cpp
    #
    # Two rules, the same two the firmware keeps:
    #   1. A generator is never written twice. Each case below calls the same
    #      body the matching preset calls, so "a custom layer reproduces its
    #      preset exactly" is structural rather than a promise a test chases.
    #   2. The arithmetic is C-like throughout (trunc_div, explicit masks),
    #      because tests/test_custom_golden.py diffs this against a host build
    #      of the real C++ byte for byte.
    # ========================================================================

    def compose(self, comp) -> None:
        """Play a Composition as this strand's base animation.

        Mirrors LedStrand::compose(). Re-installing what is already playing is
        a no-op, so a Sequencer phase that re-issues it every 200 ms does not
        restart the animation.
        """
        if self.composition is comp:
            return

        self.composition = comp
        self.compose_generation += 1
        self.compose_anchor_ms = self.now_ms
        self.compose_music_paused_at = 0
        self.compose_music_paused = False

        # The composition's own drivers take these over.
        self.level_read = None
        self.music_track = None
        # And it takes the base buffer over from whatever preset had it.
        self.pulse_run_len = 0
        self.bitscroll_master = []
        self.anim_mode = AnimMode.STATIC
        self.shift_step = 0
        self.shift_variant = 0

        n = min(len(comp.layers), _comp.MAX_LAYERS)
        self.layer_states = [_comp.LayerState() for _ in range(n)]
        self.driver_value = [0] * _comp.MAX_DRIVERS
        self.driver_primed = [False] * _comp.MAX_DRIVERS
        for d, drv in enumerate(comp.drivers[:_comp.MAX_DRIVERS]):
            if drv.kind == _comp.DriverKind.CONSTANT:
                self.driver_value[d] = drv.value

        for li in range(n):
            layer = comp.layers[li]
            st = self.layer_states[li]
            self._prepare_layer(layer, st)
            self._build_layer(layer, st)
            self._reset_layer_state(layer, st, self._motion_ring(layer, self._nominal_span(layer)))

    def clear_composition(self) -> None:
        self.composition = None
        self.compose_generation += 1
        self.layer_states = []
        self.buffer = [0] * self.length
        self.anim_mode = AnimMode.STATIC
        self.shift_step = 0
        self.shift_variant = 0

    def composing(self) -> bool:
        return self.composition is not None

    def set_driver_level(self, driver_idx: int, value: int) -> None:
        c = self.composition
        if (c is not None and driver_idx < len(c.drivers) and driver_idx < _comp.MAX_DRIVERS
                and c.drivers[driver_idx].kind == _comp.DriverKind.CONSTANT):
            self.driver_value[driver_idx] = value & 0xFF

    def get_driver_level(self, driver_idx: int) -> int:
        return self.driver_value[driver_idx] if driver_idx < _comp.MAX_DRIVERS else 0

    def restart_composition(self) -> None:
        c = self.composition
        if c is None:
            return
        self.compose_anchor_ms = self.now_ms
        self.compose_music_paused_at = 0
        self.compose_music_paused = False
        for li, st in enumerate(self.layer_states):
            layer = c.layers[li]
            self._reset_layer_state(layer, st, self._motion_ring(layer, self._nominal_span(layer)))

    def compose_music_seek(self, position_ms: int) -> None:
        if self.compose_music_paused:
            self.compose_music_paused_at = position_ms
        else:
            self.compose_anchor_ms = self.now_ms - position_ms

    def compose_music_pause(self, paused: bool = True) -> None:
        if paused and not self.compose_music_paused:
            self.compose_music_paused_at = self.now_ms - self.compose_anchor_ms
            self.compose_music_paused = True
        elif not paused and self.compose_music_paused:
            self.compose_anchor_ms = self.now_ms - self.compose_music_paused_at
            self.compose_music_paused = False

    def compose_music_position_ms(self) -> int:
        return self._compose_clock(self.now_ms)

    def _compose_clock(self, now_ms: int) -> int:
        """Where the composition's timeline is at `now_ms`. Mirrors
        composeClockNL().

        MUSIC drivers and layer windows both read this one clock, so pausing
        the music holds the windows too: left running, they would come back
        out of step with the song, and resuming - which moves the anchor back
        by the pause - would replay them.
        """
        if self.compose_music_paused:
            return self.compose_music_paused_at
        elapsed = (now_ms - self.compose_anchor_ms) & 0xFFFFFFFF
        # A restart or a seek from inside a SENSOR reader can move the anchor
        # past the now_ms this tick already read. That is "just started", not
        # 49 days in - the firmware reads it as a signed difference.
        return 0 if elapsed >= 0x80000000 else elapsed

    # ---- one-time setup, in compose() -------------------------------------

    def _nominal_span(self, layer) -> int:
        """The width a layer was designed at, before any binding moves it:
        its span clamped to the strand, never 0. Mirrors nominalSpan()."""
        start = min(layer.start, self.length)
        span = layer.width if layer.width else (self.length - start)
        if start + span > self.length:
            span = self.length - start
        return max(1, span)

    def _motion_ring(self, layer, span: int) -> int:
        """The ring a layer's motion runs on: what it scrolls round, bounces
        across and sparkles over. It has to be the ring _layer_pixel()
        samples, which for a continuous stride is the whole strand, so the
        windows show one scroll between them. Mirrors motionRing()."""
        return self.length if layer.continuous else span

    def _prepare_layer(self, layer, st) -> None:
        """Measure this layer's scratch. Mirrors prepareLayerNL().

        Only a bouncing bitscroll's master is span dependent, so only it pins
        `max_span`; everything else can be widened by a bound WIDTH.
        """
        st.scratch = []
        st.content_len = 0
        st.max_span = 0
        # A continuous stride's bounce master spans the whole strand.
        nominal = self._motion_ring(layer, self._nominal_span(layer))

        if layer.kind == _comp.LayerKind.BITSCROLL:
            unit, content_len = _build_bitscroll_unit(layer.segments, layer.bg_color,
                                                      layer.spacing)
            st.content_len = content_len
            if layer.bounce:
                if layer.repeating:
                    master_len = max(nominal * 3, len(unit) * 3)
                    # bitscrollNL() tiles with `while (size < masterLen)`, so
                    # the master overshoots to the next whole copy - and the
                    # bounce range is masterLen - span, so this rounding is
                    # load-bearing.
                    if unit:
                        master_len = -(-master_len // len(unit)) * len(unit)
                else:
                    nn = min(content_len, nominal)
                    master_len = (nominal - nn) * 2 + nn
                st.scratch = [0] * master_len
                st.max_span = nominal
            else:
                st.scratch = [0] * len(unit)
        elif layer.kind == _comp.LayerKind.FRAMES:
            st.scratch = [0] * max(1, layer.frame_width)

    def _build_layer(self, layer, st) -> None:
        """Build what a layer draws from and never changes: its bitscroll
        pattern or bounce master, its twinkle palette, its gauge scale. Only
        compose() calls this. Mirrors buildLayerNL()."""
        if layer.kind == _comp.LayerKind.TWINKLE:
            st.twinkle = _TwinkleState()
            st.twinkle.palette = list(layer.palette)

        if layer.kind == _comp.LayerKind.BITSCROLL and st.scratch:
            coverage: list[int] = []
            unit, content_len = _build_bitscroll_unit(layer.segments, layer.bg_color,
                                                      layer.spacing, coverage)
            # A bounce pins the width its master was measured at in max_span.
            designed = st.max_span if st.max_span else 1
            if not layer.bounce:
                for i in range(min(len(unit), len(st.scratch))):
                    st.scratch[i] = (coverage[i] << 24) | (unit[i] & 0xFFFFFF)
            elif layer.repeating:
                for i in range(len(st.scratch)):
                    j = i % len(unit)
                    st.scratch[i] = (coverage[j] << 24) | (unit[j] & 0xFFFFFF)
            else:
                nn = min(content_len, designed)
                pad = designed - nn
                for i in range(len(st.scratch)):
                    if pad <= i < pad + nn:
                        j = i - pad
                        st.scratch[i] = (coverage[j] << 24) | (unit[j] & 0xFFFFFF)
                    else:
                        st.scratch[i] = layer.bg_color & 0xFFFFFF

        # Resolve a GAUGE/METER scale onto the 0-255 axis once, the same
        # representation splice_mask_custom() builds for a gauge region -
        # including its fallback, so a GAUGE given no scale ramps color at
        # empty_at to color2 at full_at.
        st.stops = []
        if layer.kind in (_comp.LayerKind.GAUGE, _comp.LayerKind.METER):
            scale = layer.stops
            if not scale and layer.kind == _comp.LayerKind.GAUGE:
                scale = (GaugeStop(layer.empty_at, layer.color), GaugeStop(layer.full_at, layer.color2))
            span2 = layer.full_at - layer.empty_at
            for gs in scale:
                frac = 0.0 if span2 == 0 else (gs.at - layer.empty_at) / span2
                frac = max(0.0, min(1.0, frac))
                st.stops.append((int(frac * 255.0 + 0.5), gs.color))
            # By level only, and stable (list.sort always is), like the firmware.
            st.stops.sort(key=lambda pair: pair[0])

    def _reset_layer_state(self, layer, st, ring: int) -> None:
        """Put a layer's motion back to its first frame; what _build_layer()
        built stays. `ring` is the ring the motion runs on, see _motion_ring().
        Mirrors resetLayerStateNL()."""
        st.shift_acc = 0
        st.flash_counter = 0
        st.flash_lit = True
        st.frame_idx = 0
        st.frame_counter = 0
        st.step.reset(self._step_ticks(as_speed(layer.speed)))

        if layer.kind == _comp.LayerKind.PULSE and layer.bounce:
            # pulseNL()'s bounce start: the far end and backwards when inverted.
            run = max(1, min(layer.run_length, ring))
            st.bounce_pos = (ring - run) if layer.invert else 0
            st.bounce_dir = -1 if layer.invert else 1
        else:
            # bitscrollNL()'s bounce branch starts at 0 going forward and
            # ignores `invert` entirely - the opposite of pulse. Reproduced,
            # not tidied.
            st.bounce_pos = 0
            st.bounce_dir = 1

        if layer.kind == _comp.LayerKind.TWINKLE:
            # Every spark back to dark, on the palette _build_layer() set.
            st.twinkle.reset(self.length, st.twinkle.palette, layer.density_pct,
                             layer.fade_step, layer.bg_color)

        if layer.kind == _comp.LayerKind.FRAMES:
            self._decode_frame(layer, st)

    def _decode_frame(self, layer, st) -> None:
        """Decode frame `st.frame_idx` into this layer's scratch.

        One byte per run: the top three bits are the run length minus one, the
        bottom five a palette index. Index 0 is transparent whatever color it
        names. A run is clamped to the width left, so a hand-edited table
        cannot overrun.
        """
        width = max(1, layer.frame_width)
        st.scratch = [0] * len(st.scratch)
        if not layer.frame_data or not layer.frame_index or layer.frame_count == 0:
            return

        frame = st.frame_idx % layer.frame_count
        at = layer.frame_index[frame]
        x = 0
        while x < width and x < len(st.scratch):
            byte = layer.frame_data[at]
            at += 1
            run = (byte >> 5) + 1
            idx = byte & 0x1F
            if run > width - x:
                run = width - x
            word = 0
            if idx != 0 and layer.frame_palette and idx < len(layer.frame_palette):
                word = 0xFF000000 | (layer.frame_palette[idx] & 0xFFFFFF)
            for k in range(run):
                st.scratch[x + k] = word
            x += run

    # ---- per-tick evaluation ----------------------------------------------

    def _eval_drivers(self, c, now_ms: int) -> bool:
        """Mirrors evalDriversNL(). False when a reader replaced the
        composition, which the caller has to unwind from.

        No mutex dance here, but the same shape: a SENSOR reader is user code
        and may call back into the strand, so the readers run first, as one
        batch, and a composition swapped or dropped under them is caught once.
        A preset drops it by setting `composition` to None, which the identity
        check sees; compose() and clear_composition() bump the generation.
        """
        drivers = c.drivers[:_comp.MAX_DRIVERS]
        gen = self.compose_generation
        raw = [drv.read() if drv.kind == _comp.DriverKind.SENSOR and drv.read is not None else None
               for drv in drivers]
        if self.composition is not c or self.compose_generation != gen:
            return False

        for d, drv in enumerate(drivers):
            if drv.kind == _comp.DriverKind.CONSTANT:
                continue
            if drv.kind == _comp.DriverKind.SENSOR:
                # An unplugged device reports infinity on the robot. Holding
                # the last value avoids slamming the layer to full scale.
                if raw[d] is None or not math.isfinite(raw[d]):
                    continue
                target = self._map_level_to(raw[d], drv.empty_at, drv.full_at, drv.wrap)
                if self.driver_primed[d]:
                    self.driver_value[d] = self._smooth_level_to(
                        target, self.driver_value[d], drv.smoothing, drv.wrap)
                else:
                    self.driver_value[d] = target  # the first sample lands outright
                self.driver_primed[d] = True
            elif drv.kind == _comp.DriverKind.MUSIC:
                if drv.track is None:
                    continue
                self.driver_value[d] = sample_track(
                    drv.track.samples, drv.track.frame_count, drv.track.frame_ms,
                    self._compose_clock(now_ms), drv.sensitivity, drv.loop)
            elif drv.kind == _comp.DriverKind.LFO:
                self.driver_value[d] = _lfo_value(drv, now_ms)
        return True

    def _resolve_layer(self, c, layer):
        p = _comp.Resolved(
            start=layer.start, width=layer.width, run_length=layer.run_length,
            speed=as_speed(layer.speed).pixels, density_pct=layer.density_pct,
            opacity=layer.opacity,
            level=layer.level, mix=0,
        )
        for bd in layer.bindings[:_comp.MAX_BINDINGS]:
            if bd.param == _comp.LayerParam.NONE or bd.driver >= len(c.drivers):
                continue
            if bd.driver >= _comp.MAX_DRIVERS:
                continue
            v = _apply_curve(bd.curve, self.driver_value[bd.driver])
            x = bd.at_zero + trunc_div((bd.at_full - bd.at_zero) * v, 255)
            u = max(0, min(255, x))
            if bd.param == _comp.LayerParam.OPACITY:
                p.opacity = u
            elif bd.param == _comp.LayerParam.SPEED:
                p.speed = u
            elif bd.param == _comp.LayerParam.RUN_LENGTH:
                p.run_length = u
            elif bd.param == _comp.LayerParam.LEVEL:
                p.level = u
            elif bd.param == _comp.LayerParam.START:
                p.start = u
            elif bd.param == _comp.LayerParam.WIDTH:
                p.width = u
            elif bd.param == _comp.LayerParam.DENSITY:
                p.density_pct = u
            elif bd.param == _comp.LayerParam.MIX:
                p.mix = u
        return p

    def _resolve_span(self, p, st) -> int:
        if p.start >= self.length:
            return 0
        span = p.width if p.width else (self.length - p.start)
        if p.start + span > self.length:
            span = self.length - p.start
        if st.max_span and span > st.max_span:
            span = st.max_span
        return span

    def _advance_layer(self, layer, st, p, ring: int) -> None:
        # `ring` is the ring the motion runs on, see _motion_ring().
        #
        # The moving kinds move only on a tick their Speed says is a step,
        # which is every tick for a bare pixel count - so a layer reproduces
        # its preset exactly either way. Mirrors advanceLayerNL().
        kind = layer.kind
        if kind in (_comp.LayerKind.PULSE, _comp.LayerKind.FLOW, _comp.LayerKind.RAINBOW,
                    _comp.LayerKind.BITSCROLL) and not st.step.due():
            return
        if kind == _comp.LayerKind.PULSE:
            if layer.bounce:
                # _advance_pulse_bounce(), with ring for length.
                st.bounce_pos += st.bounce_dir * max(1, p.speed)
                max_offset = max(0, ring - max(1, p.run_length))
                if st.bounce_pos >= max_offset:
                    st.bounce_pos, st.bounce_dir = max_offset, -1
                if st.bounce_pos <= 0:
                    st.bounce_pos, st.bounce_dir = 0, 1
            else:
                st.shift_acc = (st.shift_acc + _shift_for(p.speed, ring, layer.invert)) % ring
        elif kind in (_comp.LayerKind.FLOW, _comp.LayerKind.RAINBOW):
            st.shift_acc = (st.shift_acc + _shift_for(p.speed, ring, layer.invert)) % ring
        elif kind == _comp.LayerKind.BITSCROLL:
            if layer.bounce:
                max_pos = max(0, len(st.scratch) - ring)
                st.bounce_pos += st.bounce_dir * max(1, p.speed)
                if st.bounce_pos >= max_pos:
                    st.bounce_pos, st.bounce_dir = max_pos, -1
                if st.bounce_pos <= 0:
                    st.bounce_pos, st.bounce_dir = 0, 1
            else:
                wrap_at = (len(st.scratch) or 1) if layer.repeating else ring
                st.shift_acc = (st.shift_acc + _shift_for(p.speed, wrap_at, layer.invert)) % wrap_at
        elif kind == _comp.LayerKind.FLASH:
            # _advance_flash(): compare then increment, so the tick that flips
            # the phase is the one that renders it.
            hold = self._ms_to_ticks(layer.on_ms if st.flash_lit else layer.off_ms)
            if st.flash_counter >= hold:
                st.flash_counter = 0
                st.flash_lit = not st.flash_lit
            st.flash_counter += 1
        elif kind == _comp.LayerKind.TWINKLE:
            st.twinkle.density_pct = min(100, p.density_pct)
            _step_twinkle(st.twinkle, ring, self._rng)
        elif kind == _comp.LayerKind.FRAMES:
            # _advance_flash()'s order: compare, then count, so the first
            # frame holds as long as every other one. Mirrors advanceLayerNL().
            hold = self._ms_to_ticks(layer.frame_ms) or 1
            if st.frame_counter >= hold:
                st.frame_counter = 0
                if layer.frame_count:
                    st.frame_idx = (st.frame_idx + 1) % layer.frame_count
                    self._decode_frame(layer, st)
            st.frame_counter += 1
            # A frame that holds shows the drawing where it was drawn, and a
            # scrolling frame after it starts from there rather than from
            # wherever the last scroll left off. A run of scrolling frames is
            # one scroll.
            scroll = layer.frame_scroll
            if not (scroll and layer.frame_count and scroll[st.frame_idx % layer.frame_count]):
                st.shift_acc = 0
                st.step.restart()
                return
            frame_ring = max(1, layer.frame_width)
            if p.speed and st.step.due():
                st.shift_acc = (st.shift_acc + _shift_for(p.speed, frame_ring, layer.invert)) % frame_ring

    def _refresh_ramp(self, layer, st, p, span: int, fg: int) -> None:
        """Work out once, rather than for every pixel of every tick, the colors
        a layer's pixels only index into: a FLOW's gradient, a RAINBOW's
        wheel, a gradient METER's ramp, a GAUGE's scale.

        Kept on the LayerState and rebuilt only when what it depends on moves
        - MIX, a bound WIDTH, a LEVEL - so a scrolling layer costs a lookup
        per pixel. Each entry is exactly what the per-pixel call would return,
        so the frames do not change; the firmware makes those calls per pixel
        and test_custom_golden.py diffs the two.
        """
        kind = layer.kind
        n = span or 1
        if kind == _comp.LayerKind.FLOW:
            key = (fg, span)
            if st.ramp_key != key:
                st.ramp = [gradient_pixel(fg, layer.color2, j, span, layer.seamless) for j in range(n)]
        elif kind == _comp.LayerKind.RAINBOW:
            key = span
            if st.ramp_key != key:
                st.ramp = [wheel(trunc_div(j * 256, n)) for j in range(n)]
        elif kind == _comp.LayerKind.METER and layer.gradient:
            key = (fg, span)
            if st.ramp_key != key:
                st.ramp = [gradient_pixel(fg, layer.color2, f, span, False) for f in range(n)]
        elif kind == _comp.LayerKind.GAUGE:
            step = layer.gauge_blend == GaugeBlend.STEP
            if layer.style == GaugeStyle.HEAT:
                key = p.level
                if st.ramp_key != key:
                    st.ramp = [scale_color_at(st.stops, step, layer.bg_color, p.level)]
            else:
                key = span
                if st.ramp_key != key:
                    st.ramp = [scale_color_at(st.stops, step, layer.bg_color,
                                              255 if span <= 1 else trunc_div(f * 255, span - 1))
                               for f in range(n)]
        else:
            return
        st.ramp_key = key

    def _layer_pixel(self, layer, st, p, k: int, span: int, fg: int) -> int:
        """The packed `(coverage << 24) | 0xRRGGBB` one layer shows at k.

        `fg` is the layer's color after MIX, and `st.ramp` is what
        _refresh_ramp() left there: both are the same for every pixel of a
        tick, so _composite_layer() works them out once.
        """
        kind = layer.kind

        if kind == _comp.LayerKind.OFF:
            # Coverage 0: a KEY layer contributes nothing, an OVER one paints
            # its own background - which is what "off" means in both.
            return layer.bg_color & 0xFFFFFF

        if kind == _comp.LayerKind.SOLID:
            return 0xFF000000 | (fg & 0xFFFFFF)

        if kind == _comp.LayerKind.PULSE:
            if layer.bounce:
                end = st.bounce_pos + max(1, p.run_length)
                lit = st.bounce_pos <= k < end
            else:
                j = (k + st.shift_acc) % (span or 1)
                lit = j < min(p.run_length, span)
            return (0xFF000000 | (fg & 0xFFFFFF)) if lit else (layer.bg_color & 0xFFFFFF)

        if kind == _comp.LayerKind.FLASH:
            return (0xFF000000 | (fg & 0xFFFFFF)) if st.flash_lit else (layer.bg_color & 0xFFFFFF)

        if kind == _comp.LayerKind.FLOW:
            # gradient_pixel(fg, color2, j, span, seamless)
            return 0xFF000000 | (st.ramp[(k + st.shift_acc) % (span or 1)] & 0xFFFFFF)

        if kind == _comp.LayerKind.RAINBOW:
            # wheel(j * 256 / span)
            return 0xFF000000 | (st.ramp[(k + st.shift_acc) % (span or 1)] & 0xFFFFFF)

        if kind == _comp.LayerKind.TWINKLE:
            # _step_twinkle() has already moved level/target/hold this tick.
            # The spark's own brightness *is* its coverage, so a KEY twinkle
            # fades into what is under it rather than onto its own background.
            if st.twinkle is None or k >= len(st.twinkle.level):
                return 0
            fgc = st.twinkle.palette[st.twinkle.color_idx[k]] if st.twinkle.palette else 0
            return (st.twinkle.level[k] << 24) | (fgc & 0xFFFFFF)

        if kind == _comp.LayerKind.BITSCROLL:
            if not st.scratch:
                return layer.bg_color & 0xFFFFFF
            if layer.bounce:
                idx = st.bounce_pos + k
                return st.scratch[idx] if idx < len(st.scratch) else 0
            if layer.repeating:
                return st.scratch[(k + st.shift_acc) % len(st.scratch)]
            j = (k + st.shift_acc) % (span or 1)
            return st.scratch[j] if j < st.content_len and j < len(st.scratch) else 0

        if kind == _comp.LayerKind.FRAMES:
            if not st.scratch:
                return 0
            ring = max(1, layer.frame_width)
            return st.scratch[(k + st.shift_acc) % ring]

        if kind == _comp.LayerKind.GAUGE:
            if layer.style == GaugeStyle.HEAT:
                # scale_color_at(stops, step, bg, level)
                return 0xFF000000 | (st.ramp[0] & 0xFFFFFF)
            lit_q8 = p.level * span
            full, frac = divmod(lit_q8, 255)
            f = (span - 1 - k) if layer.invert else k
            # scale_color_at(stops, step, bg, f * 255 / (span - 1))
            scale = st.ramp[f]
            if f < full:
                return 0xFF000000 | (scale & 0xFFFFFF)
            if f == full:
                return (frac << 24) | (scale & 0xFFFFFF)
            return layer.bg_color & 0xFFFFFF

        if kind == _comp.LayerKind.METER:
            lit_q8 = p.level * span
            full, frac = divmod(lit_q8, 255)
            f = (span - 1 - k) if layer.invert else k
            # level_fill() uses the three-argument gen_gradient(), whose
            # default is an open ramp - so seamless is False here, not True:
            # gradient_pixel(fg, color2, f, span, False).
            scale = st.ramp[f] if layer.gradient else fg
            if f < full:
                return 0xFF000000 | (scale & 0xFFFFFF)
            if f == full:
                return (frac << 24) | (scale & 0xFFFFFF)
            return layer.bg_color & 0xFFFFFF

        return layer.bg_color & 0xFFFFFF

    def _composite_layer(self, layer, st, p, span: int, alpha: int) -> None:
        step = layer.stride if layer.stride else self.length
        # A repeat is at most a stride wide, so no pixel is blended twice -
        # which ADD, SCREEN, MULTIPLY and MASK would stack. OVER already showed
        # only the last repeat to reach a pixel, which is exactly this.
        width = layer.stride if (layer.stride and layer.stride < span) else span
        ring = self._motion_ring(layer, span)
        # MIX slides the layer between its two colors with no rebuild.
        fg = lerp_color(layer.color, layer.color2, p.mix) if p.mix else layer.color
        self._refresh_ramp(layer, st, p, ring, fg)
        keyed = layer.blend in (_comp.LayerBlend.KEY, _comp.LayerBlend.ADD,
                                _comp.LayerBlend.SCREEN)

        base = p.start
        while base < self.length:
            for k in range(width):
                if base + k >= self.length:
                    break
                # `continuous` samples at the strand-wide index, so a strided
                # layer is one animation cut into windows, not N copies.
                local = (base + k - p.start) if layer.continuous else k
                word = self._layer_pixel(layer, st, p, local, ring, fg)
                cov = (word >> 24) & 0xFF
                rgb = word & 0xFFFFFF
                if keyed:
                    a = trunc_div(cov * alpha, 255)
                else:
                    # A blend that ignores coverage has to be handed a pixel
                    # that already accounts for it, resolved against the
                    # layer's own background - which is precisely what the
                    # preset does (_level_pixel() returns lerp_color(level_bg,
                    # color, frac) for the meter's partial edge pixel).
                    if cov != 255:
                        rgb = lerp_color(layer.bg_color, rgb, cov)
                    a = alpha
                self.buffer[base + k] = _blend_pixel(layer.blend, self.buffer[base + k], rgb, a)
            if not layer.stride:
                break
            base += step

    def _advance_composition(self, now_ms: int) -> None:
        c = self.composition
        if c is None:
            return
        # Unwinds rather than carrying on: a reader replaced the composition,
        # and a preset that did so has already painted the buffer.
        if not self._eval_drivers(c, now_ms):
            return

        elapsed = self._compose_clock(now_ms)
        self.buffer = [c.bg_color] * self.length

        n = min(len(c.layers), _comp.MAX_LAYERS, len(self.layer_states))
        for li in range(n):
            layer = c.layers[li]
            st = self.layer_states[li]

            period = layer.loop_ms if layer.loop_ms else c.loop_ms
            t = (elapsed % period) if period else elapsed

            window_alpha = _layer_window_alpha(layer, t, period)
            if window_alpha == 0:
                st.was_open = False
                continue

            p = self._resolve_layer(c, layer)
            span = self._resolve_span(p, st)
            if span == 0:
                st.was_open = False
                continue

            ring = self._motion_ring(layer, span)

            # A layer coming back in starts from its first frame, unless it
            # was asked to carry on where it left off.
            if not st.was_open and layer.retrigger:
                self._reset_layer_state(layer, st, ring)
            st.was_open = True

            alpha = trunc_div(window_alpha * p.opacity, 255)
            if alpha == 0:
                continue

            # A layer only advances while it is visible: an off-screen layer
            # costs nothing and comes back at frame zero, not mid-stride.
            self._advance_layer(layer, st, p, ring)
            self._composite_layer(layer, st, p, span, alpha)

        # _flush_buffer() reads buffer[] straight through from here.
        self.anim_mode = AnimMode.STATIC
        self.shift_step = 0
        self.shift_variant = 0



# ============================================================================
# Composition helpers - mirror of the free functions in src/led_custom.cpp
# ============================================================================

#: Quarter-wave sine, 64 entries, mirrored into a full cycle by _lfo_value().
#: A table rather than math.sin so the firmware can reproduce it byte for byte
#: with no floating-point rounding to argue about.
_SINE_TABLE = (
    128, 131, 134, 137, 140, 143, 146, 149, 152, 155, 158, 162, 165, 167, 170, 173,
    176, 179, 182, 185, 188, 190, 193, 196, 198, 201, 203, 206, 208, 211, 213, 215,
    218, 220, 222, 224, 226, 228, 230, 232, 234, 235, 237, 238, 240, 241, 243, 244,
    245, 246, 248, 249, 250, 250, 251, 252, 253, 253, 254, 254, 254, 255, 255, 255,
)


def _add_sat(a: int, b: int) -> int:
    """Saturating per-channel add. Mirrors LedStrand::addSat()."""
    r = min(255, ((a >> 16) & 0xFF) + ((b >> 16) & 0xFF))
    g = min(255, ((a >> 8) & 0xFF) + ((b >> 8) & 0xFF))
    bl = min(255, (a & 0xFF) + (b & 0xFF))
    return (r << 16) | (g << 8) | bl


def _screen_color(a: int, b: int) -> int:
    out = 0
    for shift in (16, 8, 0):
        x, y = (a >> shift) & 0xFF, (b >> shift) & 0xFF
        out |= (255 - trunc_div((255 - x) * (255 - y), 255)) << shift
    return out


def _mul_color(a: int, b: int) -> int:
    out = 0
    for shift in (16, 8, 0):
        x, y = (a >> shift) & 0xFF, (b >> shift) & 0xFF
        out |= trunc_div(x * y, 255) << shift
    return out


def _luma(c: int) -> int:
    """Rec. 601 luma, integer. Used only by MASK, to turn a colored layer into
    a single "how much of what is underneath survives"."""
    r, g, b = (c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF
    return (r * 77 + g * 150 + b * 29) >> 8


def _apply_curve(curve, v: int) -> int:
    """A driver's byte reshaped by a binding's curve: 16 equal segments,
    linear between them, integer throughout. Mirrors applyCurve()."""
    if not curve:
        return v
    points = _comp.CURVE_POINTS
    scaled = v * (points - 1)
    i = scaled // 255
    if i >= points - 1:
        return curve[points - 1]
    frac = scaled % 255
    return curve[i] + trunc_div((curve[i + 1] - curve[i]) * frac, 255)


def _shift_for(speed: int, ring: int, invert: bool) -> int:
    """pulse()/flow()'s shift_variant, exactly. Mirrors shiftFor()."""
    if ring == 0:
        return 0
    sp = (speed & 0xFF) % ring
    return ((ring - sp) % ring) if invert else sp


def _lfo_value(d, now_ms: int) -> int:
    """Wall-clock, like music_sync(), so a strand that misses a tick stays in
    phase rather than drifting behind. Mirrors lfoValue()."""
    period = d.period_ms if d.period_ms else 1
    t = (now_ms + d.phase_pct * period // 100) % period
    x = trunc_div(t * 255, period)
    shape = d.shape
    if shape == _comp.LfoShape.SAW:
        return x
    if shape == _comp.LfoShape.RAMP_DOWN:
        return 255 - x
    if shape == _comp.LfoShape.SQUARE:
        return 255 if x < 128 else 0
    if shape == _comp.LfoShape.SINE:
        q = x & 0x3F
        quadrant = (x >> 6) & 0x3
        if quadrant == 0:
            return _SINE_TABLE[q]
        if quadrant == 1:
            return _SINE_TABLE[63 - q]
        if quadrant == 2:
            return 255 - _SINE_TABLE[q]
        return 255 - _SINE_TABLE[63 - q]
    return 255 - abs(x * 2 - 255)  # TRIANGLE


def _layer_window_alpha(layer, t: int, period: int) -> int:
    """Two layers with overlapping windows and matching fades *are* a
    crossfade. Mirrors layerWindowAlpha()."""
    if layer.start_ms == 0 and layer.end_ms == 0:
        return 255
    begin = layer.start_ms
    end = layer.end_ms if layer.end_ms else (period if period else 0xFFFFFFFF)
    # The loop never reaches a window that starts at or past its end - which
    # a shortened loop leaves behind - and one that ends past it ends there.
    if period and begin >= period:
        return 0
    if period and end > period:
        end = period
    # A window whose end is at or before its start runs across the loop point,
    # which is how a layer stays lit through the wrap.
    wrapped = end <= begin and period
    inside = (t >= begin or t < end) if wrapped else (begin <= t < end)
    if not inside:
        return 0

    # uint32 arithmetic, as the firmware does it.
    since = ((t + (period - begin)) if (wrapped and t < end) else (t - begin)) & 0xFFFFFFFF
    until = ((end + (period - t)) if (wrapped and t >= begin) else (end - t)) & 0xFFFFFFFF
    if layer.fade_in_ms and since < layer.fade_in_ms:
        return trunc_div(since * 255, layer.fade_in_ms)
    if layer.fade_out_ms and until < layer.fade_out_ms:
        return trunc_div(until * 255, layer.fade_out_ms)
    return 255


def _blend_pixel(mode, dst: int, src: int, a: int) -> int:
    """Mirrors blendPixel()."""
    # MASK reads the layer, not the layer's coverage: it acts across the whole
    # span, dimming what is under it. That is what makes it a stencil. Alpha
    # then blends the masked result in like any other mode's, so a fading mask
    # eases in from untouched rather than from black.
    if mode == _comp.LayerBlend.MASK:
        return lerp_color(dst, lerp_color(0x000000, dst, _luma(src)), a)
    if a == 0:
        return dst
    if mode == _comp.LayerBlend.ADD:
        out = _add_sat(dst, src)
    elif mode == _comp.LayerBlend.SCREEN:
        out = _screen_color(dst, src)
    elif mode == _comp.LayerBlend.MULTIPLY:
        out = _mul_color(dst, src)
    else:
        out = src  # OVER, KEY
    return out if a == 255 else lerp_color(dst, out, a)


# Imported last, not at the top: composition.py needs BitScrollSegment, MusicTrack
# and the two Gauge enums from this module, so importing it up there would be a
# cycle through a half-built module. By here those types exist, and `_comp` is
# only ever read inside a method body, long after both modules have loaded.
from . import composition as _comp  # noqa: E402
