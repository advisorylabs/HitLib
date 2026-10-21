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

Every generator takes a Platform. PROS gets C++17/20 (inline variables,
designated initializers, nested namespace names). VEXcode compiles C++11, so
its export says the same things the long way round: each object the header
shares between files is a static member of a class template reached through a
reference of the same name (see _variable()), splice regions are filled in
field by field, and namespaces nest one level per line. Robot code written
against either export reads identically.

Call validate_for_export() (or validate_document_for_export()) first and show
its errors: the generators assume a validated config and do not re-check
ranges.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import codegen_custom, fill_sources
from .envelope import BAND_LABELS
from .frame_codec import MAX_DRAWN_FRAMES
from .platforms import Platform
from .models import (
    AnimationConfig,
    AnimationKind,
    CompositionConfig,
    DriverKind,
    LayerKind,
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


def validate_for_export(config: StrandConfig, music: MusicConfig | None = None,
                        compositions: list[CompositionConfig] | None = None) -> list[str]:
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
                errors.extend(_validate_animation(mode.name, phase.name, phase.animation,
                                                  music, compositions))
                errors.extend(_validate_splice(mode.name, phase.name, phase.splice))
        else:
            errors.extend(_validate_animation(mode.name, None, mode.animation,
                                              music, compositions))
            errors.extend(_validate_splice(mode.name, None, mode.splice))

    return errors


def validate_document_for_export(
    configs: list[StrandConfig], music: MusicConfig | None = None,
    compositions: list[CompositionConfig] | None = None
) -> list[str]:
    """Validate every strand for a whole-document export.

    Beyond each strand's own checks, names must be distinct: they become the
    namespace names, and two strands called "Strand" would produce `strand`
    and `strand2` with nothing to distinguish them. Ports must be too: the
    preview draws two strips, but two LedStrands on one port overwrite each
    other's frames every tick.
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

    on_port: dict[tuple[int, int], list[str]] = {}
    for cfg in configs:
        on_port.setdefault((cfg.smart_port, cfg.adi_port), []).append(cfg.name or "Profile")
    for (smart, adi), names in on_port.items():
        if len(names) > 1:
            port = f"ADI port {adi}" + (f" of the expander on smart port {smart}" if smart else "")
            listed = ", ".join(f'"{n}"' for n in names[:-1]) + f' and "{names[-1]}"'
            errors.append(
                f"{listed} are all on {port}, so on the robot they would overwrite "
                f"each other's frames - give each strand a port of its own."
            )

    for cfg in configs:
        label = cfg.name or "Profile"
        errors.extend(f"[{label}] {e}" for e in validate_for_export(cfg, music, compositions))
    return errors


def _validate_animation(
    mode_name: str, phase_name: str | None, a: AnimationConfig,
    music: MusicConfig | None = None,
    compositions: list[CompositionConfig] | None = None,
) -> list[str]:
    where = f'mode "{mode_name}"' + (f', phase "{phase_name}"' if phase_name else "")
    errors = []
    if a.kind == AnimationKind.CUSTOM:
        library = {c.id: c for c in (compositions or [])}
        comp = library.get(a.composition_id)
        if comp is None:
            errors.append(
                f"{where}: this strand plays a custom animation that is not in the "
                f"library any more - pick one in the Animator, or change the "
                f"animation kind."
            )
        else:
            errors.extend(_validate_composition(where, comp, music))
    if a.kind == AnimationKind.TWINKLE and not a.palette:
        errors.append(f"{where}: Twinkle needs at least one palette color.")
    if a.kind == AnimationKind.MUSIC:
        errors.extend(_validate_song(where, "Music Sync", a.band, music))
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
    if not (0 <= a.step_ms <= 0xFFFF):
        errors.append(f"{where}: step time must fit in 0-65535 ms (got {a.step_ms}).")
    # Flash durations are milliseconds, not uint8 counts. They get their own
    # bound (uint32 on the C++ side, but the GUI caps at 10 s).
    for label, value in (("on_ms", a.on_ms), ("off_ms", a.off_ms)):
        if value < 1:
            errors.append(f"{where}: {label} must be >= 1 ms (got {value}).")
    return errors


#: LedStrand::MusicTrack::frameCount is a uint16_t, and a longer table is a
#: narrowing error in the brace initializer that emits it.
MAX_MUSIC_FRAMES = 0xFFFF


def _validate_song(where: str, what: str, band: str, music: MusicConfig | None) -> list[str]:
    """What `band` of the design's song has to be for @p what - a Music Sync
    animation or a composition's Song Band driver - to play it on the robot."""
    if music is None or not music.loaded:
        return [
            f"{where}: {what} needs a song - load an audio or MIDI file in the "
            f"Song bar under the preview."
        ]
    if not music.bands.get(band):
        return [
            f'{where}: the song has nothing in the "{band}" band - pick a '
            f"different one, or a different source track."
        ]
    frames = len(music.table(band))
    if frames > MAX_MUSIC_FRAMES:
        return [
            f"{where}: the song is {frames} frames of {music.frame_ms} ms, and the "
            f"robot's table holds at most {MAX_MUSIC_FRAMES} - raise the Frame time "
            f"in the Song bar, or load a shorter clip."
        ]
    return []



def _validate_composition(where: str, comp: CompositionConfig,
                          music: MusicConfig | None = None) -> list[str]:
    """What the firmware cannot be handed, checked before it is emitted.

    The bounds are LedStrand's own static constexpr ceilings. Going past one
    is caught here rather than silently truncated at compose(), because a
    design that loses its eighth layer on export and nowhere else is the
    worst way to find out.
    """
    from .models import MAX_BINDINGS, MAX_DRIVERS, MAX_LAYERS, CURVE_POINTS

    errors: list[str] = []
    label = f'{where}: custom animation "{comp.name}"'

    live = [layer for layer in comp.layers if not layer.muted]
    if not live:
        errors.append(f"{label} has no layers that are not muted, so it renders as nothing.")
    if len(live) > MAX_LAYERS:
        errors.append(
            f"{label} has {len(live)} layers, and a strand can run {MAX_LAYERS}. "
            f"Mute or remove {len(live) - MAX_LAYERS} of them."
        )
    if len(comp.drivers) > MAX_DRIVERS:
        errors.append(
            f"{label} has {len(comp.drivers)} drivers, and a strand can run {MAX_DRIVERS}."
        )

    for i, layer in enumerate(live):
        lwhere = f'{label}, layer "{layer.name or i}"'
        if len(layer.bindings) > MAX_BINDINGS:
            errors.append(
                f"{lwhere}: {len(layer.bindings)} bindings, and a layer can carry "
                f"{MAX_BINDINGS}."
            )
        for b in layer.bindings:
            if b.driver >= len(comp.drivers):
                errors.append(
                    f"{lwhere}: a binding points at driver {b.driver}, which this "
                    f"animation does not have."
                )
            if b.curve and len(b.curve) != CURVE_POINTS:
                errors.append(
                    f"{lwhere}: a binding curve has {len(b.curve)} points, and a "
                    f"curve has exactly {CURVE_POINTS}."
                )
        if layer.kind == LayerKind.TWINKLE and not layer.palette:
            errors.append(f"{lwhere}: Twinkle needs at least one palette color.")
        if layer.kind == LayerKind.BITSCROLL:
            if not layer.segments:
                errors.append(f"{lwhere}: Bitscroll needs at least one segment.")
            for seg in layer.segments:
                if seg.width < 1:
                    errors.append(f"{lwhere}: a Bitscroll segment width must be >= 1.")
        if layer.kind == LayerKind.FRAMES:
            if not layer.frames:
                errors.append(f"{lwhere}: a Drawn Frames layer has nothing drawn in it.")
            if len(layer.frames) > MAX_DRAWN_FRAMES:
                errors.append(
                    f"{lwhere}: {len(layer.frames)} frames, and a drawn layer can hold "
                    f"{MAX_DRAWN_FRAMES}. Delete {len(layer.frames) - MAX_DRAWN_FRAMES} of them."
                )
            if layer.frame_width < 1:
                errors.append(f"{lwhere}: frame width must be >= 1.")
            if len(layer.frame_palette) > 32:
                errors.append(
                    f"{lwhere}: {len(layer.frame_palette)} palette colors, and a "
                    f"drawn frame's index is five bits, so 32 is the most it can name."
                )
        if not (0 <= layer.opacity <= 255):
            errors.append(f"{lwhere}: opacity must be 0-255 (got {layer.opacity}).")
        if layer.start >= MAX_LEDS:
            errors.append(f"{lwhere}: start pixel must be below {MAX_LEDS}.")

    for i, drv in enumerate(comp.drivers):
        dwhere = f'{label}, driver "{drv.name or i}"'
        if drv.kind == DriverKind.SENSOR:
            if drv.source not in fill_sources.SOURCES:
                errors.append(f'{dwhere}: unknown source "{drv.source}".')
            else:
                low, high = fill_sources.port_range(drv.source)
                if high and not (low <= drv.source_port <= high):
                    errors.append(
                        f"{dwhere}: reads a port between {low} and {high} "
                        f"(got {drv.source_port})."
                    )
                if (fill_sources.polls_a_device(drv.source)
                        and drv.source_empty == drv.source_full):
                    errors.append(
                        f"{dwhere}: Empty At and Full At are both {drv.source_empty}, "
                        f"so it has no range to move across."
                    )
                if not fill_sources.polls_a_device(drv.source):
                    # The preview sweeps it like any other, but on the robot
                    # a Sensor driver with no reader holds still, and
                    # setDriverLevel() only moves a Constant one.
                    errors.append(
                        f"{dwhere}: a Sensor driver on Manual has nothing to read on "
                        f"the robot, so it would never move there. Pick a source, or "
                        f"make it a Constant driver and set it from robot code with "
                        f"setDriverLevel()."
                    )
            if not (0 <= drv.smoothing <= 99):
                errors.append(f"{dwhere}: smoothing must be 0-99 (got {drv.smoothing}).")
        if drv.kind == DriverKind.MUSIC:
            errors.extend(_validate_song(dwhere, "a Song Band driver", drv.band, music))
        if drv.kind == DriverKind.LFO and drv.period_ms < 1:
            errors.append(f"{dwhere}: an oscillator's period must be >= 1 ms.")
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


#: Namespaces the generated code reaches, or that a header in the same file
#: defines at hitlib::profiles. A strand or composition namespace of the same
#: name would shadow one of them for everything nested beside it.
_OWNED_NAMESPACES = frozenset({"pros", "vex", "std", "hitlib", "music", "anim"})

#: Names a strand's namespace cannot take because the banner's paste block
#: aliases it at global scope in main.cpp, where these already mean something.
#: Chosen once for both platforms, so one design names its strands the same
#: whichever it is exported for.
_MAIN_CPP_NAMES = frozenset({
    # Entry points: PROS's, then the VEXcode competition template's.
    "initialize", "disabled", "autonomous", "opcontrol",
    "usercontrol", "main", "vexcodeInit",
    # The vex:: classes and enums _camel_case() can spell. A VEXcode main.cpp
    # opens with `using namespace vex;`, which makes `led::strand` ambiguous
    # between the alias and vex::led.
    "accelerometer", "analogUnits", "axisType", "brain", "brakeType", "bumper",
    "color", "competition", "controller", "controllerType", "currentUnits",
    "device", "devices", "directionType", "distance", "distanceUnits",
    "drivetrain", "electromagnet", "encoder", "event", "fontType", "gearSetting",
    "gestureType", "gps", "guido", "gyro", "inertial", "led", "ledState",
    "light", "limit", "line", "linkType", "mevent", "motor", "motor29", "mutex",
    "optical", "orientationType", "percentUnits", "pneumatics", "pot", "potV2",
    "powerUnits", "rotation", "rotationUnits", "safearray", "semaphore",
    "servo", "sizeType", "smartdrive", "sonar", "task", "temperatureUnits",
    "thread", "timeUnits", "timer", "torqueUnits", "triport", "triportType",
    "turnType", "velocityUnits", "vexlink", "vision", "voltageUnits",
    # The C library every main.cpp sees, where a strand could plausibly be
    # named the same thing ("Signal", "Time").
    "abort", "abs", "clock", "div", "exit", "floor", "free", "index", "kill",
    "log", "raise", "rand", "random", "remove", "rename", "round", "select",
    "signal", "system", "time", "timespec", "uint", "ulong", "ushort",
})


class _UniqueNamer:
    def __init__(self, reserved=()):
        #: Seeded with names that are taken before anything is generated, so
        #: the first strand to want one gets the "2" instead.
        self._used: set[str] = set(reserved)

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


def _speed(pixels: int, step_ms: int) -> str:
    """A LedStrand::Speed argument. A bare number is pixels per refresh tick,
    the only thing `speed` could say before it could go slower, so a design
    that never slows anything down exports exactly what it always did."""
    if not step_ms:
        return str(pixels)
    if pixels == 1:
        return f"LedStrand::Speed::everyMs({step_ms})"
    return f"LedStrand::Speed::everyMs({step_ms}, {pixels})"


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
# Dialect
# ============================================================================


def _variable(platform: Platform, type_: str, name: str, init: str = "", count: int | None = None) -> str:
    """A namespace-scope object defined in the header, and shared by every
    file that includes it.

    @p init is everything after the declarator - `" = {...}"`, `"{a, b}"`,
    `"(a, b)"` - or empty for default construction. @p count makes it an
    array of that many elements.

    PROS has inline variables. C++11 does not, and a plain definition in a
    header is a duplicate symbol in every file past the first. The C++11 form
    defines the object as a static data member of a class template instead,
    which the linker keeps one copy of, and binds a same-named reference to it
    so everything else in the file, and the robot's own code, spells it the
    same way on both platforms. A reference to an array needs its bound, so
    VEXcode arrays always carry one.
    """
    if platform is Platform.PROS:
        bound = "" if count is None else ("[]" if init else f"[{count}]")
        return f"inline {type_} {name}{bound}{init};"

    bound = "" if count is None else f"[{count}]"
    holder = f"{name}_"
    alias = f" (&{name}){bound}" if count is not None else f"& {name}"
    return (
        f"template <typename = void> struct {holder} {{ static {type_} value{bound}; }};\n"
        f"template <typename T> {type_} {holder}<T>::value{bound}{init};\n"
        f"static {type_}{alias} = {holder}<>::value;"
    )


def _namespace_open(platform: Platform, name: str) -> str:
    """`namespace a::b {`, which C++11 has to write one level at a time."""
    if platform is Platform.PROS:
        return f"namespace {name} {{"
    return " ".join(f"namespace {part} {{" for part in name.split("::"))


def _namespace_close(platform: Platform, name: str) -> str:
    braces = "}" if platform is Platform.PROS else "}" * len(name.split("::"))
    return f"{braces}  // namespace {name}"


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
    are generated and emitted together as its `source::` namespace. Each
    composition gets one of its own the same way, for its Sensor drivers:
    a `source::` name only resolves inside the namespace that emitted it.

    Readers are shared: two meters on the same motor and port are the same
    reading, so they share one function and one device object. Custom hooks are
    the exception - each is assigned separately, so each gets its own.
    """

    platform: Platform = Platform.PROS
    namer: _UniqueNamer = field(default_factory=_UniqueNamer)
    #: (source id, port) -> generated function name, for the shared ones.
    _shared: dict[tuple[str, int], str] = field(default_factory=dict)
    #: Rendered body of the `source::` namespace, in emission order.
    lines: list[str] = field(default_factory=list)
    #: PROS or VEX SDK headers the emitted readers need.
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
                f'/// Fill source for "{_escape(label)}". Assign it before that mode runs -'
            )
            self.lines.append("/// the banner at the top of this file spells out how.")
            self.lines.append(_variable(self.platform, "LedStrand::LevelFn", name, " = nullptr"))
            self.hooks.append((name, label))
            return f"source::{name}"

        key = (source.id, a.source_port if source.port_kind else 0)
        if key in self._shared:
            return f"source::{self._shared[key]}"

        suffix = str(a.source_port) if source.port_kind else ""
        name = self.namer.make(f"{source.label} {suffix}", "fillSource")
        self._shared[key] = name

        where = f" on port {a.source_port}" if source.port_kind else ""
        self.lines.append(f"/// {source.label}{where}.")
        if self.platform is Platform.VEXCODE:
            self.includes.update(fill_sources.VEX_INCLUDES)
            # Set up in function-local statics, for the same reason as below.
            setup = source.vex_setup.format(port=a.source_port, index=a.source_port - 1)
            body = f"{setup} return {source.vex_read};" if setup else f"return {source.vex_read};"
            self.lines.append(f"inline double {name}() {{ {body} }}")
            return f"source::{name}"

        if source.include:
            self.includes.add(source.include)
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
    comps: dict[int, str] | None = None,
) -> list[str]:
    """The call(s) that set one animation up. A list because a Fill meter takes
    two: its colors, then its source."""
    if a.kind == AnimationKind.FILL:
        reader = sources.reader_for(a, label) if sources is not None else None
        return _fill_statements(a, reader)
    return [_animation_statement(a, music, comps)]


def _animation_statement(a: AnimationConfig, music: _MusicRef | None = None,
                         comps: dict[int, str] | None = None) -> str:
    if a.kind == AnimationKind.OFF:
        return "s.off();"
    if a.kind == AnimationKind.SOLID:
        return f"s.setColor({_hex(a.color)});"
    if a.kind == AnimationKind.PULSE:
        return (
            f"s.pulse({_hex(a.color)}, {a.run_length}, {_speed(a.speed, a.step_ms)}, "
            f"{_hex(a.bg_color)}, "
            f"{_bool(a.invert)}, {_bool(a.bounce)});"
        )
    if a.kind == AnimationKind.FLASH:
        return f"s.flash({_hex(a.color)}, {a.on_ms}, {a.off_ms}, {_hex(a.bg_color)});"
    if a.kind == AnimationKind.FLOW:
        return (
            f"s.flow({_hex(a.color)}, {_hex(a.color2)}, {_speed(a.speed, a.step_ms)}, "
            f"{_bool(a.invert)}, "
            f"{_bool(a.seamless)});"
        )
    if a.kind == AnimationKind.RAINBOW:
        return f"s.rainbow({_speed(a.speed, a.step_ms)});"
    if a.kind == AnimationKind.TWINKLE:
        return (
            f"s.twinkle({_palette_literal(a.palette)}, {a.density_pct}, {a.fade_step}, {_hex(a.bg_color)});"
        )
    if a.kind == AnimationKind.BITSCROLL:
        segment = f"LedStrand::BitScrollSegment{{{_hex(a.color)}, {a.segment_width}}}"
        return (
            f"s.bitscroll({{{segment}}}, {_speed(a.speed, a.step_ms)}, {_bool(a.invert)}, "
            f"{_hex(a.bg_color)}, "
            f"{_bool(a.bounce)}, {a.spacing}, {_bool(a.repeating)});"
        )
    if a.kind == AnimationKind.CUSTOM:
        # validate_for_export() rejects a dangling id; the fallback keeps a
        # caller that skipped validation from naming a namespace that was
        # never emitted.
        name = (comps or {}).get(a.composition_id)
        if name is None:
            return "s.off();  // Custom Animation, but the design has no such composition"
        return f"s.compose(anim::{name}::composition);"
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
        return (f"s.overlayPulse({_hex(o.color)}, {o.run_length}, "
                f"{_speed(o.speed, o.step_ms)}, {_hex(o.bg_color)});")
    if o.kind == OverlayAnimationKind.FLASH:
        return f"s.overlayFlash({_hex(o.color)}, {o.on_ms}, {o.off_ms}, {_hex(o.bg_color)});"
    if o.kind == OverlayAnimationKind.FLOW:
        return (f"s.overlayFlow({_hex(o.color)}, {_hex(o.color2)}, "
                f"{_speed(o.speed, o.step_ms)}, {_bool(o.seamless)});")
    if o.kind == OverlayAnimationKind.RAINBOW:
        return f"s.overlayRainbow({_speed(o.speed, o.step_ms)});"
    if o.kind == OverlayAnimationKind.TWINKLE:
        return (
            f"s.overlayTwinkle({_palette_literal(o.palette)}, {o.density_pct}, {o.fade_step}, "
            f"{_hex(o.bg_color)});"
        )
    if o.kind == OverlayAnimationKind.BITSCROLL:
        segment = f"LedStrand::BitScrollSegment{{{_hex(o.color)}, {o.segment_width}}}"
        return (
            f"s.overlayBitscroll({{{segment}}}, {_speed(o.speed, o.step_ms)}, {_bool(o.invert)}, "
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


def _region_fields(r: SpliceRegionConfig, reader: str | None = None) -> list[tuple[str, str]]:
    """The SpliceRegion fields one region sets, as (field, value), in
    LedStrand::SpliceRegion's declaration order - which designated initializers
    require. A kind that ignores a field simply leaves it out."""
    a = r.animation
    kind = f"LedStrand::SpliceRegionAnimKind::{a.kind.value.upper()}"
    fields = [("start", str(r.start)), ("width", str(r.width)), ("kind", kind)]

    if a.kind == OverlayAnimationKind.GAUGE:
        if not a.stops:
            # Only meaningful as the fallback scale; with stops they are noise.
            fields += [("color", _hex(a.color)), ("color2", _hex(a.color2))]
        fields += [("bgColor", _hex(a.bg_color)), ("invert", _bool(a.invert))]
        if reader is not None:
            fields.append(("read", reader))
        fields += [
            ("emptyAt", _double(a.source_empty)),
            ("fullAt", _double(a.source_full)),
            ("wrap", _bool(a.source_wrap)),
            ("smoothing", str(a.smoothing)),
            ("style", f"LedStrand::GaugeStyle::{a.style.value.upper()}"),
            ("blend", f"LedStrand::GaugeBlend::{a.blend.value.upper()}"),
        ]
        if a.stops:
            fields.append(("stops", _stops_literal(a.stops)))
        return fields

    if a.kind == OverlayAnimationKind.TWINKLE:
        return fields + [
            ("bgColor", _hex(a.bg_color)),
            ("palette", _palette_literal(a.palette)),
            ("densityPct", str(a.density_pct)),
            ("fadeStep", str(a.fade_step)),
        ]

    if a.kind == OverlayAnimationKind.BITSCROLL:
        return fields + [
            ("color", _hex(a.color)),
            ("bgColor", _hex(a.bg_color)),
            ("speed", _speed(a.speed, a.step_ms)),
            ("invert", _bool(a.invert)),
            ("segmentWidth", str(a.segment_width)),
            ("spacing", str(a.spacing)),
            ("repeating", _bool(a.repeating)),
        ]

    return fields + [
        ("color", _hex(a.color)),
        ("color2", _hex(a.color2)),
        ("bgColor", _hex(a.bg_color)),
        ("runLength", str(a.run_length)),
        ("speed", _speed(a.speed, a.step_ms)),
        ("onMs", str(a.on_ms)),
        ("offMs", str(a.off_ms)),
        ("seamless", _bool(a.seamless)),
    ]


def _region_literal(r: SpliceRegionConfig, reader: str | None = None) -> str:
    """One SpliceRegion aggregate, with designated initializers."""
    return "{" + ", ".join(f".{name} = {value}" for name, value in _region_fields(r, reader)) + "}"


def _splice_statements(
    s: SpliceMaskConfig,
    sources: _FillSourceSet | None = None,
    label: str = "",
    platform: Platform = Platform.PROS,
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

    readers: list[str | None] = []
    for i, r in enumerate(s.regions):
        reader = None
        if r.animation.kind == OverlayAnimationKind.GAUGE and sources is not None:
            # Named after the segment rather than the mode, so a Custom hook in
            # the usage banner says which of six gauges it belongs to.
            reader = sources.reader_for(r.animation, f"{label} segment {i + 1}".strip())
        readers.append(reader)

    if platform is Platform.VEXCODE:
        # SpliceRegion has default member values, which in C++11 means it is
        # not an aggregate, so no brace form constructs one. Fill each in.
        # Named from the global namespace, as the SDK readers are, so nothing
        # the design names can shadow it.
        lines = [f"::std::vector<LedStrand::SpliceRegion> regions({len(s.regions)});"]
        for i, (r, reader) in enumerate(zip(s.regions, readers)):
            lines += [f"regions[{i}].{name} = {value};" for name, value in _region_fields(r, reader)]
        lines.append("s.spliceMaskCustom(regions);")
        return lines

    literals = [_region_literal(r, reader) for r, reader in zip(s.regions, readers)]
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
    platform: Platform = Platform.PROS,
    comps: dict[int, str] | None = None,
) -> list[str]:
    return [
        *_animation_statements(a, music, sources, label, comps),
        *_splice_statements(splice, sources, label, platform),
    ]


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
    entries: list[_StrandRender],
    header_name: str,
    music: _MusicRef | None = None,
    platform: Platform = Platform.PROS,
    comp_hooks: list[tuple[str, str]] | None = None,
) -> list[str]:
    """The comment block at the top of every export: what to paste into
    main.cpp, with this design's real identifiers and mode names.

    Two indents. `PASTE` lines form one contiguous compilable block, which
    paste_block() harvests and the tests compile. `NOTE` lines are asides (the
    escape hatches, the macro) that would not compile if pasted.

    The paste uses each platform's own entry points: PROS's initialize() and
    opcontrol(), and the pre_auton() and usercontrol() of VEXcode's
    competition template.

    @p comp_hooks are the compositions' Custom hooks, as (fully qualified
    name, what it feeds): no strand alias reaches into `anim::`.
    """
    PASTE = _PASTE_PREFIX
    NOTE = "//    "
    vexcode = platform is Platform.VEXCODE
    setup_fn, control_fn = ("pre_auton", "usercontrol") if vexcode else ("initialize", "opcontrol")

    hooks = [(e, name, label) for e in entries for name, label in e.sources.hooks]
    manual = [e for e in entries if e.sources.has_manual]

    lines = [
        "// Generated by HitLib Pattern Studio - edit the source design, not this file.",
        "//",
        f"// 1. Drop this file into your {platform.label} project's include/ directory.",
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
    lines.append(f"{PASTE}void {setup_fn}() {{")
    # A custom Fill source can only be supplied by the robot's own code, so the
    # paste block leaves an assignment to fill in. Placed before begin(), which
    # is when the modes that read them start running.
    assignments = [(f"{e.ns}::source::{name}", label) for e, name, label in hooks]
    assignments += comp_hooks or []
    for target, label in assignments:
        lines.append(
            f"{PASTE}    {target} = [] {{ return 0.0; }};"
            f'   // TODO: "{_escape(label)}"'
        )
    lines.append(f"{PASTE}    hitlib::studio::begin();")
    lines.append(f"{PASTE}}}")
    lines.append("//")
    lines.append("// begin() registers every strand below, starts its refresh task, attaches")
    lines.append("// its profile and activates its first mode. Calling it twice does nothing,")
    lines.append("// so a re-init path is safe.")
    if vexcode:
        lines.append("//")
        lines.append("// Not using the competition template? Call begin() at the top of main(),")
        lines.append("// after vexcodeInit(), and keep main() from returning.")
    lines.append("//")
    lines.append("// Switch modes from anywhere - these are ordinary LedStrand objects:")
    lines.append("//")
    lines.append(f"{PASTE}void {control_fn}() {{")
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
    config: StrandConfig,
    ns_namer: _UniqueNamer,
    music: _MusicRef | None = None,
    platform: Platform = Platform.PROS,
    comps: dict[int, str] | None = None,
) -> _StrandRender:
    modes = _effective_modes(config)
    display_name = config.name or "Profile"
    ns = ns_namer.make(display_name, "profile")

    fn_namer = _UniqueNamer()
    mode_namer = _UniqueNamer()
    for reserved in sorted(_RESERVED_MEMBERS):
        mode_namer.make(reserved, "mode")

    sources = _FillSourceSet(platform=platform)
    function_blocks: list[str] = []
    mode_entries: list[str] = []
    mode_names: list[str] = []

    for mode in modes:
        activate_fn, tick_fn, blocks = _generate_mode(mode, fn_namer, music, sources,
                                                      platform, comps)
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
    table = " = {\n" + "\n".join(mode_entries) + "\n}"
    lines.append(_variable(platform, "const ProfileMode", "modeTable", table, len(modes)))
    lines.append("")
    profile = f' = {{"{_escape(display_name)}", modeTable, {len(modes)}}}'
    lines.append(_variable(platform, "const Profile", "profile", profile))
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
    lines.append(_variable(platform, "LedStrand", "strand", f"{{{_constructor_args(config)}}}"))
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


def _render_studio(entries: list[_StrandRender], platform: Platform = Platform.PROS) -> list[str]:
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
        _namespace_open(platform, "hitlib::studio"),
        "",
    ]
    if split:
        lines.append("// One refresh task per interval in the design: a group ticks every strand")
        lines.append("// it owns at a single rate, and these strands were designed at different")
        lines.append("// ones. They are still one begin() call.")
    else:
        lines.append(f"// The refresh task this design's strands run on, at {intervals[0]} ms.")
    lines.append(_variable(platform, "LedGroup", "groups", count=len(intervals)))
    lines.append("")
    lines.append("/// The first group, and the only one when every strand in the design")
    lines.append("/// shares a refresh interval.")
    # Already a reference, so C++11 needs no holder for it.
    lines.append(("inline" if platform is Platform.PROS else "static") + " LedGroup& group = groups[0];")
    lines.append("")
    lines.append("/// Registers every strand in this design, initializes and starts it,")
    lines.append("/// attaches its profile and activates its first mode. Call once from")
    setup_fn = "pre_auton()" if platform is Platform.VEXCODE else "initialize()"
    lines.append(f"/// {setup_fn}; a second call does nothing, so a re-init path is safe.")
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
    lines.append("/// you. Each strand is initialized as it is added - init() skips one that")
    lines.append("/// already is - so call it before or after your own init();")
    lines.append("/// order does not matter.")
    if split:
        lines.append("/// Note: these strands were designed at different refresh intervals, and")
        lines.append("/// one group runs one. Pick the interval the flash timings matter at, or")
        lines.append("/// use the no-argument begin() instead.")
    lines.append("inline void begin(LedGroup& existing) {")
    for e in entries:
        # A group only initializes the strands it holds when its own init()
        # runs, so one added after that would otherwise never light.
        lines.append(f"    existing.add(&{qualified(e)});")
        lines.append(f"    {qualified(e)}.init();")
    lines.append("")
    for e in entries:
        lines.extend(wire(e))
    lines.append("}")
    lines.append("")
    lines.append(_namespace_close(platform, "hitlib::studio"))
    lines.append("")
    lines.append("#endif  // HITLIB_STUDIO_NO_AUTOWIRE")
    return lines


#: Sample table line width. Twelve three-digit values plus separators sit just
#: inside 80 columns, so the emitted array stays readable in a normal editor.
_SAMPLES_PER_LINE = 12


def _music_bands_used(configs: list[StrandConfig],
                      compositions: list[CompositionConfig] | None = None) -> list[str]:
    """Bands any exported Music Sync animation, or Song Band driver in a
    composition the export plays, asks for, in first-seen order.

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
    played = _compositions_used(configs)
    for comp in compositions or []:
        if comp.id not in played:
            continue
        for drv in comp.drivers:
            if drv.kind == DriverKind.MUSIC and drv.band not in used:
                used.append(drv.band)
    return used


