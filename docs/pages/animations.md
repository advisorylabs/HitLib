# Animation Reference {#animations}

Every animation is available on both hitlib::LedStrand and hitlib::LedGroup.
Group calls fan out to every strand in the group simultaneously.

All calls are **thread-safe** and take effect on the next refresh tick.

---

## Static

```cpp
strand.setColor(0xFF0000);   // solid red
strand.off();                // all pixels off (alias for setColor(0))
```

---

## Flow (gradient scroll)

```cpp
strand.flow(0xFF00DD, 0x000000, /*speed*/ 1);
strand.flow(0xFF00DD, 0x000000, /*speed*/ 1, /*invert*/ true);
```

Generates a gradient between two colors and scrolls it continuously.
`invert` reverses the scroll direction.

---

## Rainbow

```cpp
strand.rainbow(/*speed*/ 1);
```

Full HSV rainbow scrolled across the strip.

---

## Pulse

```cpp
// Moving run of color over a background
strand.pulse(0xFF0000, /*runLength*/ 5, /*speed*/ 1);
strand.pulse(0xFF0000, 5, 1, /*bgColor*/ 0x330000);

// Bounce, run reverses direction at each end
strand.pulse(0xFF0000, 5, 1, 0x000000, /*invert*/ false, /*bounce*/ true);
```

---

## Flash

```cpp
strand.flash(0x00FF00, /*onMs*/ 100, /*offMs*/ 100);
strand.flash(0x00FF00, 100, 400, /*bgColor*/ 0x003300);  // short blip, long gap
```

Blinks the whole strip: every LED lights at once for `onMs`, then the whole
strip shows `bgColor` for `offMs`, and the cycle repeats.

On and off times are independent, so blink rate and duty cycle are set
separately. `100, 100` is an even 5 Hz blink, while `100, 400` keeps the same
brief flash at 2 Hz. Durations are rounded to whole refresh ticks and clamped
to a minimum of one tick, so a strand can't be asked to blink faster than its
`refreshMs` interval.

---

## Twinkle

```cpp
strand.twinkle(
    {0xFFFFFF, 0xFFDD88},   // color palette
    /*densityPct*/ 30,       // 0–100, percentage of LEDs lit at once
    /*fadeStep*/   16,        // brightness step per tick (higher = faster fade)
    /*bgColor*/    0x000000
);
```

Randomly spawns sparkles from the palette that fade in, hold briefly, then fade out.

---

## Bitscroll

```cpp
// Segments scroll off one end and wrap around
strand.bitscroll(
    {{0xFF0000, 4}, {0x00FF00, 4}, {0x0000FF, 4}},
    /*speed*/    2,
    /*invert*/   false,
    /*bgColor*/  0x000000,
    /*bounce*/   false,
    /*spacing*/  2,        // gap pixels between segments (default 5)
    /*repeating*/ true     // tile pattern vs a single copy
);

// Bounce: pattern rocks back and forth
strand.bitscroll(segments, 2, false, 0, /*bounce*/ true);

// Bounce a single copy of the pattern instead of a tiled one
strand.bitscroll(segments, 2, false, 0, /*bounce*/ true, /*spacing*/ 5,
                 /*repeating*/ false);
```

`repeating` applies to both travel styles: with `true` the pattern tiles across
the whole strip, with `false` a single copy travels the strip on its own.

---

## Overlay Animations

A second animation buffer, independent of the base animation. It's composited
over the base by the [center spread](\ref LedStrand::centerSpread) mask, and/or
shown directly in [splice mask](\ref LedStrand::spliceMask) regions that set
`useOverlay`.

```cpp
strand.overlaySetColor(0xFFFFFF);
strand.overlayRainbow(1);
strand.overlayPulse(0x0000FF, 5, 1);
strand.overlayFlow(0xFF0000, 0x0000FF, 1);
strand.overlayFlash(0xFFFFFF, 100, 100);
```

---

## Center Spread

Reveals the overlay buffer from the center outward (or edges inward).

