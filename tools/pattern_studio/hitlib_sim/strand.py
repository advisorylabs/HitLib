"""Port of include/hitlib/led_strand.hpp + src/led_strand.cpp.

This mirrors LedStrand method-for-method so profile authoring in the GUI maps
1:1 onto the real API, and so exported C++ is a direct transliteration rather 
than a re-derivation.

Threading/locking from the original (pros::Mutex, the shared s_adiMutex, and
the release-around-user-callback dance in tick()) is intentionally dropped --
this is a single-threaded simulation object, not a hardware driver.

now_ms is a simulated clock, not wall-clock time: it advances by exactly
refresh_ms on every tick() call, mirroring the embedded assumption that
LedGroup's task calls tick() once per refresh_ms. Any caller (a Sequencer, a
ProfileMode callback) that needs "now" for timing should read strand.now_ms.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, Optional, Sequence

from .colors import gen_gradient, gen_rainbow, lerp_color
from .profile import Profile

MAX_LEDS = 64
_TWINKLE_HOLD_TICKS = 8

AnimSetupFn = Callable[["Strand"], None]


class AnimMode(Enum):
    STATIC = auto()
    SHIFT = auto()
    CENTER_SPREAD = auto()
    TWINKLE = auto()
    FLASH = auto()


class SpliceMode(Enum):
    SPLIT = auto()
    CUSTOM = auto()


@dataclass(frozen=True)
class BitScrollSegment:
    color: int
    width: int


class SpliceRegionAnimKind(Enum):
    """Mirrors LedStrand::SpliceRegionAnimKind -- same vocabulary as the
    overlay* animations, since each region's buffer is built the same way.
    """

    OFF = auto()
    SOLID = auto()
    PULSE = auto()
    FLASH = auto()
    FLOW = auto()
    RAINBOW = auto()


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


@dataclass
class _SpliceRegionState:
    """Runtime animation state for one CUSTOM-mode region -- a scaled-down
    version of the overlay buffer (buffer + shift step/speed), but one per
    region instead of shared.
    """

    start: int
    buffer: list[int]
    shift_step: int = 0
    shift_speed: int = 0
    # FLASH regions blink their whole buffer on a tick timer instead of
    # shifting it, mirroring the strand-wide flash state.
    flashing: bool = False
    flash_color: int = 0
    flash_bg_color: int = 0
    flash_on_ticks: int = 1
    flash_off_ticks: int = 1
    flash_counter: int = 0
    flash_lit: bool = True


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
        self.spread_mask: list[bool] = [False] * self.length

        # Rendered output of the last tick() -- what the GUI should draw.
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

        # Flash -- whole-strip blink driven by tick counts rather than a
        # shifting buffer, so the lit and blank halves have independent
        # durations.
        self.flash_color = 0
        self.flash_bg_color = 0
        self.flash_on_ticks = 1
        self.flash_off_ticks = 1
        self.flash_counter = 0
        self.flash_lit = True

        # Overlay flash -- mirrors the base flash state above.
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

        # Center spread
        self.spread_pos = 0
        self.spread_tick_interval = 8
        self.spread_tick_counter = 0
        self.spread_layers: list[AnimSetupFn] = []
        self.spread_layer_idx = 0
        self.spread_invert = False
        self.spread_bounce = False
        self.spread_returning = False

        # Brightness
        self.brightness_pct = 100

        # Profile state
        self.active_profile: Optional[Profile] = None
        self.mode_stack: list[_ModeEntry] = []
        self.last_mode_idx = -1

    # ========================================================================
    # tick() -- call once per refresh_ms.
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
        elif self.anim_mode == AnimMode.SHIFT:
            self._shift_buffer()

        if self.overlay_anim_mode == AnimMode.SHIFT:
            self._shift_overlay_buffer()
        elif self.overlay_anim_mode == AnimMode.FLASH:
            self._advance_overlay_flash()

        if self.anim_mode == AnimMode.CENTER_SPREAD:
            self._advance_center_spread()

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
        # phase also renders the new colour -- count it as that phase's first
        # frame, or every phase comes out one tick short.
        hold = self.flash_on_ticks if self.flash_lit else self.flash_off_ticks
        if self.flash_counter >= hold:
            self.flash_counter = 0
            self.flash_lit = not self.flash_lit
            fill = self.flash_color if self.flash_lit else self.flash_bg_color
            self.buffer = [fill] * self.length
        self.flash_counter += 1

    def flow(self, color1: int, color2: int, speed: int, invert: bool = False) -> None:
        self.pulse_run_len = 0
        self.bitscroll_master = []
        self.buffer = gen_gradient(color1, color2, self.length)
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
        # Size of `unit` up to the last segment pixel -- i.e. excluding the
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
                # the strip to the near end and back -- the same travel pulse
                # bounce does with its run. Tiling here regardless of
                # `repeating` was the bug: bounce always looked repeating.
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

        for r in regions:
            if r.start >= self.length:
                continue
            end = min(r.start + r.width, self.length)
            region_width = end - r.start

            region_idx = -1
            fallback_color = 0x000000

            if r.kind == SpliceRegionAnimKind.SOLID:
                fallback_color = r.color
            elif r.kind != SpliceRegionAnimKind.OFF:
                state = _SpliceRegionState(start=r.start, buffer=[])
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
                    state.buffer = gen_gradient(r.color, r.color2, region_width)
                    state.shift_speed = r.speed
                elif r.kind == SpliceRegionAnimKind.RAINBOW:
                    state.buffer = gen_rainbow(region_width)
                    state.shift_speed = r.speed
                self.splice_regions.append(state)
                region_idx = len(self.splice_regions) - 1

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

    def overlay_flow(self, color1: int, color2: int, speed: int) -> None:
        self.overlay_buffer = gen_gradient(color1, color2, self.length)
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
    # Center spread
    # ========================================================================

    def _start_center_spread(self, layers: Sequence[AnimSetupFn], bounce: bool,
                              tick_interval: int, invert: bool) -> None:
        self.pulse_run_len = 0
        self.bitscroll_master = []
        self.spread_layers = list(layers)
        self.spread_layer_idx = 0
        self.spread_bounce = bounce
        self.spread_invert = invert
        self.spread_tick_interval = max(tick_interval, 1)
        self.spread_pos = 0
        self.spread_tick_counter = 0
        self.spread_returning = False
        self.spread_mask = [False] * self.length
        self.anim_mode = AnimMode.CENTER_SPREAD

    def center_spread(self, tick_interval: int = 8, invert: bool = False) -> None:
        self._start_center_spread([], False, tick_interval, invert)

    def center_spread_stacked(self, layers: Sequence[AnimSetupFn], tick_interval: int = 8,
                               invert: bool = False) -> None:
        self._start_center_spread(layers, False, tick_interval, invert)

    def center_spread_bounce(self, tick_interval: int = 8, invert: bool = False) -> None:
        self._start_center_spread([], True, tick_interval, invert)

    def center_spread_bounce_stacked(self, layers: Sequence[AnimSetupFn], tick_interval: int = 8,
                                      invert: bool = False) -> None:
        self._start_center_spread(layers, True, tick_interval, invert)

    def _advance_center_spread(self) -> None:
        max_pos = self.length // 2 + 1

        self.spread_tick_counter += 1
        if self.spread_tick_counter >= self.spread_tick_interval:
            self.spread_tick_counter = 0
            if not self.spread_bounce:
                if self.spread_pos < max_pos:
                    self.spread_pos += 1
                if self.spread_pos >= max_pos:
                    self._do_layer_swap()
                    return
            elif not self.spread_returning:
                if self.spread_pos < max_pos:
                    self.spread_pos += 1
                if self.spread_pos >= max_pos:
                    self.spread_returning = True
            else:
                if self.spread_pos > 0:
                    self.spread_pos -= 1
                if self.spread_pos == 0:
                    self._do_layer_swap()
                    return

        mid = self.length // 2
        for i in range(self.length):
            if self.spread_invert:
                dist = min(i, self.length - 1 - i)
            else:
                dist = abs(i - mid)
            self.spread_mask[i] = dist < self.spread_pos

    def _do_layer_swap(self) -> None:
        promoted_base = self.overlay_buffer
        self.overlay_buffer = []

        if self.spread_layers:
            self.spread_layer_idx = (self.spread_layer_idx + 1) % len(self.spread_layers)
            fn = self.spread_layers[self.spread_layer_idx]
            if fn is not None:
                fn(self)
                self.overlay_buffer = self.buffer
                self.overlay_anim_mode = self.anim_mode
                self.overlay_shift_step = self.shift_step
                self.overlay_shift_speed = self.shift_variant
                # FLASH keeps its state in dedicated timing fields rather than
                # the shift step, so hand those over too or the promoted overlay
                # would sit frozen on whatever frame it was captured at.
                self.overlay_flash_color = self.flash_color
                self.overlay_flash_bg_color = self.flash_bg_color
                self.overlay_flash_on_ticks = self.flash_on_ticks
                self.overlay_flash_off_ticks = self.flash_off_ticks
                self.overlay_flash_counter = self.flash_counter
                self.overlay_flash_lit = self.flash_lit
        # If spread_layers is empty (plain center_spread/center_spread_bounce), the
        # C++ source leaves overlayBuffer moved-from (empty) here and relies on the
        # caller to have re-primed it before the mask grows again. Backfill with
        # black instead of letting a masked-pixel read run off the end of the list
        # -- same intent as the `buffer` size safety-net two lines down, just
        # applied to the side the original didn't guard.
        if len(self.overlay_buffer) != self.length:
            self.overlay_buffer = [0] * self.length

        self.buffer = promoted_base
        if len(self.buffer) != self.length:
            self.buffer = [0] * self.length
        self.pulse_run_len = 0
        self.bitscroll_master = []

        self.spread_pos = 0
        self.spread_tick_counter = 0
        self.spread_returning = False
        self.spread_mask = [False] * self.length
        self.anim_mode = AnimMode.CENTER_SPREAD

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

    def _flush_buffer(self) -> None:
        spread_active = self.anim_mode == AnimMode.CENTER_SPREAD
        buf_size = len(self.buffer)
        overlay_buf_size = len(self.overlay_buffer)

        pixels = []
        for i in range(self.length):
            if not spread_active and self.anim_mode == AnimMode.SHIFT and buf_size > 0:
                base_color = self.buffer[(i + self.shift_step) % buf_size]
            else:
                base_color = self.buffer[i]

            # Mirrors base_color above -- without this, overlay_shift_step
            # (advanced every tick by _shift_overlay_buffer()) is computed but
            # never actually read, so overlay animations render as a single
            # frozen frame instead of animating.
            if self.overlay_anim_mode == AnimMode.SHIFT and overlay_buf_size > 0:
                overlay_color = self.overlay_buffer[(i + self.overlay_shift_step) % overlay_buf_size]
            else:
                overlay_color = self.overlay_buffer[i]

            show_overlay = spread_active and self.spread_mask[i]
            color = overlay_color if show_overlay else base_color

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
