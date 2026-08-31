"""Exports Pattern Studio designs as ready-to-include HitLib C++.

Each strand becomes its own namespace under hitlib::profiles, holding:

  * constexpr hardware values (port, length, refresh, brightness), so the
    LedStrand is built from the numbers the preview ran against;
  * a mode:: namespace of named index constants for activateMode();
  * the setup functions (in detail::), the ProfileMode table, the Profile,
    and an apply() that sets brightness and attaches the profile.

The per-strand namespace also keeps multi-strand exports compiling: two strands
that each define an "Idle" mode would otherwise emit two identical
`inline void idle(LedStrand&)` definitions.

A Music Sync design also emits a `music::` namespace of baked `uint8_t`
envelope tables, one per band any strand uses. It sits outside the strand
namespaces because the song belongs to the document, not to one strand.

Call validate_for_export() (or validate_document_for_export()) first and show
its errors: the generators assume a validated config and do not re-check
ranges.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import fill_sources
from .envelope import BAND_LABELS
from .models import (
    AnimationConfig,
    AnimationKind,
    GaugeStopConfig,
    ModeConfig,
    MusicConfig,
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


def validate_for_export(config: StrandConfig, music: MusicConfig | None = None) -> list[str]:
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
                errors.extend(_validate_animation(mode.name, phase.name, phase.animation, music))
                errors.extend(_validate_splice(mode.name, phase.name, phase.splice))
        else:
            errors.extend(_validate_animation(mode.name, None, mode.animation, music))
            errors.extend(_validate_splice(mode.name, None, mode.splice))

    return errors


def validate_document_for_export(
    configs: list[StrandConfig], music: MusicConfig | None = None
) -> list[str]:
    """Validate every strand for a whole-document export.

    Beyond each strand's own checks, names must be distinct: they become the
    namespace names, and two strands called "Strand" would produce `strand`
    and `strand2` with nothing to distinguish them.
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
        errors.extend(f"[{label}] {e}" for e in validate_for_export(cfg, music))
    return errors


def _validate_animation(
    mode_name: str, phase_name: str | None, a: AnimationConfig, music: MusicConfig | None = None
) -> list[str]:
    where = f'mode "{mode_name}"' + (f', phase "{phase_name}"' if phase_name else "")
    errors = []
    if a.kind == AnimationKind.TWINKLE and not a.palette:
        errors.append(f"{where}: Twinkle needs at least one palette color.")
    if a.kind == AnimationKind.MUSIC:
        if music is None or not music.loaded:
            errors.append(
                f"{where}: Music Sync needs a song - load an audio or MIDI file in the "
                f"Song bar under the preview."
            )
        elif not music.bands.get(a.band):
            errors.append(
                f'{where}: the song has nothing in the "{a.band}" band - pick a '
                f"different one, or a different source track."
            )
        if not (0 <= a.sensitivity <= 255):
            errors.append(f"{where}: sensitivity must be 0-255 (got {a.sensitivity}).")
    if a.kind == AnimationKind.FILL:
        source = fill_sources.get(a.source)
        if a.source not in fill_sources.SOURCES:
            errors.append(f'{where}: unknown Fill source "{a.source}".')
        low, high = fill_sources.port_range(a.source)
        if high and not (low <= a.source_port <= high):
            errors.append(
                f"{where}: {source.label} reads a port between {low} and {high} "
                f"(got {a.source_port})."
            )
        if fill_sources.polls_a_device(a.source) and a.source_empty == a.source_full:
            errors.append(
                f"{where}: Fill's Empty At and Full At are both {a.source_empty}, so "
                f"the meter has no range to fill across."
            )
        if not (0 <= a.smoothing <= 99):
            errors.append(f"{where}: smoothing must be 0-99 (got {a.smoothing}).")
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
        if s.needs_overlay():
            errors.extend(_validate_overlay(f"{where}: overlay", s.overlay))
    else:
        if not s.regions:
            errors.append(f"{where}: Custom splice mask needs at least one region.")
        for i, r in enumerate(s.regions):
            if r.width < 1:
                errors.append(f"{where}: splice region {i + 1} width must be >= 1 (got {r.width}).")
            if not (0 <= r.start <= 255):
                errors.append(f"{where}: splice region {i + 1} start must be 0-255 (got {r.start}).")
            errors.extend(_validate_overlay(f"{where}: splice region {i + 1}", r.animation))
    return errors


