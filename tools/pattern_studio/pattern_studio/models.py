"""Declarative config for a strand's current animation.

This is the layer that sits between the GUI widgets and hitlib_sim.Strand: a
plain, serializable description of "what animation with what params" rather
than engine runtime state. Kept deliberately separate from hitlib_sim so the
same shape can later be (de)serialized for save/load and mapped
straight onto LedStrand method calls for C++ export.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from . import fill_sources
from .envelope import BAND_BASS, EnvelopeSettings, TrackAnalysis


class AnimationKind(str, Enum):
    OFF = "off"
    SOLID = "solid"
    PULSE = "pulse"
    FLASH = "flash"
    FLOW = "flow"
    RAINBOW = "rainbow"
    TWINKLE = "twinkle"
    BITSCROLL = "bitscroll"
    FILL = "fill"
    MUSIC = "music"
    CUSTOM = "custom"


# Human-readable labels for the animation-kind dropdown, in display order.
ANIMATION_KIND_LABELS: dict[AnimationKind, str] = {
    AnimationKind.OFF: "Off",
    AnimationKind.SOLID: "Solid Color",
    AnimationKind.PULSE: "Pulse",
    AnimationKind.FLASH: "Flash",
    AnimationKind.FLOW: "Flow (Gradient)",
    AnimationKind.RAINBOW: "Rainbow",
    AnimationKind.TWINKLE: "Twinkle",
    AnimationKind.BITSCROLL: "Bitscroll",
    AnimationKind.FILL: "Fill (Meter)",
    AnimationKind.MUSIC: "Music Sync",
    AnimationKind.CUSTOM: "Custom Animation",
}


@dataclass
class AnimationConfig:
    kind: AnimationKind = AnimationKind.RAINBOW
    color: int = 0xFF0000
    color2: int = 0x0000FF
    bg_color: int = 0x000000
    run_length: int = 5
    # Motion, for PULSE/FLOW/RAINBOW/BITSCROLL: `speed` pixels every `step_ms`
    # milliseconds - LedStrand::Speed. A step_ms of 0 steps on every refresh
    # tick, which is all `speed` could say before it could go slower, so it
    # is what a file saved before step_ms existed means.
    speed: int = 1
    step_ms: int = 0
    # FLASH timing. Separate from `speed` on purpose: on and off durations are
    # independent, so blink rate and duty cycle are set without interacting.
    on_ms: int = 250
    off_ms: int = 250
    invert: bool = False
    bounce: bool = False
    # FLOW only: loop the gradient back to `color` instead of cutting straight
    # from `color2` to `color` at the wrap.
    seamless: bool = True
    density_pct: int = 30
    fade_step: int = 16
    palette: list[int] = field(default_factory=lambda: [0xFF0000, 0x00FF00, 0x0000FF])
    segment_width: int = 3
    spacing: int = 5
    repeating: bool = True
    # FILL and MUSIC both drive the same meter, so they share the look fields
    # below (`gradient`, plus `color`/`color2`/`bg_color`/`invert` above) and
    # differ only in what moves it.
    #
    # FILL. `source` names an entry in fill_sources: what the bar follows, and
    # `source_port` the device it reads when that source needs one. The bar is
    # empty at `source_empty` and full at `source_full`, in whatever units the
    # reading speaks - putting full below empty reverses the meter, which is
    # how a "remaining" bar drains as a number climbs. `source_wrap` cycles
    # instead of pinning at full (a continuously turning motor), `smoothing`
    # glides the bar toward its target instead of snapping, for noisy readings.
    source: str = fill_sources.MANUAL
    source_port: int = 1
    source_empty: int = 0
    source_full: int = 255
    source_wrap: bool = False
    smoothing: int = 0
    # Preview only, never exported: the desktop can't read a real motor, so the
    # panel feeds the meter a stand-in value - a fixed percentage of the range,
    # or a sweep through it. Saved with the design so a reopened file previews
    # the way it was left.
    preview_level: int = 50
    preview_sweep: bool = True

    # MUSIC. The meter fills with `color` alone, or blends `color` to `color2`
    # across the strip when `gradient` is set; `invert` above flips which end
    # it fills from. `sensitivity` is a percentage gain on the baked envelope
    # and stays adjustable at runtime, so it can be retuned on the robot
    # without re-exporting. `band` picks which slice of the song drives this
    # strand - it is per-strand so one strip can pump on the kick while another
    # sparkles on the hats, from the same song.
    gradient: bool = True
    sensitivity: int = 100
    band: str = BAND_BASS

    # CUSTOM. Which of the document's compositions this strand plays, by
    # CompositionConfig.id. 0, or an id no longer in the library, previews
    # and exports as Off rather than failing - a composition can be deleted
    # while a strand still names it.
    composition_id: int = 0


class OverlayAnimationKind(str, Enum):
    OFF = "off"
    SOLID = "solid"
    PULSE = "pulse"
    FLASH = "flash"
    FLOW = "flow"
    RAINBOW = "rainbow"
    TWINKLE = "twinkle"
    BITSCROLL = "bitscroll"
    GAUGE = "gauge"


# Human-readable labels for the overlay-kind dropdown, in display order.
OVERLAY_ANIMATION_KIND_LABELS: dict[OverlayAnimationKind, str] = {
    OverlayAnimationKind.OFF: "Off",
    OverlayAnimationKind.SOLID: "Solid Color",
    OverlayAnimationKind.PULSE: "Pulse",
    OverlayAnimationKind.FLASH: "Flash",
    OverlayAnimationKind.FLOW: "Flow (Gradient)",
    OverlayAnimationKind.RAINBOW: "Rainbow",
    OverlayAnimationKind.TWINKLE: "Twinkle",
    OverlayAnimationKind.BITSCROLL: "Bitscroll",
    OverlayAnimationKind.GAUGE: "Gauge (Meter)",
}


class GaugeStyleKind(str, Enum):
    """Mirrors LedStrand::GaugeStyle."""

    HEAT = "heat"  # the whole region shows one color off the scale
    BAR = "bar"    # the region fills proportionally, a meter in miniature


class GaugeBlendKind(str, Enum):
    """Mirrors LedStrand::GaugeBlend."""

    LERP = "lerp"  # blend between stops
    STEP = "step"  # hold each stop's color until the next one is reached


GAUGE_STYLE_LABELS: dict[GaugeStyleKind, str] = {
    GaugeStyleKind.HEAT: "Whole Segment",
    GaugeStyleKind.BAR: "Fill Bar",
}

GAUGE_BLEND_LABELS: dict[GaugeBlendKind, str] = {
    GaugeBlendKind.LERP: "Blend Between Stops",
    GaugeBlendKind.STEP: "Hold Each Stop",
}


@dataclass
class GaugeStopConfig:
    """One color on a Gauge region's scale - mirrors LedStrand::GaugeStop.

    `at` is in the source's own units (55 °C, 40 %, 1200 mm), not in pixels or
    in 0-255, so a scale says what it means and survives a change to the
    region's Empty At / Full At range.
    """

    at: float = 0.0
    color: int = 0x00FF00


@dataclass
class OverlayAnimationConfig:
    """Mirrors LedStrand's overlay* methods, an animation buffer that can be
    shown instead of a solid color. Used two ways: as the single shared
    overlay a Split-mode splice mask's masked bins can reveal (one buffer for
    the whole strand), and as each Custom-mode region's own independent
    animation (one buffer per region - see SpliceRegionConfig).

    GAUGE is the one kind that is only meaningful in the second of those: it
    turns a region into an independent meter following its own reading, so one
    strip can carry several. The Split-mode overlay is a single shared buffer,
    where a gauge would be one meter stretched over every masked bin, so the
    panel hides the choice there.

    TWINKLE and BITSCROLL carry the same parameters as their AnimationConfig
    counterparts, minus `bounce`: both an overlay and a region are a single
    scrolling buffer, and bouncing needs a wider master pattern to slide a
    window over.
    """

    kind: OverlayAnimationKind = OverlayAnimationKind.SOLID
    color: int = 0xFFFFFF
    color2: int = 0x0000FF
    bg_color: int = 0x000000
    run_length: int = 5
    speed: int = 1     # pixels per step - see AnimationConfig
    step_ms: int = 0   # 0 steps every refresh tick
    on_ms: int = 250   # FLASH lit duration
    off_ms: int = 250  # FLASH blank duration
    # FLOW only: loop the gradient back to `color` instead of cutting straight
    # from `color2` to `color` at the wrap.
    seamless: bool = True
    # Reverse direction: a BITSCROLL scrolls the other way, a GAUGE bar fills
    # from the far end of the region.
    invert: bool = False

    # TWINKLE.
    density_pct: int = 30
    fade_step: int = 16
    palette: list[int] = field(default_factory=lambda: [0xFF0000, 0x00FF00, 0x0000FF])

    # BITSCROLL.
    segment_width: int = 3
    spacing: int = 5
    repeating: bool = True

    # GAUGE. The source fields match AnimationConfig's Fill ones exactly - the
    # same catalog, the same meaning, the same codegen - because a gauge region
    # is a Fill meter scoped to a few pixels. `stops` is what it adds: the
    # colors the scale passes through, in the source's own units. An empty list
    # falls back to `color` at Empty At blending to `color2` at Full At.
    source: str = fill_sources.MANUAL
    source_port: int = 1
    source_empty: int = 0
    source_full: int = 100
    source_wrap: bool = False
    smoothing: int = 0
    style: GaugeStyleKind = GaugeStyleKind.HEAT
    blend: GaugeBlendKind = GaugeBlendKind.LERP
    stops: list[GaugeStopConfig] = field(default_factory=list)
    # Preview only, never exported - see AnimationConfig's identically named
    # fields. Each region sweeps from its own phase offset so a row of gauges
    # does not move in lockstep.
    preview_level: int = 50
    preview_sweep: bool = True


class SpliceModeKind(str, Enum):
    SPLIT = "split"
    CUSTOM = "custom"


@dataclass
class SpliceRegionConfig:
    """One independently placed override region for a Custom-mode splice
    mask. Each region owns its own OverlayAnimationConfig, unlike Split
    mode's single shared overlay, every region can animate independently and
    simultaneously.
    """

    start: int = 0
    width: int = 1
    animation: OverlayAnimationConfig = field(default_factory=OverlayAnimationConfig)


@dataclass
class SpliceMaskConfig:
    enabled: bool = False
    mode: SpliceModeKind = SpliceModeKind.SPLIT

    # Split mode: sections + 1 equal bins, optionally alternating.
    sections: int = 1
    invert: bool = False
    alternating: bool = False
    alt_period_ms: int = 400
    bg_color: int = 0x000000
    use_overlay: bool = False

    # Custom mode: arbitrarily placed/sized regions, never alternating.
    regions: list[SpliceRegionConfig] = field(default_factory=list)

    # Split mode's single shared overlay animation (see use_overlay above).
    # Custom mode regions each carry their own animation instead - see
    # SpliceRegionConfig.animation.
    overlay: OverlayAnimationConfig = field(default_factory=OverlayAnimationConfig)

    def needs_overlay(self) -> bool:
        """Whether Split mode's shared overlay animation actually needs to be
        set up, i.e. its masked bins are configured to show it. Not
        meaningful for Custom mode, where each region owns its animation."""
        return self.mode == SpliceModeKind.SPLIT and self.use_overlay


@dataclass
class PhaseConfig:
    """One timed step of a sequenced mode - mirrors hitlib_sim.sequencer.Phase."""

    name: str = "Phase"
    duration_ms: int = 1000
    animation: AnimationConfig = field(default_factory=AnimationConfig)
    splice: SpliceMaskConfig = field(default_factory=SpliceMaskConfig)


@dataclass
class ModeConfig:
    """One named entry in a strand's profile - mirrors hitlib_sim.profile.ProfileMode.

    If `phases` is empty the mode is a steady-state animation (`animation`/
    `splice`, re-issued once on activation). If non-empty, it's a timed
    sequence: `animation`/`splice` are unused and the mode cycles through
    `phases` via a Sequencer instead.
    """

    name: str = "Mode"
    priority: int = 10
    animation: AnimationConfig = field(default_factory=AnimationConfig)
    splice: SpliceMaskConfig = field(default_factory=SpliceMaskConfig)
    phases: list[PhaseConfig] = field(default_factory=list)


@dataclass
class StrandConfig:
    name: str = "Strand"
    adi_port: int = 1
    smart_port: int = 0
    length: int = 30
    refresh_ms: int = 25
    brightness: int = 100
    animation: AnimationConfig = field(default_factory=AnimationConfig)
    splice: SpliceMaskConfig = field(default_factory=SpliceMaskConfig)

    # Profile mode: when use_profile is True, `animation`/`splice` above are
    # ignored in favor of `profile_modes`, with `active_mode_indices` driving
    # the same activate_mode()/priority-stack behavior LedStrand uses on
    # real hardware. Lets the preview exercise mode switching.
    use_profile: bool = False
    profile_modes: list[ModeConfig] = field(default_factory=list)
    active_mode_indices: list[int] = field(default_factory=list)

    def animations(self) -> list[AnimationConfig]:
        """Every animation this strand holds: its own, and each profile mode's
        and phase's. Including the ones not playing right now, since a later
        edit can bring any of them back."""
        found = [self.animation]
        for mode in self.profile_modes:
            found.append(mode.animation)
            found.extend(p.animation for p in mode.phases)
        return found


@dataclass
class MusicConfig:
    """The document's song: where it came from, how its envelope is shaped, and
    the loudness analysis both of those produced.

    Document-level rather than per-strand on purpose - a design syncs to one
    song, and every strand set to AnimationKind.MUSIC plays it, choosing its own
    band, colors and direction.

    `analysis` is the expensive part and the only thing saved: the per-band
    tables are re-baked from it on load, which is fast, and keeps the file from
    carrying two copies of the same thing that could drift apart.
    """

    name: str = ""
    source_path: str = ""
    #: "audio", "midi", or "" when nothing is loaded. Decides which controls the
    #: Song bar shows - MIDI has tracks to pick, audio does not.
    source_kind: str = ""
    #: MIDI track indices feeding the analysis. Empty means every track with
    #: notes. Meaningless for audio.
    tracks: list[int] = field(default_factory=list)
    #: Repeat the song instead of going dark at the end. A property of the song,
    #: not of any one strand: the preview transport and every exported
    #: musicSync() call have to agree about it or they show different things.
    loop: bool = False
    settings: EnvelopeSettings = field(default_factory=EnvelopeSettings)
    analysis: TrackAnalysis = field(default_factory=TrackAnalysis)
    #: band name -> baked 0-255 table. Derived from `analysis`, never saved.
    bands: dict[str, list[int]] = field(default_factory=dict)

    @property
    def loaded(self) -> bool:
        return any(self.bands.values())

    @property
    def has_song(self) -> bool:
        """Whether there is a song, baked or not.

        `loaded` asks whether the tables are baked, and a document straight
        out of a file never is: the Song bar re-bakes them from `analysis`
        when it takes the document. Ask this of one it has not taken yet.
        """
        return self.loaded or self.analysis.loaded

    @property
    def frame_ms(self) -> int:
        return self.settings.frame_ms

    @property
    def duration_ms(self) -> int:
        for table in self.bands.values():
            if table:
                return len(table) * self.settings.frame_ms
        return 0

    def table(self, band: str) -> list[int]:
        """The table a strand on `band` plays, falling back to any band that has
        one so a strand never silently goes dark because of a band choice."""
        table = self.bands.get(band)
        if table:
            return table
        for candidate in self.bands.values():
            if candidate:
                return candidate
        return []


# ============================================================================
# Custom animations (the Animator)
#
# These mirror LedStrand's Composition types the same way AnimationConfig
# mirrors its base animations: plain serializable description, no engine
# state. hitlib_sim.composition holds the runtime shape; engine.py maps one
# onto the other, and codegen maps this onto C++.
#
# Unlike the C++, every field here has a default. The firmware's Layer is a
# C++11 aggregate on purpose - no default member initializers - so its table
# can be brace-initialized identically in both dialects and live in flash. A
# user-facing default belongs here instead.
# ============================================================================


class LayerKind(str, Enum):
    """What one layer draws.

    OFF through GAUGE are OverlayAnimationKind entry for entry: a layer and a
    splice region say the same words and take the same parameters. METER is
    the Fill meter scoped to a span. FRAMES is a hand-drawn pixel strip.
    """

    OFF = "off"
    SOLID = "solid"
    PULSE = "pulse"
    FLASH = "flash"
    FLOW = "flow"
    RAINBOW = "rainbow"
    TWINKLE = "twinkle"
    BITSCROLL = "bitscroll"
    GAUGE = "gauge"
    METER = "meter"
    FRAMES = "frames"


LAYER_KIND_LABELS: dict[LayerKind, str] = {
    LayerKind.OFF: "Off",
    LayerKind.SOLID: "Solid Color",
    LayerKind.PULSE: "Pulse",
    LayerKind.FLASH: "Flash",
    LayerKind.FLOW: "Flow (Gradient)",
    LayerKind.RAINBOW: "Rainbow",
    LayerKind.TWINKLE: "Twinkle",
    LayerKind.BITSCROLL: "Bitscroll",
    LayerKind.GAUGE: "Gauge (Scale)",
    LayerKind.METER: "Meter (Bar)",
    LayerKind.FRAMES: "Drawn Frames",
}


class LayerBlend(str, Enum):
    """How a layer lands on what the layers below it already drew."""

    OVER = "over"
    KEY = "key"
    ADD = "add"
    SCREEN = "screen"
    MULTIPLY = "multiply"
    MASK = "mask"


LAYER_BLEND_LABELS: dict[LayerBlend, str] = {
    LayerBlend.OVER: "Replace",
    LayerBlend.KEY: "Keep Below (Key)",
    LayerBlend.ADD: "Add",
    LayerBlend.SCREEN: "Screen",
    LayerBlend.MULTIPLY: "Multiply",
    LayerBlend.MASK: "Mask",
}

#: One line each, for the Blend combo's tooltips. KEY is the one worth
#: explaining: it is what makes a second layer stack rather than replace.
LAYER_BLEND_HELP: dict[LayerBlend, str] = {
    LayerBlend.OVER: "Paint over what is underneath, background and all.",
    LayerBlend.KEY: "Paint only where this layer is lit, and let the layers below "
                    "show through its gaps.",
    LayerBlend.ADD: "Add this layer's light to what is underneath. Good for accents "
                    "and sparks.",
    LayerBlend.SCREEN: "Brighten what is underneath without blowing it out.",
    LayerBlend.MULTIPLY: "Tint what is underneath. Black hides, white leaves it alone.",
    LayerBlend.MASK: "Use this layer's brightness to decide how much of the layers "
                     "below survives.",
}


class DriverKind(str, Enum):
    """Where a driver's 0-255 comes from."""

    CONSTANT = "constant"
    SENSOR = "sensor"
    MUSIC = "music"
    LFO = "lfo"


