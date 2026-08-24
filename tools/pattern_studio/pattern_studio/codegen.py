"""Exports Pattern Studio designs as ready-to-include HitLib C++.

Each strand becomes its own namespace under hitlib::profiles, holding:

  * constexpr hardware values (port, length, refresh, brightness) so the
    LedStrand on the robot is constructed from the same numbers the preview
    ran against, instead of being retyped by hand;
  * a mode:: namespace of named index constants, since activateMode() takes
    an index and counting array rows by hand is where mode mixups come from;
  * the setup functions (in detail::), the ProfileMode table, the Profile,
    and an apply() that sets brightness and attaches the profile in one call.

The per-strand namespace is also what makes multi-strand exports safe: two
strands that each define an "Idle" mode would otherwise emit two identical
`inline void idle(LedStrand&)` definitions and fail to compile.

validate_for_export() (or validate_document_for_export()) should be called
first and its errors shown to the user, the generators assume a config that
already passed validation and do not re-check ranges themselves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import (
    AnimationConfig,
    AnimationKind,
    ModeConfig,
    OverlayAnimationConfig,
    OverlayAnimationKind,
    SpliceMaskConfig,
    SpliceModeKind,
    SpliceRegionConfig,
    StrandConfig,
)

MAX_LEDS = 64


# ============================================================================
# Validation
# ============================================================================


def _effective_modes(config: StrandConfig) -> list[ModeConfig]:
    """Modes to export: the real profile if one's defined, else the single
    free-standing animation wrapped as a synthetic one-mode profile. So
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
        errors.append("Profile has no modes, add at least one mode before exporting.")

    seen_names = set()
    for mode in modes:
        if not mode.name.strip():
            errors.append("A mode has an empty name.")
        if mode.name in seen_names:
            errors.append(f'Duplicate mode name "{mode.name}", mode names must be unique.')
        seen_names.add(mode.name)
        if not (0 <= mode.priority <= 255):
            errors.append(f'Mode "{mode.name}": priority must be 0-255 (got {mode.priority}).')

        if mode.phases:
            for phase in mode.phases:
                if phase.duration_ms < 1:
                    errors.append(f'Mode "{mode.name}", phase "{phase.name}": duration must be >= 1 ms.')
                errors.extend(_validate_animation(mode.name, phase.name, phase.animation))
                errors.extend(_validate_splice(mode.name, phase.name, phase.splice))
        else:
            errors.extend(_validate_animation(mode.name, None, mode.animation))
            errors.extend(_validate_splice(mode.name, None, mode.splice))

    return errors


def validate_document_for_export(configs: list[StrandConfig]) -> list[str]:
    """Validate every strand for a whole-document export.

    Beyond each strand's own checks, strand names have to be distinct: they
    become the namespace names in the generated header, and two strands called
    "Strand" would produce `strand` and `strand2` with nothing to say which is
    which. Renaming is a one-word fix and keeps the output readable.
    """
    if not configs:
        return ["Nothing to export, add at least one strand."]

    errors: list[str] = []
    seen: dict[str, int] = {}
    for cfg in configs:
        name = cfg.name or "Profile"
        seen[name] = seen.get(name, 0) + 1
    for name, count in seen.items():
        if count > 1:
            errors.append(
                f'{count} strands are named "{name}", give each strand a unique name '
                f"so its generated namespace can be told apart."
            )

    for cfg in configs:
        label = cfg.name or "Profile"
        errors.extend(f"[{label}] {e}" for e in validate_for_export(cfg))
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
    # Flash durations are milliseconds, not uint8 counts. They get their own
    # bound (uint32 on the C++ side, but the GUI caps at 10 s).
    for label, value in (("on_ms", a.on_ms), ("off_ms", a.off_ms)):
        if value < 1:
            errors.append(f"{where}: {label} must be >= 1 ms (got {value}).")
    return errors


def _validate_splice(mode_name: str, phase_name: str | None, s: SpliceMaskConfig) -> list[str]:
    if not s.enabled:
        return []
    where = f'mode "{mode_name}"' + (f', phase "{phase_name}"' if phase_name else "")
    errors = []
    if s.mode == SpliceModeKind.SPLIT:
        if not (0 <= s.sections <= 255):
            errors.append(f"{where}: splice sections must be 0-255 (got {s.sections}).")
    else:
        if not s.regions:
            errors.append(f"{where}: Custom splice mask needs at least one region.")
        for i, r in enumerate(s.regions):
            if r.width < 1:
                errors.append(f"{where}: splice region {i + 1} width must be >= 1 (got {r.width}).")
            if not (0 <= r.start <= 255):
                errors.append(f"{where}: splice region {i + 1} start must be 0-255 (got {r.start}).")
    return errors


