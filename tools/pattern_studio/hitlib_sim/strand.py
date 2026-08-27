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
import random
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Sequence

from .colors import gen_gradient, gen_rainbow, lerp_color, trunc_div
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

    OFF through RAINBOW share the overlay* animations' vocabulary, since each
    region's buffer is built the same way. GAUGE is the exception: it animates
    from a reading rather than from a clock, which is what makes one region an
    independent meter.
    """

    OFF = auto()
    SOLID = auto()
    PULSE = auto()
    FLASH = auto()
    FLOW = auto()
    RAINBOW = auto()
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
    speed: int = 1
    on_ms: int = 250
    off_ms: int = 250
    seamless: bool = True  # FLOW only.
    # GAUGE only, ignored by every other kind. `read` of None leaves the
    # region hand-driven through set_region_level().
    read: Optional[Callable[[], float]] = None
    empty_at: float = 0.0
    full_at: float = 100.0
    wrap: bool = False
    smoothing: int = 0
    invert: bool = False
    style: GaugeStyle = GaugeStyle.HEAT
    blend: GaugeBlend = GaugeBlend.LERP
    #: The scale, in the reading's own units. Empty falls back to `color` at
    #: `empty_at` blending to `color2` at `full_at`.
    stops: tuple[GaugeStop, ...] = ()


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
    # Index in the sequence passed to splice_mask_custom(), which is what
    # set_region_level() addresses. Not the index in splice_regions: SOLID and
    # OFF regions never get a state, so the two disagree.
    user_idx: int = 0
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
        self.shift_variant = 0

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
        self.twinkle_level: list[int] = [0] * self.length
        self.twinkle_target: list[int] = [0] * self.length
        self.twinkle_color_idx: list[int] = [0] * self.length
        self.twinkle_hold_ticks: list[int] = [0] * self.length
        self.twinkle_palette: list[int] = []
        self.twinkle_density_pct = 30
        self.twinkle_fade_step = 16
        self.twinkle_bg_color = 0
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
        # which is what lets Pattern Studio scrub by seeking.
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

        # Brightness
        self.brightness_pct = 100

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

        if self.pulse_run_len > 0:
            self._advance_pulse_bounce()
        elif self.bitscroll_master:
            self._advance_bitscroll_bounce()
        elif self.anim_mode == AnimMode.TWINKLE:
            self._advance_twinkle()
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
        self.pulse_run_len = 0
        self.bitscroll_master = []
        self.buffer = [color] * self.length
        self.shift_step = 0
        self.shift_variant = 0
        self.anim_mode = AnimMode.STATIC

    def pulse(self, color: int, run_length: int, speed: int, bg_color: int = 0,
              invert: bool = False, bounce: bool = False) -> None:
        self.bitscroll_master = []
        if not bounce:
            self.pulse_run_len = 0
            self.buffer = [bg_color] * self.length
            rl = min(run_length, self.length)
            for i in range(rl):
                self.buffer[i] = color
            self.shift_step = 0
            sp = speed % self.length
            self.shift_variant = ((self.length - sp) % self.length) if invert else sp
            self.anim_mode = AnimMode.SHIFT
        else:
            if len(self.buffer) != self.length:
                self.buffer = [bg_color] * self.length
            self.pulse_color = color
            self.pulse_bg = bg_color
            self.pulse_run_len = max(1, min(run_length, self.length))
            self.pulse_speed = max(speed, 1)
            self.pulse_offset = (self.length - self.pulse_run_len) if invert else 0
            self.pulse_dir = -1 if invert else 1
            self.anim_mode = AnimMode.STATIC  # content generated per-tick by _advance_pulse_bounce()

    def _advance_pulse_bounce(self) -> None:
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

    def flow(self, color1: int, color2: int, speed: int, invert: bool = False,
             seamless: bool = True) -> None:
        self.pulse_run_len = 0
        self.bitscroll_master = []
        self.buffer = gen_gradient(color1, color2, self.length, seamless)
        self.shift_step = 0
        sp = speed % self.length
        self.shift_variant = ((self.length - sp) % self.length) if invert else sp
        self.anim_mode = AnimMode.SHIFT

    def rainbow(self, speed: int) -> None:
        self.pulse_run_len = 0
        self.bitscroll_master = []
        self.buffer = gen_rainbow(self.length)
        self.shift_step = 0
        self.shift_variant = speed
        self.anim_mode = AnimMode.SHIFT

    def twinkle(self, colors: Sequence[int], density_pct: int = 30, fade_step: int = 16,
                bg_color: int = 0) -> None:
        self.pulse_run_len = 0
        self.bitscroll_master = []
        self.twinkle_palette = list(colors)
        self.twinkle_level = [0] * self.length
        self.twinkle_target = [0] * self.length
        self.twinkle_color_idx = [0] * self.length
        self.twinkle_hold_ticks = [0] * self.length
        self.twinkle_density_pct = min(density_pct, 100)
        self.twinkle_fade_step = max(fade_step, 1)
        self.twinkle_bg_color = bg_color
        if len(self.buffer) != self.length:
            self.buffer = [bg_color] * self.length
        self.anim_mode = AnimMode.TWINKLE

    def _advance_twinkle(self) -> None:
        active_count = sum(
            1 for i in range(self.length)
            if self.twinkle_level[i] > 0 or self.twinkle_hold_ticks[i] > 0
        )
        target_count = (self.length * self.twinkle_density_pct + 50) // 100

        if active_count < target_count and self.twinkle_palette:
            # Reservoir-sample one idle pixel to spawn, at most one per tick.
            chosen = -1
            idle_seen = 0
            for i in range(self.length):
                if self.twinkle_level[i] == 0 and self.twinkle_hold_ticks[i] == 0:
                    idle_seen += 1
                    if self._rng.randrange(idle_seen) == 0:
                        chosen = i
            if chosen >= 0:
                self.twinkle_color_idx[chosen] = self._rng.randrange(len(self.twinkle_palette))
                self.twinkle_target[chosen] = 255

        for i in range(self.length):
            if self.twinkle_hold_ticks[i] > 0:
                self.twinkle_hold_ticks[i] -= 1
                if self.twinkle_hold_ticks[i] == 0:
                    self.twinkle_target[i] = 0
            elif self.twinkle_level[i] < self.twinkle_target[i]:
                nl = self.twinkle_level[i] + self.twinkle_fade_step
                self.twinkle_level[i] = min(nl, 255)
                if self.twinkle_level[i] >= self.twinkle_target[i]:
                    self.twinkle_hold_ticks[i] = _TWINKLE_HOLD_TICKS
            elif self.twinkle_level[i] > self.twinkle_target[i]:
                nl = self.twinkle_level[i] - self.twinkle_fade_step
                self.twinkle_level[i] = max(nl, 0)

            fg = self.twinkle_palette[self.twinkle_color_idx[i]] if self.twinkle_palette else 0
            self.buffer[i] = lerp_color(self.twinkle_bg_color, fg, self.twinkle_level[i])

    def bitscroll(self, segments: Sequence[BitScrollSegment], speed: int, invert: bool = False,
                  bg_color: int = 0, bounce: bool = False, spacing: int = 5,
                  repeating: bool = True) -> None:
        self.pulse_run_len = 0

        unit: list[int] = []
        # Size of `unit` up to the last segment pixel, i.e. excluding the
        # trailing run of `spacing`, which only exists to separate one tile
        # from the next. A single non-tiled copy shouldn't carry it.
        content_len = 0
        for seg in segments:
            for _ in range(seg.width):
                if len(unit) >= MAX_LEDS:
                    break
                unit.append(seg.color)
            content_len = len(unit)
            if len(unit) >= MAX_LEDS:
                break
            for _ in range(spacing):
                if len(unit) >= MAX_LEDS:
                    break
                unit.append(bg_color)
            if len(unit) >= MAX_LEDS:
                break
        if not unit:
            unit.append(bg_color)
            content_len = len(unit)

        if not bounce:
            self.bitscroll_master = []
            if repeating:
                reps = (self.length // len(unit)) + 2
                self.buffer = unit * reps
            else:
                self.buffer = [bg_color] * self.length
                n = min(len(unit), self.length)
                self.buffer[:n] = unit[:n]
            self.shift_step = 0
            buf_size = len(self.buffer)
            sp = speed % buf_size
            self.shift_variant = ((buf_size - sp) % buf_size) if invert else sp
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
            self.bounce_speed = max(speed, 1)
            self.anim_mode = AnimMode.STATIC
            self._fill_bitscroll_from_master()

    def _fill_bitscroll_from_master(self) -> None:
        max_start = max(0, len(self.bitscroll_master) - self.length)
        start = min(self.bounce_scroll_pos, max_start)
        self.buffer = self.bitscroll_master[start:start + self.length]

    def _advance_bitscroll_bounce(self) -> None:
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
        if buf_size == 0:
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
        """Read the envelope at an arbitrary millisecond offset, interpolating
        between the two frames it falls between, so a coarse frame rate still
        renders as a smooth fill at the strand's faster refresh rate."""
        track = self.music_track
        if track is None or track.frame_count == 0 or track.frame_ms <= 0:
            return 0

        total_ms = track.duration_ms
        if position_ms >= total_ms:
            if not self.music_loop:
                return 0  # song over, let the meter drain
            position_ms %= total_ms
        if position_ms < 0:
            position_ms = 0

        idx, frac = divmod(position_ms, track.frame_ms)
        a = track.samples[idx]
        if idx + 1 < track.frame_count:
            b = track.samples[idx + 1]
        else:
            b = track.samples[0] if self.music_loop else a

        raw = a + trunc_div((b - a) * frac, track.frame_ms)
        return max(0, min(trunc_div(raw * self.music_sensitivity, 100), 255))

    def _level_pixel(self, i: int, full: int, frac: int) -> int:
        """The color one meter pixel shows, given this frame's fill cutoff:
        `full` whole lit pixels and `frac` of the way into the next one. The
        partial pixel is what keeps a 30-pixel strand from showing only 30
        distinguishable levels."""
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
                    state.shift_speed = r.speed
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
                    state.shift_speed = r.speed
                elif r.kind == SpliceRegionAnimKind.RAINBOW:
                    state.buffer = gen_rainbow(region_width)
                    state.shift_speed = r.speed
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
                    # empty scale becomes the two-color fallback, which is what
                    # guarantees there is always something to bracket between.
                    scale = r.stops or (GaugeStop(r.empty_at, r.color), GaugeStop(r.full_at, r.color2))
                    state.gauge_stops = sorted(
                        # wrap is False here whatever the gauge itself does: a
                        # stop's place on the scale is a clamp, not a lap.
                        (self._map_level_to(gs.at, r.empty_at, r.full_at, False), gs.color)
                        for gs in scale
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
            if state.shift_speed == 0:
                continue
            state.shift_step = (state.shift_step + state.shift_speed) % buf_size

    @staticmethod
    def _gauge_color_at(state: _SpliceRegionState, level: int) -> int:
        """The color a gauge's scale gives for a 0-255 level. Stops are already
        sorted and already on that axis, so this is a bracket and at most one
        lerp. Mirrors LedStrand::gaugeColorAt()."""
        stops = state.gauge_stops
        if not stops:
            return state.gauge_bg
        # Past either end the scale holds, so it never has to cover a range the
        # gauge does not care about.
        if level <= stops[0][0]:
            return stops[0][1]
        if level >= stops[-1][0]:
            return stops[-1][1]

        i = 1
        while i < len(stops) and stops[i][0] < level:
            i += 1
        lo_at, lo_color = stops[i - 1]
        hi_at, hi_color = stops[i]
        # STEP holds the lower stop until the higher one is actually reached,
        # which is the honest reading when the stops are thresholds something
        # crosses rather than points on a ramp.
        if state.gauge_blend == GaugeBlend.STEP:
            return lo_color

        span = hi_at - lo_at
        if span == 0:
            return hi_color
        return lerp_color(lo_color, hi_color, trunc_div((level - lo_at) * 255, span))

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

    def overlay_pulse(self, color: int, run_length: int, speed: int, bg_color: int = 0) -> None:
        self.overlay_buffer = [bg_color] * self.length
        rl = min(run_length, self.length)
        for i in range(rl):
            self.overlay_buffer[i] = color
        self.overlay_shift_step = 0
        self.overlay_shift_speed = speed
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

    def overlay_flow(self, color1: int, color2: int, speed: int, seamless: bool = True) -> None:
        self.overlay_buffer = gen_gradient(color1, color2, self.length, seamless)
        self.overlay_shift_step = 0
        self.overlay_shift_speed = speed
        self.overlay_anim_mode = AnimMode.SHIFT

    def overlay_rainbow(self, speed: int) -> None:
        self.overlay_buffer = gen_rainbow(self.length)
        self.overlay_shift_step = 0
        self.overlay_shift_speed = speed
        self.overlay_anim_mode = AnimMode.SHIFT

    def _shift_overlay_buffer(self) -> None:
        buf_size = len(self.overlay_buffer)
        if buf_size == 0:
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
        return max(1, (ms + self.refresh_ms // 2) // self.refresh_ms)

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