def _render_music(
    music: MusicConfig, bands: list[str], platform: Platform = Platform.PROS
) -> tuple[_MusicRef, list[str]]:
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
        rows = [
            "    " + " ".join(f"{v:3d}," for v in table[start : start + _SAMPLES_PER_LINE])
            for start in range(0, len(table), _SAMPLES_PER_LINE)
        ]
        samples = " = {\n" + "\n".join(rows) + "\n}"
        lines.append(_variable(platform, "const uint8_t", samples_var, samples, len(table)))
        track = f" = {{{samples_var}, {len(table)}, {music.frame_ms}}}"
        lines.append(_variable(platform, "const LedStrand::MusicTrack", var, track))
    lines.append("")
    lines.append("}  // namespace music")
    return _MusicRef(exprs=exprs, loop=music.loop), lines



def _compositions_used(configs: list[StrandConfig]) -> set[int]:
    """Composition ids something in this export actually plays."""
    used: set[int] = set()
    for cfg in configs:
        for mode in _effective_modes(cfg):
            leaves = [p.animation for p in mode.phases] if mode.phases else [mode.animation]
            for a in leaves:
                if a.kind == AnimationKind.CUSTOM and a.composition_id:
                    used.add(a.composition_id)
    return used


def _render_compositions(
    compositions: list[CompositionConfig],
    configs: list[StrandConfig],
    music_ref: _MusicRef | None,
    platform: Platform,
) -> tuple[list[str], dict[int, str], list[tuple[str, _FillSourceSet]]]:
    """The `anim::` namespace, the id -> namespace-name map the strands need
    to reach into it, and each emitted composition's readers by its name.

    Emitted once for the whole file, outside any strand, because a
    composition belongs to the document: two strands can play one, and
    emitting it twice would be two copies of the same tables in flash.

    Each composition gets its own _FillSourceSet. Its readers land in its own
    `source::`, so one that another composition already emitted would be a
    name this one cannot see.
    """
    used = _compositions_used(configs)
    wanted = [c for c in compositions if c.id in used]
    if not wanted:
        return [], {}, []

    namer = _UniqueNamer(reserved=_OWNED_NAMESPACES)
    names: dict[int, str] = {}
    readers: list[tuple[str, _FillSourceSet]] = []
    body: list[str] = []
    for comp in wanted:
        name = namer.make(comp.name, "animation")
        names[comp.id] = name
        sources = _FillSourceSet(platform=platform)
        readers.append((name, sources))
        body.extend(codegen_custom.render_composition(
            comp, name, platform, lambda ty, n, init="", count=None: _variable(
                platform, ty, n, init, count),
            sources, music_ref, _namespace_open, _namespace_close,
        ))

    lines = [
        "// Custom animations from the Animator. A composition belongs to the",
        "// document rather than to one strip, so several strands can play the",
        "// same one - hence its own namespace rather than a strand's.",
        _namespace_open(platform, "anim"),
        "",
        *body,
        _namespace_close(platform, "anim"),
    ]
    return lines, names, readers