DRIVER_KIND_LABELS: dict[DriverKind, str] = {
    DriverKind.CONSTANT: "Set From Code",
    DriverKind.SENSOR: "Sensor Reading",
    DriverKind.MUSIC: "Song Band",
    DriverKind.LFO: "Oscillator",
}


class LfoShape(str, Enum):
    TRIANGLE = "triangle"
    SAW = "saw"
    RAMP_DOWN = "ramp_down"
    SQUARE = "square"
    SINE = "sine"


LFO_SHAPE_LABELS: dict[LfoShape, str] = {
    LfoShape.TRIANGLE: "Triangle (up and down)",
    LfoShape.SAW: "Saw (up, then snap)",
    LfoShape.RAMP_DOWN: "Ramp Down",
    LfoShape.SQUARE: "Square (on/off)",
    LfoShape.SINE: "Sine (smooth)",
}


class LayerParam(str, Enum):
    """A layer parameter a driver is allowed to move."""

    NONE = "none"
    OPACITY = "opacity"
    SPEED = "speed"
    RUN_LENGTH = "run_length"
    LEVEL = "level"
    START = "start"
    WIDTH = "width"
    DENSITY = "density"
    MIX = "mix"


LAYER_PARAM_LABELS: dict[LayerParam, str] = {
    LayerParam.NONE: "(nothing)",
    LayerParam.OPACITY: "Opacity",
    LayerParam.SPEED: "Speed (px per step)",
    LayerParam.RUN_LENGTH: "Run Length",
    LayerParam.LEVEL: "Level",
    LayerParam.START: "Start Pixel",
    LayerParam.WIDTH: "Width",
    LayerParam.DENSITY: "Density",
    LayerParam.MIX: "Color Mix",
}