def _validate_overlay(where: str, o: OverlayAnimationConfig) -> list[str]:
    """The per-kind checks an overlay animation gets, whether it is Split
    mode's shared buffer or one Custom region's own."""
    errors = []
    if o.kind == OverlayAnimationKind.TWINKLE and not o.palette:
        errors.append(f"{where}: Twinkle needs at least one palette color.")
    if o.kind == OverlayAnimationKind.BITSCROLL and o.segment_width < 1:
        errors.append(f"{where}: Bitscroll segment width must be >= 1.")
    errors.extend(_validate_gauge(where, o))
    return errors


def _validate_gauge(where: str, o: OverlayAnimationConfig) -> list[str]:
    """A Gauge region's source checks - the same ones a Fill meter gets, since
    it is the same machinery pointed at a few pixels instead of a strand."""
    if o.kind != OverlayAnimationKind.GAUGE:
        return []
    errors = []
    source = fill_sources.get(o.source)
    if o.source not in fill_sources.SOURCES:
        errors.append(f'{where}: unknown Gauge source "{o.source}".')
    low, high = fill_sources.port_range(o.source)
    if high and not (low <= o.source_port <= high):
        errors.append(
            f"{where}: {source.label} reads a port between {low} and {high} "
            f"(got {o.source_port})."
        )
    if o.source_empty == o.source_full:
        # Unlike a Fill meter this matters even for a hand-driven gauge: the
        # range is what places the color stops, not just what maps a reading.
        errors.append(
            f"{where}: Gauge's Empty At and Full At are both {o.source_empty}, so its "
            f"color scale has no range to spread across."
        )
    if not (0 <= o.smoothing <= 99):
        errors.append(f"{where}: smoothing must be 0-99 (got {o.smoothing}).")
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


def _double(value: float) -> str:
    """A number as an unambiguous C++ double literal.

    levelSource() takes doubles; writing `20` beside `0.5` in one argument list
    obscures that both are the same type.
    """
    text = f"{value:g}"
    return text if ("." in text or "e" in text) else f"{text}.0"


def _palette_literal(colors: list[int]) -> str:
    return "{" + ", ".join(_hex(c) for c in colors) + "}"


# ============================================================================
# Animation / splice call generation
# ============================================================================


@dataclass(frozen=True)
class _MusicRef:
    """How generated code reaches the baked song: the expression naming each
    band's MusicTrack, and the loop flag musicSync() is called with. Both are
    document-level, so they are handed down to every animation, which supplies
    only its own band."""

    exprs: dict[str, str]
    loop: bool

    def expr_for(self, band: str) -> str | None:
        """The table for `band`, or any other emitted one. Mirrors the preview's
        fallback in engine.MusicBinding, so an export can't come out darker than
        the design it was taken from."""
        if band in self.exprs:
            return self.exprs[band]
        return next(iter(self.exprs.values()), None)