def _render_file(
    configs: list[StrandConfig],
    header_name: str | None,
    music: MusicConfig | None = None,
    platform: Platform = Platform.PROS,
    compositions: list[CompositionConfig] | None = None,
) -> str:
    single = len(configs) == 1
    ns_namer = _UniqueNamer(reserved=_OWNED_NAMESPACES | _MAIN_CPP_NAMES)

    music_ref: _MusicRef | None = None
    music_lines: list[str] = []
    bands_used = _music_bands_used(configs, compositions)
    if music is not None and music.loaded and bands_used:
        music_ref, music_lines = _render_music(music, bands_used, platform)
        if not music_ref.exprs:
            music_ref, music_lines = None, []

    # Only the compositions something actually plays. A library can hold a
    # dozen while a design uses two, and a header carrying the other ten is
    # flash spent on nothing.
    comp_lines, comp_names, comp_sources = _render_compositions(
        compositions or [], configs, music_ref, platform
    )
    comp_hooks = [
        (f"hitlib::profiles::anim::{comp}::source::{name}", label)
        for comp, sources in comp_sources
        for name, label in sources.hooks
    ]

    entries = [_render_strand(cfg, ns_namer, music_ref, platform, comp_names) for cfg in configs]

    default_name = (
        suggested_header_name(configs[0].name, platform) if single else document_header_name(platform)
    )

    lines: list[str] = ["#pragma once", ""]
    lines.extend(_usage_banner(entries, header_name or default_name, music_ref, platform,
                               comp_hooks))
    lines.append("")
    # HitLib's headers are .h in its VEXcode download.
    for library_header in ("led_group", "led_profile", "led_sequencer", "led_strand"):
        lines.append(f'#include "hitlib/{library_header}{platform.header_suffix}"')
    device_includes = sorted(
        {inc for e in entries for inc in e.sources.includes}
        | {inc for _, sources in comp_sources for inc in sources.includes}
    )
    if device_includes:
        lines.append("")
        lines.append("// Devices this design's Fill meters and Gauges read")
        for include in device_includes:
            lines.append(f'#include "{include}"')
    lines.append("")
    if platform is Platform.VEXCODE:
        lines.append("// VEXcode compiles C++11, which has no inline variables. Each object below")
        lines.append("// that every file including this header shares is a static member of a")
        lines.append("// class template (the NAME_ structs), reached through a reference named")
        lines.append("// NAME. Use the reference; the struct is only where the object lives.")
        lines.append("")
    lines.append(_namespace_open(platform, "hitlib::profiles"))
    lines.append("")
    if music_lines:
        lines.extend(music_lines)
        lines.append("")
    if comp_lines:
        lines.extend(comp_lines)
        lines.append("")
    for entry in entries:
        lines.extend(entry.lines)
        lines.append("")
    lines.append(_namespace_close(platform, "hitlib::profiles"))
    lines.append("")
    lines.extend(_render_studio(entries, platform))
    lines.append("")
    return "\n".join(lines)


