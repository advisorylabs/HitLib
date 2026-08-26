"""Gauge splice regions: one strip carrying several independent meters.

These mirror src/led_strand.cpp's advanceSpliceRegions()/paintGaugeRegion()
path, so a change to the firmware that this file stops agreeing with is a
change that would make the preview lie about the robot.

The case they are all really about is a strip under a drivebase split into one
segment per motor, each colored by how hot its own motor is - which is the
thing a strand-wide levelFill() meter cannot do, since there is one of those
per strand and six motors on a drivebase.
"""

import math
import re
from pathlib import Path

from hitlib_sim import (
    MOTOR_HEAT_STOPS,
    GaugeBlend,
    GaugeStop,
    GaugeStyle,
    SpliceRegion,
    SpliceRegionAnimKind,
    Strand,
)

# The stop colors, by the temperature each belongs to, so a test can say "this
# segment should be showing the 55 °C color" instead of a bare hex number.
HEAT = {stop.at: stop.color for stop in MOTOR_HEAT_STOPS}


def _strip(length: int = 60) -> Strand:
    s = Strand(adi_port=1, length=length, refresh_ms=25)
    s.off()  # the base layer is fully masked; keep it black
    return s


def _settle(strand: Strand, ticks: int = 600) -> None:
    """Run long enough for a smoothed gauge to actually arrive at its reading."""
    for _ in range(ticks):
        strand.tick()


def _drive_heat(temps: list[float], *, width: int = 9, pitch: int = 10) -> Strand:
    """The six-motor drivebase design: one segment per motor, a dark pixel
    between each pair."""
    s = _strip(pitch * len(temps))
    s.splice_mask_custom([
        Strand.motor_heat_gauge(i * pitch, width, (lambda i=i: temps[i]))
        for i in range(len(temps))
    ])
    return s


def test_each_segment_follows_its_own_motor():
    """The whole point: six readings, six different colors, one strip."""
    temps = [25.0, 40.0, 50.0, 57.0, 63.0, 72.0]
    s = _drive_heat(temps)
    _settle(s)

    # Every segment shows something different, because every motor is at a
    # different temperature. A single strand-wide meter could not do this.
    shown = [s.pixels[i * 10] for i in range(6)]
    assert len(set(shown)) == 6

    # And they get hotter in the order the motors do: red climbs the whole way
    # up the scale, which is the property somebody glancing under the robot is
    # actually reading off it.
    reds = [(color >> 16) & 0xFF for color in shown[:5]]
    assert reds == sorted(reds)

    # The overheated one is pinned at the top of the scale, whatever it reads
    # past 70 - and magenta is deliberately not on the red ramp the others are.
    assert shown[5] == HEAT[70.0]


def test_a_segment_is_one_flat_color_across_its_pixels():
    s = _drive_heat([57.0])
    _settle(s)
    assert len(set(s.pixels[0:9])) == 1


def test_the_gap_between_segments_stays_dark():
    """The dark pixel is what stops two neighbouring segments at similar
    levels reading as one long one."""
    s = _drive_heat([30.0, 30.0], width=9, pitch=10)
    _settle(s)
    assert s.pixels[9] == 0x000000
    assert s.pixels[0] != 0x000000


def test_the_scale_lands_exactly_on_its_stops():
    """A reading sitting on a stop shows that stop's color, not a blend near
    it - the thresholds are the whole reason the scale has six of them."""
    for at, color in HEAT.items():
        s = _drive_heat([at])
        _settle(s)
        assert s.pixels[0] == color, f"{at} C should be {color:06X}"


def test_below_and_above_the_scale_it_holds():
    for temp, expected in ((-40.0, HEAT[20.0]), (200.0, HEAT[70.0])):
        s = _drive_heat([temp])
        _settle(s)
        assert s.pixels[0] == expected


def test_blend_slides_between_stops_and_step_holds():
    """50 °C is halfway between the 45 and 55 stops. Blend shows a mix of the
    two; Step still shows the 45 one, because 55 has not been reached."""
    half = _drive_heat([50.0])
    _settle(half)
    blended = half.pixels[0]
    assert blended not in (HEAT[45.0], HEAT[55.0])

    stepped = _strip(10)
    region = Strand.motor_heat_gauge(0, 9, lambda: 50.0)
    stepped.splice_mask_custom([SpliceRegion(**{**region.__dict__, "blend": GaugeBlend.STEP})])
    _settle(stepped)
    assert stepped.pixels[0] == HEAT[45.0]


def test_bar_style_fills_the_segment_in_proportion():
    s = _strip(10)
    s.splice_mask_custom([
        SpliceRegion(
            start=0, width=10, kind=SpliceRegionAnimKind.GAUGE,
            read=lambda: 50.0, empty_at=0.0, full_at=100.0,
            style=GaugeStyle.BAR, blend=GaugeBlend.STEP,
            bg_color=0x000000, stops=(GaugeStop(0.0, 0x00FF00),),
        )
    ])
    _settle(s)

    lit = [p for p in s.pixels[:10] if p != 0x000000]
    # Half the range, so about half the segment - the partial edge pixel means
    # "about", which is the point of it.
    assert 4 <= len(lit) <= 6


