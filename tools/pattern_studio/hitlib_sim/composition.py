"""Port of LedStrand's Composition types - the 1.5 custom-animation model.

Mirrors include/hitlib/led_strand.hpp field for field, in the same style as
SpliceRegion in strand.py: frozen dataclasses with the firmware's own names,
so a reader can hold the two side by side.

The evaluator itself lives on Strand (see strand.py's `compose` and the
`_advance_composition` family), because it writes the strand's buffer and
reaches the same generator bodies the presets do. Keeping the *descriptors*
here keeps strand.py from growing another 200 lines of plain data.

Defaults exist here and deliberately do not exist in the C++: Layer and Driver
are C++11 aggregates with no default member initializers, which is what lets
the exported table be brace-initialized identically in both dialects and live
in flash. A user-facing default belongs in Pattern Studio, and a test-facing
one belongs here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Optional, Sequence

from .strand import (
    BitScrollSegment,
    GaugeBlend,
    GaugeStop,
    GaugeStyle,
    MusicTrack,
    Speed,
    _StepPacer,
)

#: Ceilings, matching the static constexpr members on LedStrand.
MAX_LAYERS = 8
MAX_DRIVERS = 8
MAX_BINDINGS = 4
CURVE_POINTS = 17


class LayerKind(Enum):
    """Port of LedStrand::LayerKind.

    OFF through GAUGE are SpliceRegionAnimKind entry for entry: a layer and a
    splice region say the same words and take the same parameters. METER is
    level_fill()'s meter scoped to a span. FRAMES is a hand-drawn pixel strip.
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
    METER = auto()
    FRAMES = auto()


class LayerBlend(Enum):
    """Port of LedStrand::LayerBlend - how a layer lands on the layers below.

    KEY is the one that makes stacking work: a PULSE over a wash paints only
    its lit run and lets the wash through the gaps, where OVER would blank
    them. Every generator reports a coverage per pixel and KEY is what reads
    it.
    """

    OVER = auto()
    KEY = auto()
    ADD = auto()
    SCREEN = auto()
    MULTIPLY = auto()
    MASK = auto()


class DriverKind(Enum):
    CONSTANT = auto()
    SENSOR = auto()
    MUSIC = auto()
    LFO = auto()


class LfoShape(Enum):
    TRIANGLE = auto()
    SAW = auto()
    RAMP_DOWN = auto()
    SQUARE = auto()
    SINE = auto()


class LayerParam(Enum):
    NONE = auto()
    OPACITY = auto()
    SPEED = auto()
    RUN_LENGTH = auto()
    LEVEL = auto()
    START = auto()
    WIDTH = auto()
    DENSITY = auto()
    MIX = auto()


@dataclass(frozen=True)
class Binding:
    """Ties one driver to one parameter of one layer.

    The parameter is `at_zero` when the driver reads 0 and `at_full` when it
    reads 255; `at_full` below `at_zero` reverses it, the same way
    level_source()'s full_at below empty_at reverses a meter. `curve`, when
    given, is CURVE_POINTS bytes that reshape the driver's 0-255 first - a
    curve that steps makes a binding a threshold, one that eases makes it an
    envelope.
    """

    param: LayerParam = LayerParam.NONE
    driver: int = 0
    at_zero: int = 0
    at_full: int = 255
    curve: Optional[Sequence[int]] = None