```cpp
// Set up base, then overlay, then trigger the spread
strand.flow(0xFF00DD, 0x000000, 1);
strand.overlayRainbow(1);
strand.centerSpread(/*tickInterval*/ 8);

// Edges -> center
strand.centerSpread(8, /*invert*/ true);

// Bounce: expand fully, contract, then swap layers
strand.centerSpreadBounce(8);

// Cycle through an arbitrary list of setup functions automatically
strand.centerSpreadStacked({layerA, layerB, layerC}, 8);
strand.centerSpreadBounceStacked({layerA, layerB}, 8);
```

`tickInterval` controls speed: at `refreshMs=20`, `tickInterval=10` advances one
pixel every 200 ms (~6 s across 63 LEDs).

---

## Level Meter

Fills the strip from one end in proportion to a 0-255 level, and dims the LED
at the edge of the fill part-way so a short strand still shows a smooth ramp
rather than one step per pixel.

```cpp
// Set the colors once...
strand.levelFill(0x00FF88);                                  // one color
strand.levelFill(0x00FF00, 0xFF0000, /*gradient*/ true);      // green at the bottom, red at the top
strand.levelFill(0x00FF88, 0x000000, false, 0x050505, /*invert*/ true);  // fills from the far end

// ...then drive it from wherever the number comes from.
strand.setLevel(intakeVelocity * 255 / maxVelocity);
```

With `gradient`, the two colors are laid out across the **whole strip**, not
across the lit part, so a given pixel is always the same color no matter how
full the meter is. That is what makes a VU-style scale work; a gradient
stretched over the fill would recolor every pixel on every update.

Three things can move a meter, and they are mutually exclusive - whichever was
set up last owns it, so a strip handed to a song never has a stale sensor
fighting it for the same pixels:

| | Set up with | Moved by |
|---|---|---|
| **By hand** | `levelFill()` | your code calling `setLevel()` |
| **From a value** | `levelSource()` | the strand, once per tick |
| **From a song** | `musicSync()` | the baked envelope, against the wall clock |

### Following a value

`levelSource()` hands the strand a reader and the range it maps onto the
strip. From then on the bar tracks the value with no code in your control
loop at all:

```cpp
pros::Motor intake(11);

strand.levelFill(0x00FF00, 0xFF0000, /*gradient*/ true);     // green -> red scale
strand.levelSource([] { return intake.get_temperature(); },
                   20.0, 70.0);                              // cool -> shutdown hot
```

The two bounds are in whatever units the reader already speaks - degrees,
percent, millimetres, RPM - so nothing has to be scaled into 0-255 first:

```cpp
// A fuel gauge, empty when the battery is.
strand.levelSource([] { return pros::battery::get_capacity(); }, 0.0, 100.0);

// A bar that fills once per revolution and starts over.
strand.levelSource([] { return arm.get_position(); }, 0.0, 360.0, /*wrap*/ true);

// Full at 0: a "distance remaining" bar that drains as the number climbs.
strand.levelSource([] { return (double)goal.get(); }, 2000.0, 0.0);

// A noisy reading, glided rather than snapped.
strand.levelSource([] { return intake.get_actual_velocity(); },
                   0.0, 200.0, false, /*smoothing*/ 60);
```

Set it up once - in `initialize()`, or in a mode's `onActivate` so the meter
follows that mode - and call `clearLevelSource()` or `setLevel()` to take it
back by hand.

A few details worth knowing:

- **Outside the range** the bar clamps to empty or full, so a gauge parks at
  either end. `wrap` cycles instead, which is what a continuously turning
  motor or a heading wants: 450° of a 0-360 range shows a quarter full, not
  full.
- **`smoothing`** (0-99) is how much of the previous frame's fill to keep each
  tick. The bar still reaches its target, it just takes a few ticks - and a
  wrapping meter takes the short way round rather than sweeping back down the
  whole strip to roll over.
- **The reader runs on the LED task**, not yours, so it must not block. It has
  to return a `double`: a reading that comes back as an integer needs saying
  so, `[] { return (double)rot.get_angle(); }`.
- **An unplugged device** reports `PROS_ERR_F`, which is infinity. The meter
  holds where it is rather than slamming to full.