def suggested_header_name(strand_name: str, platform: Platform = Platform.PROS) -> str:
    """Default filename to offer for a single-strand export: "My Robot" ->
    my_robot.hpp (my_robot.h for VEXcode)."""
    return _snake_case(strand_name or "profile", "profile") + platform.header_suffix


def document_header_name(platform: Platform = Platform.PROS) -> str:
    """Default filename to offer for a whole-document export."""
    return "led_profiles" + platform.header_suffix


def generate_cpp(
    config: StrandConfig,
    header_name: str | None = None,
    music: MusicConfig | None = None,
    platform: Platform = Platform.PROS,
    compositions: list[CompositionConfig] | None = None,
) -> str:
    """Render one strand as a standalone header.

    @p header_name is the filename the export will be saved as, it only
    feeds the #include line in the usage banner. @p music is the design's
    song, needed only when the strand actually has a Music Sync animation.
    @p platform is the environment the robot's code is built in.
    """
    return _render_file([config], header_name, music, platform, compositions)


def generate_document_cpp(
    configs: list[StrandConfig], header_name: str | None = None,
    music: MusicConfig | None = None,
    platform: Platform = Platform.PROS,
    compositions: list[CompositionConfig] | None = None,
) -> str:
    """Render every strand in the document into a single header, each in its
    own namespace.

    One file means one namespace namer, so two strands that both have an
    "Idle" mode cannot generate colliding identifiers. Separately exported
    files have no such shared namer.
    """
    if not configs:
        raise ValueError("no strands to export")
    return _render_file(configs, header_name, music, platform, compositions)


