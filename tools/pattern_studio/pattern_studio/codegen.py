"""Exports a StrandConfig as C++ matching include/hitlib/profiles/classic.hpp's
shape: one inline setup function per mode (and per sequenced phase), a
Sequencer::Phase array + Sequencer for timed modes, a ProfileMode array, and
the final Profile struct.

validate_for_export() should be called first and its errors shown to the
user -- generate_cpp() assumes a config that already passed validation and
does not re-check ranges itself.
"""

from __future__ import annotations

import re

from .models import AnimationConfig, AnimationKind, ModeConfig, SpliceMaskConfig, StrandConfig

MAX_LEDS = 64


# ============================================================================
# Validation
# ============================================================================


def _effective_modes(config: StrandConfig) -> list[ModeConfig]:
    """Modes to export: the real profile if one's defined, else the single
    free-standing animation wrapped as a synthetic one-mode profile -- so
    Export works regardless of whether "Use Profile" is toggled on.
    """
    if config.use_profile and config.profile_modes:
        return config.profile_modes
    return [ModeConfig(name="Default", priority=100, animation=config.animation, splice=config.splice)]


def validate_for_export(config: StrandConfig) -> list[str]:
    errors: list[str] = []

    if not (1 <= config.length <= MAX_LEDS):
        errors.append(f"Strand length must be 1-{MAX_LEDS} (got {config.length}).")
    if not (0 <= config.brightness <= 100):
        errors.append(f"Brightness must be 0-100 (got {config.brightness}).")
    if not (1 <= config.adi_port <= 8):
        errors.append(f"ADI port must be 1-8 (got {config.adi_port}).")
    if not (0 <= config.smart_port <= 21):
        errors.append(f"Smart port must be 0-21 (got {config.smart_port}).")
    if config.refresh_ms < 1:
        errors.append(f"Refresh interval must be >= 1 ms (got {config.refresh_ms}).")

    modes = _effective_modes(config)
    if not modes:
        errors.append("Profile has no modes -- add at least one mode before exporting.")

    seen_names = set()
    for mode in modes:
        if not mode.name.strip():
            errors.append("A mode has an empty name.")
        if mode.name in seen_names:
            errors.append(f'Duplicate mode name "{mode.name}" -- mode names must be unique.')
        seen_names.add(mode.name)
        if not (0 <= mode.priority <= 255):
            errors.append(f'Mode "{mode.name}": priority must be 0-255 (got {mode.priority}).')

        if mode.phases:
            for phase in mode.phases:
                if phase.duration_ms < 1:
                    errors.append(f'Mode "{mode.name}", phase "{phase.name}": duration must be >= 1 ms.')
                errors.extend(_validate_animation(mode.name, phase.name, phase.animation))
        else:
            errors.extend(_validate_animation(mode.name, None, mode.animation))

    return errors


def _validate_animation(mode_name: str, phase_name: str | None, a: AnimationConfig) -> list[str]:
    where = f'mode "{mode_name}"' + (f', phase "{phase_name}"' if phase_name else "")
    errors = []
    if a.kind == AnimationKind.TWINKLE and not a.palette:
        errors.append(f"{where}: Twinkle needs at least one palette color.")
    if a.kind == AnimationKind.BITSCROLL and a.segment_width < 1:
        errors.append(f"{where}: Bitscroll segment width must be >= 1.")
    for label, value in (("run_length", a.run_length), ("speed", a.speed), ("segment_width", a.segment_width)):
        if not (0 <= value <= 255):
            errors.append(f"{where}: {label} must fit in 0-255 (got {value}).")
    return errors


# ============================================================================
# Identifier sanitizing
# ============================================================================

_WORD_RE = re.compile(r"[A-Za-z0-9]+")


def _camel_case(name: str, fallback: str) -> str:
    words = _WORD_RE.findall(name)
    if not words:
        words = [fallback]
    first, *rest = words
    ident = first.lower() + "".join(w.capitalize() for w in rest)
    if ident[0].isdigit():
        ident = fallback + ident
    return ident


class _UniqueNamer:
    def __init__(self):
        self._used: set[str] = set()

    def make(self, name: str, fallback: str) -> str:
        base = _camel_case(name, fallback)
        candidate = base
        n = 2
        while candidate in self._used:
            candidate = f"{base}{n}"
            n += 1
        self._used.add(candidate)
        return candidate


# ============================================================================
# Value formatting
# ============================================================================


def _hex(color: int) -> str:
    return f"0x{color & 0xFFFFFF:06X}"


def _bool(b: bool) -> str:
    return "true" if b else "false"


def _palette_literal(colors: list[int]) -> str:
    return "{" + ", ".join(_hex(c) for c in colors) + "}"


# ============================================================================
# Animation / splice call generation
# ============================================================================


