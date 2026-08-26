"""The things a Fill animation can follow, and the C++ each one generates.

A Fill meter is only useful if it is easy to point at something real, so this
is a catalog of the readings a VEX robot actually has - battery capacity, motor
heat, an arm's rotation - each with the range that makes a sensible bar out of
it and the one-line reader the export emits for it. Picking "Motor Temperature"
and a port in the GUI is therefore enough to produce code that compiles and
runs with nothing to fill in by hand.

Two entries are not devices:

  * MANUAL - no reader at all, the robot's own code calls setLevel().
  * CUSTOM - the export emits a null LevelFn the user assigns from their code,
    which covers everything not in the list (a computed value, a class member,
    a device this catalog doesn't know) without making them edit generated
    code that the next export would overwrite.

Everything downstream reads this table rather than hard-coding source names:
the inspector builds its dropdown from ORDER, codegen renders `expr`/`device`,
and the preview uses the ranges to sweep something plausible.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hitlib_sim import MOTOR_HEAT_STOPS

MANUAL = "manual"
CUSTOM = "custom"

#: Port spaces a source can want. Smart ports are 1-21 on the brain, ADI ports
#: 1-8 ('A'-'H'), and "" means the source needs no port at all.
PORT_NONE = ""
PORT_SMART = "smart"
PORT_ADI = "adi"


@dataclass(frozen=True)
class FillSource:
    """One selectable driver for a Fill meter."""

    id: str
    label: str
    #: PORT_NONE / PORT_SMART / PORT_ADI - decides whether the Port field shows
    #: and what range it allows.
    port_kind: str = PORT_NONE
    #: Unit suffix on the Empty/Full fields, so the numbers say what they mean.
    unit: str = ""
    #: Range that makes a useful bar out of this reading, pre-filled when the
    #: source is picked. Overridable - they are defaults, not limits.
    empty_default: int = 0
    full_default: int = 100
    #: Continuously turning readings (a motor's position, a heading) want to
    #: cycle rather than pin at full.
    wrap_default: bool = False
    #: A device type that takes a port, plus the call to make on it. The export
    #: holds the device in a function-local static so it is constructed once.
    device: str = ""
    call: str = ""
    #: A portless expression, used instead of device/call.
    expr: str = ""
    #: PROS header the generated reader needs, added to the export's includes.
    include: str = ""
    #: One line shown under the dropdown - what this bar ends up showing.
    hint: str = ""
    #: Default color scale for a Gauge region on this source, in its own units.
    #: Empty means a gauge starts as a plain two-color ramp across the range,
    #: which is all most readings warrant - a scale is worth having when the
    #: numbers themselves have meanings, as a motor's temperatures do.
    stops: tuple[tuple[float, int], ...] = field(default_factory=tuple)


_SOURCES: tuple[FillSource, ...] = (
    FillSource(
        id=MANUAL,
        label="Manual (setLevel)",
        hint="Your code calls setLevel(0-255). Nothing polls anything.",
        empty_default=0,
        full_default=255,
    ),
    FillSource(
        id="battery",
        label="Battery Capacity",
        unit=" %",
        empty_default=0,
        full_default=100,
        expr="pros::battery::get_capacity()",
        include="pros/misc.hpp",
        hint="A fuel gauge - the bar empties as the battery does.",
    ),
    FillSource(
        id="motor_temp",
        label="Motor Temperature",
        port_kind=PORT_SMART,
        unit=" °C",
        # V5 motors start derating around 55 C and cut out near 70, so a bar
        # that reads full at 70 is a bar that reads full when it matters.
        empty_default=20,
        full_default=70,
        device="pros::Motor",
        call="get_temperature()",
        include="pros/motors.hpp",
        hint="Fills as a motor heats up - full is roughly where it shuts down.",
        # The V5's own derating schedule, shared with LedStrand::motorHeatGauge()
        # rather than restated, so the GUI preset and the C++ one cannot drift.
        stops=tuple((stop.at, stop.color) for stop in MOTOR_HEAT_STOPS),
    ),
    FillSource(
        id="motor_position",
        label="Motor Position",
        port_kind=PORT_SMART,
        unit="°",
        empty_default=0,
        full_default=360,
        wrap_default=True,
        device="pros::Motor",
        call="get_position()",
        include="pros/motors.hpp",
        hint="Fills as a motor turns. With Wrap on, one bar per revolution.",
    ),
    FillSource(
        id="motor_velocity",
        label="Motor Velocity",
        port_kind=PORT_SMART,
        unit=" rpm",
        empty_default=0,
        full_default=200,
        device="pros::Motor",
        call="get_actual_velocity()",
        include="pros/motors.hpp",
        hint="A speedometer. Worth some smoothing - the reading is noisy.",
    ),
    FillSource(
        id="motor_efficiency",
        label="Motor Efficiency",
        port_kind=PORT_SMART,
        unit=" %",
        empty_default=0,
        full_default=100,
        device="pros::Motor",
        call="get_efficiency()",
        include="pros/motors.hpp",
        hint="Drops as a motor works against a load - a stall warning bar.",
    ),
    FillSource(
        id="rotation",
        label="Rotation Sensor",
        port_kind=PORT_SMART,
        unit=" c°",
        # get_angle() is centidegrees: a full turn is 36000, not 360.
        empty_default=0,
        full_default=36000,
        wrap_default=True,
        device="pros::Rotation",
        call="get_angle()",
        include="pros/rotation.hpp",
        hint="Absolute shaft angle in centidegrees - 36000 is one full turn.",
    ),
    FillSource(
        id="imu_heading",
        label="IMU Heading",
        port_kind=PORT_SMART,
        unit="°",
        empty_default=0,
        full_default=360,
        wrap_default=True,
        device="pros::Imu",
        call="get_heading()",
        include="pros/imu.hpp",
        hint="Which way the robot faces, as a bar that goes round with it.",
    ),
    FillSource(
        id="distance",
        label="Distance Sensor",
        port_kind=PORT_SMART,
        unit=" mm",
        empty_default=0,
        full_default=2000,
        device="pros::Distance",
        call="get()",
        include="pros/distance.hpp",
        hint="How far away something is. Swap Empty/Full for a proximity bar.",
    ),
    FillSource(
        id="potentiometer",
        label="Potentiometer (ADI)",
        port_kind=PORT_ADI,
        unit="°",
        empty_default=0,
        full_default=250,
        device="pros::adi::Potentiometer",
        call="get_angle()",
        include="pros/adi.hpp",
        hint="An analog angle - a lift or arm's position on an ADI port.",
    ),
    FillSource(
        id=CUSTOM,
        label="Custom (assign in code)",
        empty_default=0,
        full_default=100,
        hint="The export leaves a hook you point at anything returning a number.",
    ),
)

#: id -> FillSource.
SOURCES: dict[str, FillSource] = {s.id: s for s in _SOURCES}

#: Ids in dropdown order.
ORDER: list[str] = [s.id for s in _SOURCES]


def get(source_id: str) -> FillSource:
    """The source `source_id` names, falling back to Manual.

    Falling back rather than raising keeps a file written by a newer version of
    Pattern Studio (or hand-edited) loadable: the strand comes back as a
    hand-driven meter instead of failing the whole load.
    """
    return SOURCES.get(source_id, SOURCES[MANUAL])


def port_range(source_id: str) -> tuple[int, int]:
    """Valid port numbers for a source's port space. (0, 0) when it needs none."""
    kind = get(source_id).port_kind
    if kind == PORT_SMART:
        return 1, 21
    if kind == PORT_ADI:
        return 1, 8
    return 0, 0


def default_stops(source_id: str) -> list[tuple[float, int]]:
    """The color scale a Gauge region starts with for a source.

    A list rather than the stored tuple because the caller edits it: this is a
    starting point to adjust, not a fixed scale.
    """
    return [(at, color) for at, color in get(source_id).stops]


def polls_a_device(source_id: str) -> bool:
    """Whether this source generates a reader at all.

    False for Manual (the robot drives the meter itself) - everything else,
    Custom included, exports a levelSource() call.
    """
    return source_id != MANUAL