def _generate_mode(
    mode: ModeConfig,
    namer: _UniqueNamer,
    music: _MusicRef | None = None,
    sources: _FillSourceSet | None = None,
    platform: Platform = Platform.PROS,
    comps: dict[int, str] | None = None,
) -> tuple[str, str | None, list[str]]:
    blocks: list[str] = []

    if not mode.phases:
        activate_fn = namer.make(mode.name, "mode")
        body = "\n".join(
            f"    {line}"
            for line in _leaf_body(mode.animation, mode.splice, music, sources,
                                   mode.name, platform, comps)
        )
        blocks.append(f"inline void {activate_fn}(LedStrand& s) {{\n{body}\n}}")
        return activate_fn, None, blocks

    phase_fn_names = []
    for phase in mode.phases:
        fn_name = namer.make(f"{mode.name} {phase.name}", "phase")
        label = f"{mode.name} {phase.name}"
        body = "\n".join(
            f"    {line}"
            for line in _leaf_body(phase.animation, phase.splice, music, sources,
                                   label, platform, comps)
        )
        blocks.append(f"inline void {fn_name}(LedStrand& s) {{\n{body}\n}}")
        phase_fn_names.append((fn_name, phase.duration_ms))

    phases_array_name = namer.make(f"{mode.name} Phases", "phases")
    phase_entries = "\n".join(f"    {{{dur}, {fn}}}," for fn, dur in phase_fn_names)
    phases = f" = {{\n{phase_entries}\n}}"
    blocks.append(_variable(platform, "const Sequencer::Phase", phases_array_name, phases, len(phase_fn_names)))

    seq_name = namer.make(f"{mode.name} Seq", "seq")
    blocks.append(_variable(platform, "Sequencer", seq_name, f"({phases_array_name}, {len(phase_fn_names)})"))

    activate_fn = namer.make(f"{mode.name} Activate", "modeActivate")
    blocks.append(f"inline void {activate_fn}(LedStrand& s) {{ {seq_name}.start(s); }}")

    tick_fn = namer.make(f"{mode.name} Tick", "modeTick")
    blocks.append(f"inline void {tick_fn}(LedStrand& s) {{ {seq_name}.update(s); }}")

    return activate_fn, tick_fn, blocks


def _escape(text: str) -> str:
    """@p text as the inside of a C++ string literal, or of a quoted name in a
    comment.

    A control character (a newline in a hand-edited file's name, say) becomes
    a three-digit octal escape: unlike \\x, octal stops after three digits,
    so a name that goes on with a digit cannot run into it.
    """
    out = []
    for ch in text:
        if ch in '\\"':
            out.append("\\" + ch)
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\{ord(ch):03o}")
        else:
            out.append(ch)
    return "".join(out)