def test_a_hand_driven_gauge_follows_set_region_level():
    s = _strip(20)
    s.splice_mask_custom([
        Strand.motor_heat_gauge(0, 9),    # no reader: driven from robot code
        Strand.motor_heat_gauge(10, 9),
    ])
    s.set_region_level(1, 255)
    s.tick()

    assert s.pixels[0] == HEAT[20.0]   # untouched, still at the bottom
    assert s.pixels[10] == HEAT[70.0]  # the one that was set


def test_set_region_level_leaves_a_polled_gauge_alone():
    """A gauge with a reader would overwrite the value on the next tick, so
    setting it would only ever look like a glitch."""
    s = _drive_heat([25.0])
    s.set_region_level(0, 255)
    _settle(s, 5)
    assert s.pixels[0] != HEAT[70.0]


def test_an_unplugged_motor_holds_the_color_instead_of_reading_full():
    """PROS reports a missing device as infinity. Going dark-to-magenta on an
    unplugged port would cry wolf on the one signal that has to mean something."""
    temps = [30.0]
    s = _drive_heat(temps)
    _settle(s)
    warm = s.pixels[0]

    temps[0] = math.inf
    _settle(s, 40)
    assert s.pixels[0] == warm


def test_smoothing_creeps_toward_a_step_change():
    """The V5 reports temperature in coarse steps, so the gauge has to glide
    between them rather than jump."""
    temps = [20.0]
    s = _drive_heat(temps)
    _settle(s)
    assert s.pixels[0] == HEAT[20.0]

    temps[0] = 70.0
    s.tick()
    after_one_tick = s.pixels[0]
    assert after_one_tick != HEAT[70.0]  # not there yet

    _settle(s)
    assert s.pixels[0] == HEAT[70.0]     # but it arrives


def test_replacing_the_mask_swaps_every_gauge_at_once():
    s = _drive_heat([65.0, 65.0])
    _settle(s)
    assert s.pixels[0] == HEAT[65.0]

    s.splice_mask_custom([Strand.motor_heat_gauge(0, 9, lambda: 20.0)])
    _settle(s)
    assert s.pixels[0] == HEAT[20.0]
    # The second segment's pixels belong to nobody now, so it stops overriding
    # them and the base animation shows through again.
    assert s.pixels[10] == 0x000000


def test_a_gauge_with_no_stops_falls_back_to_two_colors():
    s = _strip(10)
    s.splice_mask_custom([
        SpliceRegion(
            start=0, width=10, kind=SpliceRegionAnimKind.GAUGE,
            read=lambda: 100.0, empty_at=0.0, full_at=100.0,
            color=0x00FF00, color2=0xFF0000,
        )
    ])
    _settle(s)
    assert s.pixels[0] == 0xFF0000


def test_gauges_coexist_with_ordinary_animated_regions():
    """A gauge is one region kind among several - the segment next to it can
    still be doing something completely unrelated."""
    s = _strip(20)
    s.splice_mask_custom([
        Strand.motor_heat_gauge(0, 9, lambda: 70.0),
        SpliceRegion(start=10, width=9, kind=SpliceRegionAnimKind.RAINBOW, speed=1),
    ])
    _settle(s, 10)

    assert s.pixels[0] == HEAT[70.0]
    # The rainbow region is still animating on its own clock.
    assert len(set(s.pixels[10:19])) > 1


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_the_motor_heat_scale_matches_the_one_the_firmware_ships():
    """MOTOR_HEAT_STOPS is a port of LedStrand::motorHeatGauge()'s table, and
    Pattern Studio's Motor Temperature preset is built from it - so a scale
    edited on one side and not the other would make an export come out looking
    different from the design it was taken from.
    """
    source = (REPO_ROOT / "src" / "led_strand.cpp").read_text(encoding="utf-8")
    table = source.split("LedStrand::motorHeatGauge")[1].split("r.stops = {")[1].split("};")[0]
    in_cpp = [(float(at), int(color, 16))
              for at, color in re.findall(r"\{\s*([0-9.]+),\s*(0x[0-9A-Fa-f]{6})\s*\}", table)]

    assert in_cpp == [(stop.at, stop.color) for stop in MOTOR_HEAT_STOPS]
    # And the range the stops are placed across agrees too.
    assert ".emptyAt = 20.0" not in table  # the range lives above the table
    factory = source.split("LedStrand::motorHeatGauge")[1].split("return r;")[0]
    assert "r.emptyAt = 20.0;" in factory and "r.fullAt  = 70.0;" in factory