# ============================================================================
# Identifier sanitizing
# ============================================================================

_WORD_RE = re.compile(r"[A-Za-z0-9]+")

# Reserved words that would make the generated identifier a syntax error if
# used verbatim (e.g. a mode named "Default", _effective_modes() synthesizes
# exactly that name for a non-profile strand's single animation).
_CPP_KEYWORDS = {
    "alignas", "alignof", "and", "and_eq", "asm", "auto", "bitand", "bitor",
    "bool", "break", "case", "catch", "char", "char8_t", "char16_t", "char32_t",
    "class", "compl", "concept", "const", "consteval", "constexpr", "constinit",
    "const_cast", "continue", "co_await", "co_return", "co_yield", "decltype",
    "default", "delete", "do", "double", "dynamic_cast", "else", "enum",
    "explicit", "export", "extern", "false", "float", "for", "friend", "goto",
    "if", "inline", "int", "long", "mutable", "namespace", "new", "noexcept",
    "not", "not_eq", "nullptr", "operator", "or", "or_eq", "private",
    "protected", "public", "register", "reinterpret_cast", "requires",
    "return", "short", "signed", "sizeof", "static", "static_assert",
    "static_cast", "struct", "switch", "template", "this", "thread_local",
    "throw", "true", "try", "typedef", "typeid", "typename", "union",
    "unsigned", "using", "virtual", "void", "volatile", "wchar_t", "while",
    "xor", "xor_eq",
}


def _camel_case(name: str, fallback: str) -> str:
    words = _WORD_RE.findall(name)
    if not words:
        words = [fallback]
    first, *rest = words
    ident = first.lower() + "".join(w.capitalize() for w in rest)
    if ident[0].isdigit():
        ident = fallback + ident
    if ident in _CPP_KEYWORDS:
        ident = ident + fallback.capitalize()
    return ident


def _snake_case(name: str, fallback: str) -> str:
    words = _WORD_RE.findall(name)
    if not words:
        words = [fallback]
    ident = "_".join(w.lower() for w in words)
    if ident[0].isdigit():
        ident = f"{fallback}_{ident}"
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
        return f"s.flash({_hex(a.color)}, {a.on_ms}, {a.off_ms}, {_hex(a.bg_color)});"
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


def _overlay_statement(o: OverlayAnimationConfig) -> str:
    if o.kind == OverlayAnimationKind.OFF:
        return "s.overlaySetColor(0x000000);"
    if o.kind == OverlayAnimationKind.SOLID:
        return f"s.overlaySetColor({_hex(o.color)});"
    if o.kind == OverlayAnimationKind.PULSE:
        return f"s.overlayPulse({_hex(o.color)}, {o.run_length}, {o.speed}, {_hex(o.bg_color)});"
    if o.kind == OverlayAnimationKind.FLASH:
        return f"s.overlayFlash({_hex(o.color)}, {o.on_ms}, {o.off_ms}, {_hex(o.bg_color)});"
    if o.kind == OverlayAnimationKind.FLOW:
        return f"s.overlayFlow({_hex(o.color)}, {_hex(o.color2)}, {o.speed});"
    if o.kind == OverlayAnimationKind.RAINBOW:
        return f"s.overlayRainbow({o.speed});"
    raise ValueError(f"unhandled overlay kind: {o.kind}")


def _region_literal(r: SpliceRegionConfig) -> str:
    a = r.animation
    kind = f"LedStrand::SpliceRegionAnimKind::{a.kind.value.upper()}"
    return (
        f"{{.start = {r.start}, .width = {r.width}, .kind = {kind}, "
        f".color = {_hex(a.color)}, .color2 = {_hex(a.color2)}, .bgColor = {_hex(a.bg_color)}, "
        f".runLength = {a.run_length}, .speed = {a.speed}, "
        f".onMs = {a.on_ms}, .offMs = {a.off_ms}}}"
    )