@dataclass
class _FillSourceSet:
    """The readers one strand's Fill animations need, collected as its modes
    are generated and emitted together as its `source::` namespace.

    Readers are shared: two meters on the same motor and port are the same
    reading, so they share one function and one device object. Custom hooks are
    the exception - each is assigned separately, so each gets its own.
    """

    namer: _UniqueNamer = field(default_factory=_UniqueNamer)
    #: (source id, port) -> generated function name, for the shared ones.
    _shared: dict[tuple[str, int], str] = field(default_factory=dict)
    #: Rendered body of the `source::` namespace, in emission order.
    lines: list[str] = field(default_factory=list)
    #: PROS headers the emitted readers need.
    includes: set[str] = field(default_factory=set)
    #: Hooks the user has to assign, as (qualified name, what it feeds) - the
    #: usage banner spells out the assignment for each.
    hooks: list[tuple[str, str]] = field(default_factory=list)
    #: Whether any meter is hand-driven, which the banner also mentions.
    has_manual: bool = False

    def reader_for(self, a: AnimationConfig | OverlayAnimationConfig, label: str) -> str | None:
        """The expression naming `a`'s reader, emitting it if it is new.

        Takes either config shape: a Gauge region carries the same source
        fields under the same names as a Fill meter, and shares its readers.

        None for a Manual meter, which has no reader: the export is a
        levelFill() with nothing attached, driven by setLevel() (or
        setRegionLevel() for a gauge).
        """
        source = fill_sources.get(a.source)
        if not fill_sources.polls_a_device(source.id):
            self.has_manual = True
            return None

        if source.id == fill_sources.CUSTOM:
            name = self.namer.make(label, "fillSource")
            self.lines.append(
                f'/// Fill source for "{label}". Assign it before that mode runs -'
            )
            self.lines.append("/// the banner at the top of this file spells out how.")
            self.lines.append(f"inline LedStrand::LevelFn {name} = nullptr;")
            self.hooks.append((name, label))
            return f"source::{name}"

        key = (source.id, a.source_port if source.port_kind else 0)
        if key in self._shared:
            return f"source::{self._shared[key]}"

        suffix = str(a.source_port) if source.port_kind else ""
        name = self.namer.make(f"{source.label} {suffix}", "fillSource")
        self._shared[key] = name
        if source.include:
            self.includes.add(source.include)

        where = f" on port {a.source_port}" if source.port_kind else ""
        self.lines.append(f"/// {source.label}{where}.")
        if source.device:
            # The device is a function-local static so it is constructed once,
            # on the first tick that reads it, rather than per call or during
            # static init before the brain has enumerated its ports.
            self.lines.append(
                f"inline double {name}() {{ static {source.device} device({a.source_port}); "
                f"return device.{source.call}; }}"
            )
        else:
            self.lines.append(f"inline double {name}() {{ return {source.expr}; }}")
        return f"source::{name}"


def _fill_statements(a: AnimationConfig, reader: str | None) -> list[str]:
    """A Fill meter: what it looks like, then what moves it."""
    lines = [
        f"s.levelFill({_hex(a.color)}, {_hex(a.color2)}, {_bool(a.gradient)}, "
        f"{_hex(a.bg_color)}, {_bool(a.invert)});"
    ]
    if reader is not None:
        lines.append(
            f"s.levelSource({reader}, {_double(a.source_empty)}, {_double(a.source_full)}, "
            f"{_bool(a.source_wrap)}, {a.smoothing});"
        )
    return lines


def _animation_statements(
    a: AnimationConfig,
    music: _MusicRef | None = None,
    sources: _FillSourceSet | None = None,
    label: str = "",
) -> list[str]:
    """The call(s) that set one animation up. A list because a Fill meter takes
    two: its colors, then its source."""
    if a.kind == AnimationKind.FILL:
        reader = sources.reader_for(a, label) if sources is not None else None
        return _fill_statements(a, reader)
    return [_animation_statement(a, music)]