[Pattern Studio](\ref install_page) has this as its **Fill** animation: pick
what the bar follows from a list of the things a V5 robot actually has -
battery capacity, motor heat, position, velocity or efficiency, a rotation
sensor, an IMU heading, a distance sensor, a potentiometer - and the exported
header carries the reader already written, including the `#include` it needs.
Anything not on the list exports as a `LevelFn` hook to assign from your own
code, and the export's banner spells out the assignment.

---

## Music Sync

The same meter, driven by a song instead of by hand.

The V5 can neither hear audio nor decode a song, so it is resolved on the
desktop. [Pattern Studio](\ref install_page) reads an audio file - MP3, M4A,
FLAC, OGG, WAV - or a MIDI, measures its loudness in three frequency bands, and
exports each band a strand uses as a table of one byte per frame. On the robot,
playback is an array lookup and a lerp per tick, no matter how long the song is.

```cpp
#include "my_show.hpp"   // exported by Pattern Studio, defines music::anthemBass

void startShow(hitlib::LedStrand& s) {
    s.musicSync(
        hitlib::profiles::music::anthemBass,
        0x00FF88, 0xFF0044, /*gradient*/ true,   // fill colors
        0x000000,                                 // unfilled background
        /*invert*/ false,
        /*sensitivity*/ 100,                      // % gain on the envelope
        /*loop*/ false
    );
}
```

Playback is anchored to the wall clock at the moment `musicSync()` runs, so
call it when the music starts, typically from a mode's `onActivate`. Samples
are interpolated between frames, so the fill stays smooth even when the
envelope's frame rate is coarser than `refreshMs`.

### Bands

Each strand picks the part of the song it follows, so a robot with two strips
can run them off one track without them moving together:

| Band | What it follows |
|---|---|
| **Bass** | Kick and bass. Pumps on the beat, and what most strips want. |
| **Mid** | Vocals, snare, guitars. Follows the body of the arrangement. |
| **Treble** | Hats and cymbals. Sparse and sparkly. |
| **Full mix** | Everything at once. Steadiest, least rhythmic. |

Only the bands a design actually uses are exported, so following the bass alone
costs one table rather than four.

### Sensitivity

A percentage gain applied to every sample: above 100 the quiet passages reach
further up the strip (and loud ones clip at full), below 100 only the peaks
register. It is the one knob worth retuning on the robot, so it has its own
setter and needs no re-export:

```cpp
strand.setSensitivity(160);   // punchier
strand.musicSeek(30000);      // jump 30 s in
strand.musicPause();          // hold the meter where it is
strand.musicPause(false);     // and carry on
```

Everything about *how* the song drives the strip - which band, how hard it
punches, how fast it falls away - is decided in Pattern Studio and baked into
the table. A three-minute song at the default 25 ms frame is about 8 KB of
flash per band.

---

## Splice Mask

Overrides part of the strip, either as equal alternating bins sharing the
[overlay](\ref LedStrand::overlaySetColor) buffer (`spliceMask`) or as
arbitrarily placed regions that each animate independently
(`spliceMaskCustom`). The two forms are mutually exclusive whichever was
called most recently is what's active.

```cpp
// Two halves: left shows animation, right shows bgColor
strand.spliceMask(1);

// Alternating, toggles every 100 ms (creates a strobe/interleave effect)
strand.spliceMask(3, false, /*alternating*/ true, /*periodMs*/ 100);

// Masked bins show the overlay animation instead of a solid color
strand.overlayRainbow(1);
strand.spliceMask(1, false, false, 100, 0x000000, /*useOverlay*/ true);

// Custom: arbitrary, non-alternating regions, each with its own animation --
// unlike the overlay above, these run independently and simultaneously.
using Kind = LedStrand::SpliceRegionAnimKind;
strand.spliceMaskCustom({
    {.start = 0,  .width = 5, .kind = Kind::SOLID,   .color = 0xFF0000},              // solid red
    {.start = 10, .width = 8, .kind = Kind::RAINBOW, .speed = 1},                      // its own rainbow
    {.start = 20, .width = 6, .kind = Kind::PULSE,   .color = 0x00FF00, .runLength = 3, .speed = 2},
});

// Clear (either kind)
strand.clearSpliceMask();
```