@dataclass(frozen=True)
class Layer:
    """Port of LedStrand::Layer, flattened.

    The C++ groups these into nested sub-structs so an unused group can be
    written `{}` and the emitted table reads as a shape rather than a wall of
    forty fields. Python has keyword arguments and defaults, so the grouping
    would only add depth to every access; the names are otherwise identical.
    """

    kind: LayerKind = LayerKind.OFF
    name: str = ""

    # span
    start: int = 0
    width: int = 0          # 0 means "to the end of the strand"
    stride: int = 0         # 0 draws one span; otherwise repeat every N pixels
    continuous: bool = False

    # mix
    blend: LayerBlend = LayerBlend.OVER
    opacity: int = 255

    bindings: tuple[Binding, ...] = ()

    # paint
    color: int = 0xFFFFFF
    color2: int = 0x0000FF
    bg_color: int = 0x000000

    # motion. `speed` is a Speed or a bare pixels-per-tick int; a SPEED
    # binding moves its pixels, never its step time.
    run_length: int = 5
    speed: int | Speed = 1
    invert: bool = False
    bounce: bool = False
    seamless: bool = True

    # flash
    on_ms: int = 250
    off_ms: int = 250

    # time
    loop_ms: int = 0        # 0 uses the composition's
    start_ms: int = 0
    end_ms: int = 0
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    retrigger: bool = False

    # sparkle
    palette: tuple[int, ...] = ()
    density_pct: int = 30
    fade_step: int = 16

    # pattern
    segments: tuple[BitScrollSegment, ...] = ()
    spacing: int = 5
    repeating: bool = True

    # gauge / meter
    level: int = 0
    gradient: bool = False
    style: GaugeStyle = GaugeStyle.HEAT
    gauge_blend: GaugeBlend = GaugeBlend.LERP
    stops: tuple[GaugeStop, ...] = ()
    empty_at: float = 0.0
    full_at: float = 100.0

    # frames
    frame_data: bytes = b""
    frame_index: tuple[int, ...] = ()
    frame_palette: tuple[int, ...] = ()
    frame_width: int = 0
    frame_count: int = 0
    frame_ms: int = 100
    #: One flag per frame: non-zero scrolls that frame at `speed`. Empty
    #: holds every frame still.
    frame_scroll: tuple[int, ...] = ()


@dataclass(frozen=True)
class Driver:
    """One thing a composition watches, reduced to a byte per tick.

    The three drivers that already exist on Strand, plus an oscillator.
    SENSOR is level_source()'s reader, range, wrap and smoothing; MUSIC is
    music_sync()'s track and sensitivity; CONSTANT is set_level().
    """

    kind: DriverKind = DriverKind.CONSTANT
    name: str = ""
    value: int = 0
    read: Optional[Callable[[], float]] = None
    empty_at: float = 0.0
    full_at: float = 100.0
    wrap: bool = False
    smoothing: int = 0
    track: Optional[MusicTrack] = None
    sensitivity: int = 100
    loop: bool = False
    shape: LfoShape = LfoShape.TRIANGLE
    period_ms: int = 1000
    phase_pct: int = 0


@dataclass(frozen=True)
class Composition:
    """A named, reusable custom animation: layers over a driver table and a
    clock. Layer 0 is the bottom."""

    name: str = ""
    layers: tuple[Layer, ...] = ()
    drivers: tuple[Driver, ...] = ()
    loop_ms: int = 0
    bg_color: int = 0x000000


@dataclass
class LayerState:
    """What one layer remembers between ticks - port of LedStrand::LayerState.

    `scratch` stands in for the C++ arena slice. The firmware packs every
    layer's scratch into one grow-only block because a tick there must not
    allocate; Python has no such constraint, so each layer keeps its own list
    and the *contents* are what have to match, not the allocation strategy.
    """

    shift_acc: int = 0
    bounce_pos: int = 0
    bounce_dir: int = 1
    flash_counter: int = 0
    flash_lit: bool = True
    was_open: bool = False
    frame_idx: int = 0
    frame_counter: int = 0
    step: _StepPacer = field(default_factory=_StepPacer)
    content_len: int = 0
    max_span: int = 0
    scratch: list[int] = field(default_factory=list)
    twinkle: object = None
    stops: list[tuple[int, int]] = field(default_factory=list)
    #: Sim only: the colors this layer's pixels index into this tick, and
    #: what they were worked out for - see Strand._refresh_ramp(). The
    #: firmware works each pixel out as it goes, which a tick on a Cortex-A9
    #: affords and a Python preview of eight strands does not.
    ramp_key: object = None
    ramp: list[int] = field(default_factory=list)


@dataclass
class Resolved:
    """A layer's bindable parameters after its bindings have been applied."""

    start: int = 0
    width: int = 0
    run_length: int = 0
    speed: int = 0
    density_pct: int = 0
    opacity: int = 255
    level: int = 0
    mix: int = 0