def _animation_statement(a: AnimationConfig, music: _MusicRef | None = None) -> str:
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
        return (
            f"s.flow({_hex(a.color)}, {_hex(a.color2)}, {a.speed}, {_bool(a.invert)}, "
            f"{_bool(a.seamless)});"
        )
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
    if a.kind == AnimationKind.MUSIC:
        # validate_for_export() rejects this case; the fallback is here so a
        # caller that skipped validation gets a dark strip rather than a
        # reference to a table that was never emitted.
        expr = music.expr_for(a.band) if music is not None else None
        if expr is None:
            return "s.off();  // Music Sync, but the design has no song loaded"
        return (
            f"s.musicSync({expr}, {_hex(a.color)}, {_hex(a.color2)}, "
            f"{_bool(a.gradient)}, {_hex(a.bg_color)}, {_bool(a.invert)}, "
            f"{a.sensitivity}, {_bool(music.loop)});"
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
        return f"s.overlayFlow({_hex(o.color)}, {_hex(o.color2)}, {o.speed}, {_bool(o.seamless)});"
    if o.kind == OverlayAnimationKind.RAINBOW:
        return f"s.overlayRainbow({o.speed});"
    if o.kind == OverlayAnimationKind.TWINKLE:
        return (
            f"s.overlayTwinkle({_palette_literal(o.palette)}, {o.density_pct}, {o.fade_step}, "
            f"{_hex(o.bg_color)});"
        )
    if o.kind == OverlayAnimationKind.BITSCROLL:
        segment = f"LedStrand::BitScrollSegment{{{_hex(o.color)}, {o.segment_width}}}"
        return (
            f"s.overlayBitscroll({{{segment}}}, {o.speed}, {_bool(o.invert)}, "
            f"{_hex(o.bg_color)}, {o.spacing}, {_bool(o.repeating)});"
        )
    if o.kind == OverlayAnimationKind.GAUGE:
        # The panel does not offer a gauge here (Split's overlay is one shared
        # buffer, so a gauge would be one meter smeared across every masked
        # bin). A hand-edited file can still ask for it; a dark overlay is a
        # better answer than a traceback.
        return "s.overlaySetColor(0x000000);  // Gauge is a Custom-region kind, not an overlay"
    raise ValueError(f"unhandled overlay kind: {o.kind}")


def _stops_literal(stops: list[GaugeStopConfig]) -> str:
    return "{" + ", ".join(f"{{{_double(stop.at)}, {_hex(stop.color)}}}" for stop in stops) + "}"


def _region_literal(r: SpliceRegionConfig, reader: str | None = None) -> str:
    """One SpliceRegion aggregate. Designated initializers, so the fields have
    to stay in LedStrand::SpliceRegion's declaration order - and so a kind that
    ignores a field can simply leave it out."""
    a = r.animation
    kind = f"LedStrand::SpliceRegionAnimKind::{a.kind.value.upper()}"
    head = f".start = {r.start}, .width = {r.width}, .kind = {kind}"

    if a.kind == OverlayAnimationKind.GAUGE:
        parts = [head]
        if not a.stops:
            # Only meaningful as the fallback scale; with stops they are noise.
            parts.append(f".color = {_hex(a.color)}, .color2 = {_hex(a.color2)}")
        parts.append(f".bgColor = {_hex(a.bg_color)}, .invert = {_bool(a.invert)}")
        if reader is not None:
            parts.append(f".read = {reader}")
        parts.append(
            f".emptyAt = {_double(a.source_empty)}, .fullAt = {_double(a.source_full)}, "
            f".wrap = {_bool(a.source_wrap)}, .smoothing = {a.smoothing}, "
            f".style = LedStrand::GaugeStyle::{a.style.value.upper()}, "
            f".blend = LedStrand::GaugeBlend::{a.blend.value.upper()}"
        )
        if a.stops:
            parts.append(f".stops = {_stops_literal(a.stops)}")
        return "{" + ", ".join(parts) + "}"

    if a.kind == OverlayAnimationKind.TWINKLE:
        return (
            f"{{{head}, .bgColor = {_hex(a.bg_color)}, "
            f".palette = {_palette_literal(a.palette)}, "
            f".densityPct = {a.density_pct}, .fadeStep = {a.fade_step}}}"
        )

    if a.kind == OverlayAnimationKind.BITSCROLL:
        return (
            f"{{{head}, .color = {_hex(a.color)}, .bgColor = {_hex(a.bg_color)}, "
            f".speed = {a.speed}, .invert = {_bool(a.invert)}, "
            f".segmentWidth = {a.segment_width}, .spacing = {a.spacing}, "
            f".repeating = {_bool(a.repeating)}}}"
        )

    return (
        f"{{{head}, "
        f".color = {_hex(a.color)}, .color2 = {_hex(a.color2)}, .bgColor = {_hex(a.bg_color)}, "
        f".runLength = {a.run_length}, .speed = {a.speed}, "
        f".onMs = {a.on_ms}, .offMs = {a.off_ms}, .seamless = {_bool(a.seamless)}}}"
    )


def _splice_statements(
    s: SpliceMaskConfig,
    sources: _FillSourceSet | None = None,
    label: str = "",
) -> list[str]:
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

    literals = []
    for i, r in enumerate(s.regions):
        reader = None
        if r.animation.kind == OverlayAnimationKind.GAUGE and sources is not None:
            # Named after the segment rather than the mode, so a Custom hook in
            # the usage banner says which of six gauges it belongs to.
            reader = sources.reader_for(r.animation, f"{label} segment {i + 1}".strip())
        literals.append(_region_literal(r, reader))
    # One region per line. A single-line call was fine for two solid regions,
    # but a row of six gauges with a color scale each runs to thousands of
    # characters, and a generated file still has to be readable.
    return ["s.spliceMaskCustom({", *[f"    {lit}," for lit in literals], "});"]


def _leaf_body(
    a: AnimationConfig,
    splice: SpliceMaskConfig,
    music: _MusicRef | None = None,
    sources: _FillSourceSet | None = None,
    label: str = "",
) -> list[str]:
    return [*_animation_statements(a, music, sources, label), *_splice_statements(splice, sources, label)]


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
    #: This strand's Fill readers: the PROS headers they need, and the hooks
    #: the banner has to tell the user to assign.
    sources: _FillSourceSet = field(default_factory=_FillSourceSet)

# ============================================================================
# Full-file generation
# ============================================================================

# Names that the strand namespace defines itself, a mode constant that sanitized
# to one of these would shadow it.
_RESERVED_MEMBERS = {
    "adiPort", "smartPort", "length", "refreshMs", "brightness",
    "mode", "source", "detail", "modeTable", "profile", "apply",
    "strand",
}


def _constructor_args(config: StrandConfig) -> str:
    """The LedStrand constructor arguments for @p config, named rather than
    numeric so the strand is built from the same constants the rest of its
    namespace exports - change the port in the GUI and there is one place it
    lands. An ADI expander needs the four-argument constructor.
    """
    parts = ["smartPort"] if config.smart_port else []
    parts += ["adiPort", "length", "refreshMs"]
    return ", ".join(parts)


#: The banner marks its copy-paste block with this exact indent; prose asides
#: use a shallower one, so harvesting on it yields code and only code.
_PASTE_PREFIX = "//        "


def paste_block(code: str) -> str:
    """The pasteable lines of @p code's usage banner, comment marker stripped.

    The single reader of that convention: the Deploy dialog shows this and the
    tests compile it, so a banner that stops being valid stops compiling.
    """
    return "\n".join(
        line[len(_PASTE_PREFIX) :]
        for line in code.splitlines()
        if line.startswith(_PASTE_PREFIX)
    )


def _usage_banner(
    entries: list[_StrandRender], header_name: str, music: _MusicRef | None = None
) -> list[str]:
    """The comment block at the top of every export: what to paste into
    main.cpp, with this design's real identifiers and mode names.

    Two indents. `PASTE` lines form one contiguous compilable block, which
    paste_block() harvests and the tests compile. `NOTE` lines are asides (the
    escape hatches, the macro) that would not compile if pasted.
    """
    PASTE = _PASTE_PREFIX
    NOTE = "//    "

    hooks = [(e, name, label) for e in entries for name, label in e.sources.hooks]
    manual = [e for e in entries if e.sources.has_manual]

    lines = [
        "// Generated by HitLib Pattern Studio - edit the source design, not this file.",
        "//",
        "// 1. Drop this file into your PROS project's include/ directory.",
        "// 2. Paste this into main.cpp. Ports, lengths, refresh intervals,",
        "//    brightness and modes all come from the design.",
        "//",
        f'{PASTE}#include "{header_name}"',
        PASTE.rstrip(),
    ]
    width = max(len(e.ns) for e in entries)
    for e in entries:
        lines.append(f"{PASTE}namespace {e.ns:<{width}} = hitlib::profiles::{e.ns};")
    lines.append(PASTE.rstrip())
    lines.append(f"{PASTE}void initialize() {{")
    # A custom Fill source can only be supplied by the robot's own code, so the
    # paste block leaves an assignment to fill in. Placed before begin(), which
    # is when the modes that read them start running.
    for e, name, label in hooks:
        lines.append(
            f"{PASTE}    {e.ns}::source::{name} = [] {{ return 0.0; }};"
            f'   // TODO: "{label}"'
        )
    lines.append(f"{PASTE}    hitlib::studio::begin();")
    lines.append(f"{PASTE}}}")
    lines.append("//")
    lines.append("// begin() registers every strand below, starts its refresh task, attaches")
    lines.append("// its profile and activates its first mode. Calling it twice does nothing,")
    lines.append("// so a re-init path is safe.")
    lines.append("//")
    lines.append("// Switch modes from anywhere - these are ordinary LedStrand objects:")
    lines.append("//")
    lines.append(f"{PASTE}void opcontrol() {{")
    for e in entries:
        lines.append(
            f"{PASTE}    {e.ns}::strand.activateMode({e.ns}::mode::{e.mode_names[0]});"
        )
        # A timed activation only reads as an example when there is a second
        # mode for it to expire back to.
        if len(e.mode_names) > 1:
            lines.append(
                f"{PASTE}    {e.ns}::strand.activateModeTimed({e.ns}::mode::{e.mode_names[1]}, 1500);"
            )
    for e in manual:
        lines.append(f"{PASTE}    {e.ns}::strand.setLevel(128);   // a Manual Fill meter, 0-255")
    lines.append(f"{PASTE}}}")
    lines.append("//")
    for e in entries:
        listed = ", ".join(f"{name} = {i}" for i, name in enumerate(e.mode_names))
        lines.append(f"// Modes - {e.ns}::mode::{{{listed}}}")

    if music is not None:
        lines.append("//")
        lines.append("// The mode that plays the song starts its clock on activation, so")
        lines.append("// activate it when the music starts. setSensitivity() retunes the fill;")
        lines.append("// musicSeek() repositions it.")

    # The two opt-outs, for a codebase that owns its own LED setup.
    lines.append("//")
    lines.append("// To wire these strands up yourself:")
    lines.append("//")
    lines.append(f"{NOTE}hitlib::studio::begin(yourGroup);")
    lines.append("// adds them to a group you own; you keep init(), start() and the interval.")
    lines.append("//")
    lines.append(f"{NOTE}#define HITLIB_STUDIO_NO_AUTOWIRE")
    lines.append("// before the #include defines no strands and no group, leaving only the")
    lines.append("// profile and the constants above to build your own LedStrand from.")
    return lines


def _render_strand(
    config: StrandConfig, ns_namer: _UniqueNamer, music: _MusicRef | None = None
) -> _StrandRender:
    modes = _effective_modes(config)
    display_name = config.name or "Profile"
    ns = ns_namer.make(display_name, "profile")

    fn_namer = _UniqueNamer()
    mode_namer = _UniqueNamer()
    for reserved in sorted(_RESERVED_MEMBERS):
        mode_namer.make(reserved, "mode")

    sources = _FillSourceSet()
    function_blocks: list[str] = []
    mode_entries: list[str] = []
    mode_names: list[str] = []

    for mode in modes:
        activate_fn, tick_fn, blocks = _generate_mode(mode, fn_namer, music, sources)
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
    if sources.lines:
        lines.append("// --- Fill sources: the readings this design's meters follow ---")
        lines.append("namespace source {")
        lines.extend(sources.lines)
        lines.append("}  // namespace source")
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
    lines.append("#ifndef HITLIB_STUDIO_NO_AUTOWIRE")
    lines.append("/// The strand itself, built from the hardware constants above. An")
    lines.append("/// ordinary LedStrand: hitlib::studio::begin() starts it, and every API")
    lines.append("/// call works on it directly.")
    lines.append(f"inline LedStrand strand{{{_constructor_args(config)}}};")
    lines.append("#endif")
    lines.append("")
    lines.append(f"}}  // namespace {ns}")

    return _StrandRender(
        config=config,
        ns=ns,
        var=f"{ns}Strand",
        mode_names=mode_names,
        lines=lines,
        sources=sources,
    )


def _render_studio(entries: list[_StrandRender]) -> list[str]:
    """The `hitlib::studio` namespace: the groups this design runs on and the
    begin() that starts them.

    Every value begin() needs - port, length, interval, brightness, first mode
    - is already in the namespaces above, so the robot only includes the file
    and calls begin().

    Strands are bucketed by refresh interval. A group ticks everything it owns
    at one rate, and a strand converts flash durations to ticks with its own
    interval, so strands at different rates cannot share a group.
    """
    buckets: dict[int, list[_StrandRender]] = {}
    for e in entries:
        buckets.setdefault(e.config.refresh_ms, []).append(e)
    intervals = list(buckets)
    split = len(intervals) > 1

    def qualified(e: _StrandRender) -> str:
        return f"profiles::{e.ns}::strand"

    def wire(e: _StrandRender) -> list[str]:
        """Attach one strand's profile and bring it up in its first mode."""
        return [
            f"    profiles::{e.ns}::apply({qualified(e)});",
            f"    {qualified(e)}.activateMode(profiles::{e.ns}::mode::{e.mode_names[0]});",
        ]

    lines = [
        "#ifndef HITLIB_STUDIO_NO_AUTOWIRE",
        "",
        "/// This design, ready to run - see the banner at the top of this file.",
        "namespace hitlib::studio {",
        "",
    ]
    if split:
        lines.append("// One refresh task per interval in the design: a group ticks every strand")
        lines.append("// it owns at a single rate, and these strands were designed at different")
        lines.append("// ones. They are still one begin() call.")
    else:
        lines.append(f"// The refresh task this design's strands run on, at {intervals[0]} ms.")
    lines.append(f"inline LedGroup groups[{len(intervals)}];")
    lines.append("")
    lines.append("/// The first group, and the only one when every strand in the design")
    lines.append("/// shares a refresh interval.")
    lines.append("inline LedGroup& group = groups[0];")
    lines.append("")
    lines.append("/// Registers every strand in this design, initializes and starts it,")
    lines.append("/// attaches its profile and activates its first mode. Call once from")
    lines.append("/// initialize(); a second call does nothing, so a re-init path is safe.")
    lines.append("inline void begin() {")
    lines.append("    static bool started = false;")
    lines.append("    if (started) return;")
    lines.append("    started = true;")
    lines.append("")
    for i, ms in enumerate(intervals):
        for e in buckets[ms]:
            lines.append(f"    groups[{i}].add(&{qualified(e)});")
        lines.append(f"    groups[{i}].init({ms});")
        lines.append(f"    groups[{i}].start();")
    lines.append("")
    for e in entries:
        lines.extend(wire(e))
    lines.append("}")
    lines.append("")
    lines.append("/// Adds this design's strands to a group your own code owns and attaches")
    lines.append("/// their profiles, leaving init(), start() and the refresh interval to")
    lines.append("/// you. Call it before or after your own init(); order does not matter.")
    if split:
        lines.append("/// Note: these strands were designed at different refresh intervals, and")
        lines.append("/// one group runs one. Pick the interval the flash timings matter at, or")
        lines.append("/// use the no-argument begin() instead.")
    lines.append("inline void begin(LedGroup& existing) {")
    for e in entries:
        lines.append(f"    existing.add(&{qualified(e)});")
    lines.append("")
    for e in entries:
        lines.extend(wire(e))
    lines.append("}")
    lines.append("")
    lines.append("}  // namespace hitlib::studio")
    lines.append("")
    lines.append("#endif  // HITLIB_STUDIO_NO_AUTOWIRE")
    return lines


#: Sample table line width. Twelve three-digit values plus separators sit just
#: inside 80 columns, so the emitted array stays readable in a normal editor.
_SAMPLES_PER_LINE = 12


def _music_bands_used(configs: list[StrandConfig]) -> list[str]:
    """Bands any exported Music Sync animation asks for, in first-seen order.

    Only these get a table emitted: the tables are the bulk of the generated
    file, and a design that pumps on the bass has no use for three more.
    """
    used: list[str] = []
    for config in configs:
        for mode in _effective_modes(config):
            leaves = [p.animation for p in mode.phases] if mode.phases else [mode.animation]
            for a in leaves:
                if a.kind == AnimationKind.MUSIC and a.band not in used:
                    used.append(a.band)
    return used


def _render_music(music: MusicConfig, bands: list[str]) -> tuple[_MusicRef, list[str]]:
    """The `music::` namespace holding one baked envelope per band in use, and
    the references animations use to reach them."""
    namer = _UniqueNamer()
    exprs: dict[str, str] = {}
    tables: list[tuple[str, str, str, list[int]]] = []
    for band in bands:
        table = music.table(band)
        if not table:
            continue
        var = namer.make(f"{music.name} {band}", "song")
        samples_var = namer.make(f"{music.name} {band} samples", "songSamples")
        exprs[band] = f"music::{var}"
        tables.append((band, var, samples_var, table))

    total_ms = music.duration_ms
    kb = sum(len(t) for *_, t in tables) / 1024
    lines = [
        "/// The song this design syncs to, analysed and baked by Pattern Studio.",
        "///",
        f"/// {total_ms // 60000}:{total_ms // 1000 % 60:02d} at {music.frame_ms} ms per frame,"
        f" {len(tables)} band table(s), about {kb:.1f} KB.",
        "/// Playback is driven by the wall clock from the moment musicSync() runs,",
        "/// so activate the mode when the music starts.",
        "namespace music {",
    ]
    for band, var, samples_var, table in tables:
        lines.append("")
        lines.append(f"// --- {BAND_LABELS.get(band, band)} ---")
        lines.append(f"inline const uint8_t {samples_var}[] = {{")
        for start in range(0, len(table), _SAMPLES_PER_LINE):
            row = table[start : start + _SAMPLES_PER_LINE]
            lines.append("    " + " ".join(f"{v:3d}," for v in row))
        lines.append("};")
        lines.append(
            f"inline const LedStrand::MusicTrack {var} = "
            f"{{{samples_var}, {len(table)}, {music.frame_ms}}};"
        )
    lines.append("")
    lines.append("}  // namespace music")
    return _MusicRef(exprs=exprs, loop=music.loop), lines


def _render_file(
    configs: list[StrandConfig], header_name: str | None, music: MusicConfig | None = None
) -> str:
    single = len(configs) == 1
    ns_namer = _UniqueNamer()

    music_ref: _MusicRef | None = None
    music_lines: list[str] = []
    bands_used = _music_bands_used(configs)
    if music is not None and music.loaded and bands_used:
        music_ref, music_lines = _render_music(music, bands_used)
        if not music_ref.exprs:
            music_ref, music_lines = None, []

    entries = [_render_strand(cfg, ns_namer, music_ref) for cfg in configs]

    default_name = suggested_header_name(configs[0].name) if single else "led_profiles.hpp"

    lines: list[str] = ["#pragma once", ""]
    lines.extend(_usage_banner(entries, header_name or default_name, music_ref))
    lines.append("")
    lines.append('#include "hitlib/led_group.hpp"')
    lines.append('#include "hitlib/led_profile.hpp"')
    lines.append('#include "hitlib/led_sequencer.hpp"')
    lines.append('#include "hitlib/led_strand.hpp"')
    device_includes = sorted({inc for e in entries for inc in e.sources.includes})
    if device_includes:
        lines.append("")
        lines.append("// Devices this design's Fill meters and Gauges read")
        for include in device_includes:
            lines.append(f'#include "{include}"')
    lines.append("")
    lines.append("namespace hitlib::profiles {")
    lines.append("")
    if music_lines:
        lines.extend(music_lines)
        lines.append("")
    for entry in entries:
        lines.extend(entry.lines)
        lines.append("")
    lines.append("}  // namespace hitlib::profiles")
    lines.append("")
    lines.extend(_render_studio(entries))
    lines.append("")
    return "\n".join(lines)


def suggested_header_name(strand_name: str) -> str:
    """Default filename to offer for a single-strand export: "My Robot" ->
    my_robot.hpp."""
    return _snake_case(strand_name or "profile", "profile") + ".hpp"


def generate_cpp(
    config: StrandConfig, header_name: str | None = None, music: MusicConfig | None = None
) -> str:
    """Render one strand as a standalone header.

    @p header_name is the filename the export will be saved as, it only
    feeds the #include line in the usage banner. @p music is the design's
    song, needed only when the strand actually has a Music Sync animation.
    """
    return _render_file([config], header_name, music)


def generate_document_cpp(
    configs: list[StrandConfig], header_name: str | None = None,
    music: MusicConfig | None = None,
) -> str:
    """Render every strand in the document into a single header, each in its
    own namespace.

    One file means one namespace namer, so two strands that both have an
    "Idle" mode cannot generate colliding identifiers. Separately exported
    files have no such shared namer.
    """
    if not configs:
        raise ValueError("no strands to export")
    return _render_file(configs, header_name, music)


def _generate_mode(
    mode: ModeConfig,
    namer: _UniqueNamer,
    music: _MusicRef | None = None,
    sources: _FillSourceSet | None = None,
) -> tuple[str, str | None, list[str]]:
    blocks: list[str] = []

    if not mode.phases:
        activate_fn = namer.make(mode.name, "mode")
        body = "\n".join(
            f"    {line}"
            for line in _leaf_body(mode.animation, mode.splice, music, sources, mode.name)
        )
        blocks.append(f"inline void {activate_fn}(LedStrand& s) {{\n{body}\n}}")
        return activate_fn, None, blocks

    phase_fn_names = []
    for phase in mode.phases:
        fn_name = namer.make(f"{mode.name} {phase.name}", "phase")
        label = f"{mode.name} {phase.name}"
        body = "\n".join(
            f"    {line}"
            for line in _leaf_body(phase.animation, phase.splice, music, sources, label)
        )
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