#: Ceilings, matching the static constexpr members on LedStrand. A layer past
#: MAX_LAYERS is not exported, so the Animator refuses to add one rather than
#: letting a design silently lose a layer at the border.
#: LedStrand::MAX_LEDS. The longest strip the firmware will drive.
MAX_LEDS = 64
MAX_LAYERS = 8
MAX_DRIVERS = 8
MAX_BINDINGS = 4
CURVE_POINTS = 17


@dataclass
class BitScrollSegmentConfig:
    """One lit run of a Bitscroll layer."""

    color: int = 0xFF0000
    width: int = 3


@dataclass
class BindingConfig:
    """Ties one driver to one parameter of one layer.

    The parameter reads `at_zero` when the driver is 0 and `at_full` when it
    is 255. `at_full` below `at_zero` reverses it, the same way a Fill meter's
    Full At below its Empty At reverses the bar.

    `curve` is empty for a straight line, or CURVE_POINTS values 0-255 that
    reshape the driver first: a curve that steps makes this a threshold, one
    that eases makes it an envelope.
    """

    param: LayerParam = LayerParam.NONE
    driver: int = 0
    at_zero: int = 0
    at_full: int = 255
    curve: list[int] = field(default_factory=list)


@dataclass
class LayerConfig:
    """One layer of a custom animation.

    The field names match AnimationConfig's wherever the two mean the same
    thing, so OverlayAnimationPanel can edit either without a translation
    table in between.
    """

    name: str = "Layer"
    kind: LayerKind = LayerKind.SOLID
    muted: bool = False

    # Where it lands. `width` 0 means "to the end of the strand"; `stride` 0
    # draws one span, otherwise the span repeats every `stride` pixels.
    start: int = 0
    width: int = 0
    stride: int = 0
    #: With `stride`, sample the animation at the strand-wide pixel index
    #: rather than restarting it in each repeat - one scroll cut into windows
    #: instead of N synchronized copies.
    continuous: bool = False

    blend: LayerBlend = LayerBlend.OVER
    opacity: int = 255
    bindings: list[BindingConfig] = field(default_factory=list)

    color: int = 0xFFFFFF
    color2: int = 0x0000FF
    bg_color: int = 0x000000

    run_length: int = 5
    # `speed` pixels every `step_ms` ms, as AnimationConfig. A SPEED binding
    # moves the pixels; the step time is the layer's own.
    speed: int = 1
    step_ms: int = 0
    invert: bool = False
    bounce: bool = False
    seamless: bool = True

    on_ms: int = 250
    off_ms: int = 250

    # When it is on, inside the composition's loop. Both zero means always.
    # An end at or before the start runs across the loop point.
    loop_ms: int = 0
    start_ms: int = 0
    end_ms: int = 0
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    retrigger: bool = False

    # TWINKLE.
    palette: list[int] = field(default_factory=lambda: [0xFF0000, 0x00FF00, 0x0000FF])
    density_pct: int = 30
    fade_step: int = 16

    # BITSCROLL. Several segments, unlike the preset - which has always taken
    # a vector in C++ but has never been offered more than one here.
    segments: list[BitScrollSegmentConfig] = field(default_factory=list)
    spacing: int = 5
    repeating: bool = True

    # GAUGE / METER. The scale is in the driver's own units, as a Fill
    # meter's is, so it survives a change to the range.
    level: int = 0
    gradient: bool = False
    style: GaugeStyleKind = GaugeStyleKind.HEAT
    gauge_blend: GaugeBlendKind = GaugeBlendKind.LERP
    stops: list[GaugeStopConfig] = field(default_factory=list)
    empty_at: float = 0.0
    full_at: float = 100.0

    # FRAMES. `frames` is one row of palette indices per frame; index 0 is
    # transparent. The RLE packing happens on the way out - see frame_codec -
    # so the editor always holds the plain grid.
    frames: list[list[int]] = field(default_factory=list)
    frame_palette: list[int] = field(default_factory=lambda: [0x000000, 0xFF0000, 0x00FF00])
    frame_width: int = 20
    frame_ms: int = 100
    #: Per row of `frames`: True scrolls that frame at `speed`/`step_ms`,
    #: False shows it exactly as drawn. A row with no entry holds.
    frame_scroll: list[bool] = field(default_factory=list)

    def scroll_flags(self) -> list[bool]:
        """`frame_scroll`, one entry per frame - padded with holds or cut to
        length, since the grid can grow or shrink under it."""
        flags = list(self.frame_scroll[:len(self.frames)])
        return flags + [False] * (len(self.frames) - len(flags))


