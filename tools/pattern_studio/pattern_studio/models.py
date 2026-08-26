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
}


@dataclass
class AnimationConfig:
    kind: AnimationKind = AnimationKind.RAINBOW
    color: int = 0xFF0000
    color2: int = 0x0000FF
    bg_color: int = 0x000000
    run_length: int = 5
    speed: int = 1
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


class OverlayAnimationKind(str, Enum):
    OFF = "off"
    SOLID = "solid"
    PULSE = "pulse"
    FLASH = "flash"
    FLOW = "flow"
    RAINBOW = "rainbow"
    GAUGE = "gauge"


# Human-readable labels for the overlay-kind dropdown, in display order.
OVERLAY_ANIMATION_KIND_LABELS: dict[OverlayAnimationKind, str] = {
    OverlayAnimationKind.OFF: "Off",
    OverlayAnimationKind.SOLID: "Solid Color",
    OverlayAnimationKind.PULSE: "Pulse",
    OverlayAnimationKind.FLASH: "Flash",
    OverlayAnimationKind.FLOW: "Flow (Gradient)",
    OverlayAnimationKind.RAINBOW: "Rainbow",
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
    turns a region into an independent meter following its own reading, which
    is what puts six motor-temperature gauges on one strip. The Split-mode
    overlay is a single shared buffer, so a gauge there would be one meter
    stretched over every masked bin - the panel hides the choice.
    """

    kind: OverlayAnimationKind = OverlayAnimationKind.SOLID
    color: int = 0xFFFFFF
    color2: int = 0x0000FF
    bg_color: int = 0x000000
    run_length: int = 5
    speed: int = 1
    on_ms: int = 250   # FLASH lit duration
    off_ms: int = 250  # FLASH blank duration
    # FLOW only: loop the gradient back to `color` instead of cutting straight
    # from `color2` to `color` at the wrap.
    seamless: bool = True

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
    invert: bool = False
    style: GaugeStyleKind = GaugeStyleKind.HEAT
    blend: GaugeBlendKind = GaugeBlendKind.LERP
    stops: list[GaugeStopConfig] = field(default_factory=list)
    # Preview only, never exported - see AnimationConfig's identically named
    # fields. Each region sweeps from its own phase offset so six gauges in a
    # row don't move in lockstep, which would hide exactly the difference
    # between them that the design is for.
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


@dataclass
class Document:
    """Everything one .hlprofile holds: the strands, and the song they sync to."""

    strands: list[StrandConfig] = field(default_factory=list)
    music: MusicConfig = field(default_factory=MusicConfig)
