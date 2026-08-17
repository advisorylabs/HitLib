"""Port of include/hitlib/led_strand.hpp + src/led_strand.cpp.

This mirrors LedStrand method-for-method so profile authoring in the GUI maps
1:1 onto the real API, and so exported C++ (Phase 5 of the project plan) is a
direct transliteration rather than a re-derivation.

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


@dataclass(frozen=True)
class BitScrollSegment:
    color: int
    width: int


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
        self.splice_sections = 0
        self.splice_invert = False
        self.splice_alternating = False
        self.splice_alt_ms = 100
        self.splice_bg_color = 0
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
        elif self.anim_mode == AnimMode.SHIFT:
            self._shift_buffer()

        if self.overlay_anim_mode == AnimMode.SHIFT:
            self._shift_overlay_buffer()

        if self.anim_mode == AnimMode.CENTER_SPREAD:
            self._advance_center_spread()

        self._advance_splice_alternating(now)

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

    def flash(self, color: int, speed: int, bg_color: int = 0) -> None:
        self.pulse_run_len = 0
        self.bitscroll_master = []
        reps = min(speed, 32)
        period = self.length * (1 + reps)
        self.buffer = [bg_color] * period
        for i in range(self.length):
            self.buffer[i] = color
        self.shift_step = 0
        self.shift_variant = 1
        self.anim_mode = AnimMode.SHIFT

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
                  bg_color: int = 0, bounce: bool = False, spacing: int = 0,
                  repeating: bool = True) -> None:
        self.pulse_run_len = 0

        unit: list[int] = []
        for seg in segments:
            for _ in range(seg.width):
                if len(unit) >= MAX_LEDS:
                    break
                unit.append(seg.color)
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
            master_len = max(self.length * 3, len(unit) * 3)
            self.bitscroll_master = []
            while len(self.bitscroll_master) < master_len:
                self.bitscroll_master.extend(unit)
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
                     alt_period_ms: int = 100, bg_color: int = 0) -> None:
        self.splice_sections = sections
        self.splice_invert = invert
        self.splice_alternating = alternating
        self.splice_alt_ms = alt_period_ms if alt_period_ms > 0 else 1
        self.splice_bg_color = bg_color
        self.splice_active = sections != 0
        self.splice_last_toggle_ms = self.now_ms
        self._rebuild_splice_mask()

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
                idx += 1

    def _advance_splice_alternating(self, now_ms: int) -> None:
        if not self.splice_active or not self.splice_alternating:
            return
        if now_ms - self.splice_last_toggle_ms >= self.splice_alt_ms:
            self.splice_invert = not self.splice_invert
            self.splice_last_toggle_ms = now_ms
            self._rebuild_splice_mask()

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

    def overlay_flash(self, color: int, speed: int, bg_color: int = 0) -> None:
        reps = min(speed, 32)
        period = self.length * (1 + reps)
        self.overlay_buffer = [bg_color] * period
        for i in range(self.length):
            self.overlay_buffer[i] = color
        self.overlay_shift_step = 0
        self.overlay_shift_speed = 1
        self.overlay_anim_mode = AnimMode.SHIFT

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

        pixels = []
        for i in range(self.length):
            if not spread_active and self.anim_mode == AnimMode.SHIFT and buf_size > 0:
                base_color = self.buffer[(i + self.shift_step) % buf_size]
            else:
                base_color = self.buffer[i]

            show_overlay = spread_active and self.spread_mask[i]
            color = self.overlay_buffer[i] if show_overlay else base_color

            if self.splice_active and not self.splice_show_anim[i]:
                color = self.overlay_buffer[i] if spread_active else self.splice_bg_color

            pixels.append(self._apply_brightness(color))
        self.pixels = pixels