def _splice_statements(s: SpliceMaskConfig) -> list[str]:
    if not s.enabled:
        return []
    if s.mode == SpliceModeKind.SPLIT:
        lines: list[str] = []
        if s.needs_overlay():
            lines.append(_overlay_statement(s.overlay))
        lines.append(
            f"s.spliceMask({s.sections}, {_bool(s.invert)}, {_bool(s.alternating)}, "
            f"{s.alt_period_ms}, {_hex(s.bg_color)}, {_bool(s.use_overlay)});"
        )
        return lines
    region_list = ", ".join(_region_literal(r) for r in s.regions)
    return [f"s.spliceMaskCustom({{{region_list}}});"]


def _leaf_body(a: AnimationConfig, splice: SpliceMaskConfig) -> list[str]:
    return [_animation_statement(a), *_splice_statements(splice)]


@dataclass
class _StrandRender:
    """One strand's generated namespace, plus what the usage banner needs to
    describe it (its namespace, the variable name the banner declares for it,
    and its mode constants in index order)."""

    config: StrandConfig
    ns: str
    var: str
    mode_names: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)

# ============================================================================
# Full-file generation
# ============================================================================

# Names that the strand namespace defines itself, a mode constant that sanitized
# to one of these would shadow it.
_RESERVED_MEMBERS = {
    "adiPort", "smartPort", "length", "refreshMs", "brightness",
    "mode", "detail", "modeTable", "profile", "apply",
}


def _constructor_args(config: StrandConfig, ns: str) -> str:
    parts = [f"{ns}::smartPort"] if config.smart_port else []
    parts += [f"{ns}::adiPort", f"{ns}::length", f"{ns}::refreshMs"]
    return ", ".join(parts)


def _usage_banner(entries: list[_StrandRender], header_name: str) -> list[str]:
    """The comment block at the top of every export: exactly what to paste into
    main.cpp, spelled out with this design's real identifiers, ports and mode
    names, so nothing has to be transcribed by hand from the GUI.
    """
    single = len(entries) == 1
    lines = [
        "// Generated by HitLib Pattern Studio - edit the source design, not this file.",
        "//",
        "// 1. Drop this file into your PROS project's include/ directory.",
        "// 2. Include it after hitlib, then wire it up:",
        "//",
        '//        #include "hitlib/hitapi.hpp"',
        f'//        #include "{header_name}"',
        "//",
    ]
    for e in entries:
        lines.append(f"//        namespace {e.ns} = hitlib::profiles::{e.ns};")
    lines.append("//")
    for e in entries:
        lines.append(f"//        hitlib::LedStrand {e.var}({_constructor_args(e.config, e.ns)});")
    lines.append("//        hitlib::LedGroup  group;")
    lines.append("//")
    lines.append("//        void initialize() {")
    for e in entries:
        lines.append(f"//            group.add(&{e.var});")
    lines.append("//            group.init();")
    lines.append("//            group.start();")
    if single:
        # One profile, one group, so apply it to every strand at once.
        lines.append(f"//            {entries[0].ns}::apply(group);   // brightness + attachProfile")
        lines.append(f"//            group.activateMode({entries[0].ns}::mode::{entries[0].mode_names[0]});")
    else:
        # Each strand carries its own profile, so they're attached (and driven)
        # per strand rather than through the group.
        for e in entries:
            lines.append(f"//            {e.ns}::apply({e.var});   // brightness + attachProfile")
        for e in entries:
            lines.append(f"//            {e.var}.activateMode({e.ns}::mode::{e.mode_names[0]});")
    lines.append("//        }")
    lines.append("//")
    for e in entries:
        listed = ", ".join(f"{name} = {i}" for i, name in enumerate(e.mode_names))
        lines.append(f"// Modes - {e.ns}::mode::{{{listed}}}")
    return lines