@dataclass
class DriverConfig:
    """One thing a composition watches, reduced to a byte per tick.

    The source fields match AnimationConfig's Fill ones exactly - the same
    catalog, the same meaning, the same codegen - because a SENSOR driver is
    a Fill meter's source with the meter taken off the end of it.
    """

    name: str = "Driver"
    kind: DriverKind = DriverKind.CONSTANT

    #: CONSTANT's starting byte, and what the preview shows for it.
    value: int = 128

    # SENSOR.
    source: str = fill_sources.MANUAL
    source_port: int = 1
    source_empty: int = 0
    source_full: int = 100
    source_wrap: bool = False
    smoothing: int = 0

    # MUSIC.
    band: str = BAND_BASS
    sensitivity: int = 100

    # LFO.
    shape: LfoShape = LfoShape.TRIANGLE
    period_ms: int = 1000
    phase_pct: int = 0

    # Preview only, never exported - as AnimationConfig's identically named
    # fields are. A desktop cannot read a real motor, so a SENSOR driver is
    # fed a stand-in through the firmware's own mapping.
    preview_level: int = 50
    preview_sweep: bool = True


@dataclass
class CompositionConfig:
    """A named, reusable custom animation: layers over a driver table and a
    clock. Layer 0 is the bottom.

    Lives on the Document rather than on a strand, like the song does: several
    strands can play one composition, and the Animator edits it in one place.
    """

    #: Stable across renames, because a strand refers to a composition by id.
    id: int = 1
    name: str = "Animation"
    layers: list[LayerConfig] = field(default_factory=list)
    drivers: list[DriverConfig] = field(default_factory=list)
    loop_ms: int = 0
    bg_color: int = 0x000000