### Gauge regions

A `GAUGE` region is the odd one out: it animates from a *reading* rather than
from a clock. Each one polls its own value every tick and colors itself off its
own scale, so a single strip can carry several independent meters -- which the
strand-wide [level meter](
ef LedStrand::levelFill) cannot do, there being one of those per
strand.

The scale is a list of stops given in the reading's own units, so it says what
it means. Below the first stop and above the last, the gauge holds that stop's
color, so a scale never has to cover a range you don't care about.

```cpp
using Kind = LedStrand::SpliceRegionAnimKind;

pros::Motor intake(11);
double intakeTemp() { return intake.get_temperature(); }

void showIntakeHeat(LedStrand& strand) {
    strand.spliceMaskCustom({
        {.start = 0, .width = 9, .kind = Kind::GAUGE,
         .read = intakeTemp, .emptyAt = 20.0, .fullAt = 70.0, .smoothing = 80,
         .stops = {{20.0, 0x00FF00}, {55.0, 0xFF7000}, {70.0, 0xFF00FF}}},
    });
}
```

`style` picks what the region does with its level: `HEAT` (the default) colors
every pixel it owns at once, and `BAR` fills the region proportionally instead,
like a miniature `levelFill()`. `blend` picks what happens *between* two stops:
`LERP` slides between their colors, and `STEP` holds each one until the next is
actually reached -- the honest choice when the stops are thresholds something
crosses rather than points on a ramp.

Leave `read` as `nullptr` and the region is hand-driven instead, through
[setRegionLevel()](\ref LedStrand::setRegionLevel) -- `setLevel()`'s
counterpart for one region.

### Motor heat, one segment per motor

The case gauges exist for: a strip under the drivebase, split into one segment
per motor, each colored by how hot its own motor is.
[motorHeatGauge()](\ref LedStrand::motorHeatGauge) is a gauge region arriving
pre-loaded with the V5's own derating schedule, so the colors mean something
specific rather than being a pretty ramp -- green cold, yellow nearing the first
cut, then orange, red and deep red for the 50%, 25% and 12.5% current limits,
and magenta at 70 °C where the motor shuts down.

```cpp
// Front to back down the left side, then the right - the order the LEDs run in.
pros::Motor drive[6] = {pros::Motor(1),  pros::Motor(2),  pros::Motor(3),
                        pros::Motor(11), pros::Motor(12), pros::Motor(13)};

// A LevelFn is a plain function pointer and so cannot capture an index, which
// is why each segment gets its own one-line reader.
double heat0() { return drive[0].get_temperature(); }
double heat1() { return drive[1].get_temperature(); }
double heat2() { return drive[2].get_temperature(); }
double heat3() { return drive[3].get_temperature(); }
double heat4() { return drive[4].get_temperature(); }
double heat5() { return drive[5].get_temperature(); }
LedStrand::LevelFn readers[6] = {heat0, heat1, heat2, heat3, heat4, heat5};

void showDriveHeat(LedStrand& strand) {
    std::vector<LedStrand::SpliceRegion> segments;
    for (uint8_t i = 0; i < 6; ++i) {
        // 9 lit pixels out of every 10: the tenth is left uncovered as a dark
        // divider, without which two neighbouring segments at similar
        // temperatures read as one long one.
        segments.push_back(LedStrand::motorHeatGauge(i * 10, 9, readers[i]));
    }
    strand.off();                      // the base layer is fully masked
    strand.spliceMaskCustom(segments);
}
```

The V5 reports motor temperature in coarse steps rather than as a continuous
reading, which is why the preset ships with `smoothing` at 80: without it a
segment jumps from one stop color straight to the next instead of creeping
between them.

Pattern Studio builds the whole thing without any of this being typed: set one
region up as a Gauge on Motor Temperature (which brings the six stops with it),
then press **Divide** to lay out however many equal segments the strip holds and
change each one's port.

---

## Brightness

```cpp
strand.setBrightness(60);   // 60% applied non-destructively at flush time
strand.setBrightness(100);  // restore full brightness
```