def _render_strand(config: StrandConfig, ns_namer: _UniqueNamer) -> _StrandRender:
    modes = _effective_modes(config)
    display_name = config.name or "Profile"
    ns = ns_namer.make(display_name, "profile")

    fn_namer = _UniqueNamer()
    mode_namer = _UniqueNamer()
    for reserved in sorted(_RESERVED_MEMBERS):
        mode_namer.make(reserved, "mode")

    function_blocks: list[str] = []
    mode_entries: list[str] = []
    mode_names: list[str] = []

    for mode in modes:
        activate_fn, tick_fn, blocks = _generate_mode(mode, fn_namer)
        function_blocks.extend(blocks)
        tick_arg = f"detail::{tick_fn}" if tick_fn else "nullptr"
        mode_entries.append(
            f'    {{"{_escape(mode.name)}", {mode.priority}, detail::{activate_fn}, {tick_arg}}},'
        )
        mode_names.append(mode_namer.make(mode.name, "mode"))

    lines: list[str] = []
    lines.append(f'/// "{_escape(display_name)}" - exported from Pattern Studio.')
    lines.append(f"namespace {ns} {{")
    lines.append("")
    lines.append("// --- Hardware: the strand this design was previewed against ---")
    if config.smart_port:
        lines.append(f"constexpr uint8_t  smartPort  = {config.smart_port};  // ADI expander")
    lines.append(f"constexpr uint8_t  adiPort    = {config.adi_port};")
    lines.append(f"constexpr uint8_t  length     = {config.length};")
    lines.append(f"constexpr uint32_t refreshMs  = {config.refresh_ms};")
    lines.append(f"constexpr uint8_t  brightness = {config.brightness};")
    lines.append("")
    lines.append("// --- Mode indices: pass these to activateMode() / activateModeTimed() ---")
    lines.append("namespace mode {")
    width = max(len(n) for n in mode_names)
    for i, (name, m) in enumerate(zip(mode_names, modes)):
        lines.append(
            f'constexpr uint8_t {name:<{width}} = {i};  // "{_escape(m.name)}", priority {m.priority}'
        )
    lines.append("}  // namespace mode")
    lines.append("")
    lines.append("namespace detail {")
    for block in function_blocks:
        lines.append(block)
        lines.append("")
    lines.append("}  // namespace detail")
    lines.append("")
    lines.append("inline const ProfileMode modeTable[] = {")
    lines.extend(mode_entries)
    lines.append("};")
    lines.append("")
    lines.append(f'inline const Profile profile = {{"{_escape(display_name)}", modeTable, {len(modes)}}};')
    lines.append("")
    lines.append("/// Sets this design's brightness and attaches its profile. Call after")
    lines.append("/// init()/start(), then activateMode() with a mode:: constant above.")
    lines.append("inline void apply(LedStrand& s) { s.setBrightness(brightness); s.attachProfile(&profile); }")
    lines.append("inline void apply(LedGroup& g) { g.setBrightness(brightness); g.attachProfile(&profile); }")
    lines.append("")
    lines.append(f"}}  // namespace {ns}")

    return _StrandRender(
        config=config,
        ns=ns,
        var=f"{ns}Strand",
        mode_names=mode_names,
        lines=lines,
    )


def _render_file(configs: list[StrandConfig], header_name: str | None) -> str:
    single = len(configs) == 1
    ns_namer = _UniqueNamer()
    entries = [_render_strand(cfg, ns_namer) for cfg in configs]

    default_name = suggested_header_name(configs[0].name) if single else "led_profiles.hpp"

    lines: list[str] = ["#pragma once", ""]
    lines.extend(_usage_banner(entries, header_name or default_name))
    lines.append("")
    lines.append('#include "hitlib/led_group.hpp"')
    lines.append('#include "hitlib/led_profile.hpp"')
    lines.append('#include "hitlib/led_sequencer.hpp"')
    lines.append('#include "hitlib/led_strand.hpp"')
    lines.append("")
    lines.append("namespace hitlib::profiles {")
    lines.append("")
    for entry in entries:
        lines.extend(entry.lines)
        lines.append("")
    lines.append("}  // namespace hitlib::profiles")
    lines.append("")
    return "\n".join(lines)


def suggested_header_name(strand_name: str) -> str:
    """Default filename to offer for a single-strand export: "My Robot" ->
    my_robot.hpp."""
    return _snake_case(strand_name or "profile", "profile") + ".hpp"


def generate_cpp(config: StrandConfig, header_name: str | None = None) -> str:
    """Render one strand as a standalone header.

    @p header_name is the filename the export will be saved as, it only
    feeds the #include line in the usage banner.
    """
    return _render_file([config], header_name)


def generate_document_cpp(configs: list[StrandConfig], header_name: str | None = None) -> str:
    """Render every strand in the document into a single header, each in its
    own namespace.

    Sharing one file (and therefore one namespace namer) is what keeps two
    strands that both have, say, an "Idle" mode from generating colliding
    identifiers; separately exported files could not see each other.
    """
    if not configs:
        raise ValueError("no strands to export")
    return _render_file(configs, header_name)


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