def _animation_statement(a: AnimationConfig) -> str:
    if a.kind == AnimationKind.OFF:
        return "s.off();"
    if a.kind == AnimationKind.SOLID:
        return f"s.setColor({_hex(a.color)});"
    if a.kind == AnimationKind.PULSE:
        return (
            f"s.pulse({_hex(a.color)}, {a.run_length}, {a.speed}, {_hex(a.bg_color)}, "
            f"{_bool(a.invert)}, {_bool(a.bounce)});"
        )
    if a.kind == AnimationKind.FLASH:
        return f"s.flash({_hex(a.color)}, {a.speed}, {_hex(a.bg_color)});"
    if a.kind == AnimationKind.FLOW:
        return f"s.flow({_hex(a.color)}, {_hex(a.color2)}, {a.speed}, {_bool(a.invert)});"
    if a.kind == AnimationKind.RAINBOW:
        return f"s.rainbow({a.speed});"
    if a.kind == AnimationKind.TWINKLE:
        return (
            f"s.twinkle({_palette_literal(a.palette)}, {a.density_pct}, {a.fade_step}, {_hex(a.bg_color)});"
        )
    if a.kind == AnimationKind.BITSCROLL:
        segment = f"LedStrand::BitScrollSegment{{{_hex(a.color)}, {a.segment_width}}}"
        return (
            f"s.bitscroll({{{segment}}}, {a.speed}, {_bool(a.invert)}, {_hex(a.bg_color)}, "
            f"{_bool(a.bounce)}, {a.spacing}, {_bool(a.repeating)});"
        )
    raise ValueError(f"unhandled animation kind: {a.kind}")


def _splice_statement(s: SpliceMaskConfig) -> str | None:
    if not s.enabled:
        return None
    return (
        f"s.spliceMask({s.sections}, {_bool(s.invert)}, {_bool(s.alternating)}, "
        f"{s.alt_period_ms}, {_hex(s.bg_color)});"
    )


def _leaf_body(a: AnimationConfig, splice: SpliceMaskConfig) -> list[str]:
    lines = [_animation_statement(a)]
    splice_stmt = _splice_statement(splice)
    if splice_stmt:
        lines.append(splice_stmt)
    return lines


# ============================================================================
# Full-file generation
# ============================================================================


def generate_cpp(config: StrandConfig, profile_var_name: str | None = None) -> str:
    modes = _effective_modes(config)
    namer = _UniqueNamer()
    profile_display_name = config.name or "Profile"
    var_name = profile_var_name or _camel_case(profile_display_name, "profile")

    function_blocks: list[str] = []
    mode_entries: list[str] = []

    for mode in modes:
        activate_fn, tick_fn, blocks = _generate_mode(mode, namer)
        function_blocks.extend(blocks)
        tick_arg = tick_fn if tick_fn else "nullptr"
        mode_entries.append(f'    {{"{_escape(mode.name)}", {mode.priority}, {activate_fn}, {tick_arg}}},')

    modes_array_name = f"{var_name}Modes"

    lines: list[str] = []
    lines.append("#pragma once")
    lines.append("")
    lines.append("// Generated by HitLib Pattern Studio -- edit the source profile, not this file.")
    lines.append("")
    lines.append('#include "hitlib/led_strand.hpp"')
    lines.append('#include "hitlib/led_profile.hpp"')
    lines.append('#include "hitlib/led_sequencer.hpp"')
    lines.append("")
    lines.append("namespace hitlib::profiles {")
    lines.append("")

    for block in function_blocks:
        lines.append(block)
        lines.append("")

    lines.append(f"inline const ProfileMode {modes_array_name}[] = {{")
    lines.extend(mode_entries)
    lines.append("};")
    lines.append("")
    lines.append(
        f'inline const Profile {var_name} = {{"{_escape(profile_display_name)}", '
        f"{modes_array_name}, {len(modes)}}};"
    )
    lines.append("")
    lines.append("} // namespace hitlib::profiles")
    lines.append("")
    return "\n".join(lines)


def _generate_mode(mode: ModeConfig, namer: _UniqueNamer) -> tuple[str, str | None, list[str]]:
    blocks: list[str] = []

    if not mode.phases:
        activate_fn = namer.make(mode.name, "mode")
        body = "\n".join(f"    {line}" for line in _leaf_body(mode.animation, mode.splice))
        blocks.append(f"inline void {activate_fn}(LedStrand& s) {{\n{body}\n}}")
        return activate_fn, None, blocks

    phase_fn_names = []
    for phase in mode.phases:
        fn_name = namer.make(f"{mode.name} {phase.name}", "phase")
        body = "\n".join(f"    {line}" for line in _leaf_body(phase.animation, phase.splice))
        blocks.append(f"inline void {fn_name}(LedStrand& s) {{\n{body}\n}}")
        phase_fn_names.append((fn_name, phase.duration_ms))

    phases_array_name = namer.make(f"{mode.name} Phases", "phases")
    phase_entries = "\n".join(f"    {{{dur}, {fn}}}," for fn, dur in phase_fn_names)
    blocks.append(
        f"inline const Sequencer::Phase {phases_array_name}[] = {{\n{phase_entries}\n}};"
    )

    seq_name = namer.make(f"{mode.name} Seq", "seq")
    blocks.append(f"inline Sequencer {seq_name}({phases_array_name}, {len(phase_fn_names)});")

    activate_fn = namer.make(f"{mode.name} Activate", "modeActivate")
    blocks.append(f"inline void {activate_fn}(LedStrand& s) {{ {seq_name}.start(s); }}")

    tick_fn = namer.make(f"{mode.name} Tick", "modeTick")
    blocks.append(f"inline void {tick_fn}(LedStrand& s) {{ {seq_name}.update(s); }}")

    return activate_fn, tick_fn, blocks


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')