def composition_ids_named(strands: list[StrandConfig]) -> set[int]:
    """Every composition id a strand names, whether or not the library still
    has it. 0 names nothing."""
    return {a.composition_id for cfg in strands for a in cfg.animations() if a.composition_id}


@dataclass
class Document:
    """Everything one .hlprofile holds: the strands, the song they sync to,
    and the custom animations they can play.

    `compositions` is a library rather than a per-strand field for the same
    reason `music` is: several strands can play one, and the Animator edits
    it in one place.
    """

    strands: list[StrandConfig] = field(default_factory=list)
    music: MusicConfig = field(default_factory=MusicConfig)
    compositions: list[CompositionConfig] = field(default_factory=list)

    def composition(self, comp_id: int) -> CompositionConfig | None:
        """The composition with `comp_id`, or None if nothing has that id."""
        for comp in self.compositions:
            if comp.id == comp_id:
                return comp
        return None

    def next_composition_id(self) -> int:
        """An id no composition in this document is using, and no strand names.

        A strand pointing at a deleted composition stays broken visibly rather
        than silently adopting whatever was created next. The document only
        knows what it holds now; MainWindow._next_composition_id also
        remembers what was deleted this session.
        """
        ids = [c.id for c in self.compositions]
        return max([*ids, *composition_ids_named(self.strands)], default=0) + 1
