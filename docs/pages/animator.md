# Custom Animations {#animator}

New in 1.5. Pattern Studio's **Animate** tab builds animations rather than
picking them, and hitlib::LedStrand::compose() plays what it builds.

A custom animation is a **Composition**: an ordered stack of up to eight
*layers* over a table of up to eight *drivers*. A layer says what to draw,
where on the strip, when, and how it lands on the layers below it. A driver
is one thing the animation watches - a sensor, a song, an oscillator, or a
byte your own code sets - reduced to a single 0-255 value each tick. A
*binding* ties one to a layer's parameter.

Every preset animation is expressible as a one-layer composition, and the
Design tab's **Open as Custom** button performs that conversion on whatever
you have configured. That is not a claim in a document: Pattern Studio's test
suite runs both and compares them frame for frame.

```cpp
strand.compose(hitlib::profiles::anim::heatWash::composition);
```

---

## Playing one

```cpp
void compose(const Composition& comp);
void clearComposition();
bool composing() const;
```

A composition replaces the base animation. Brightness, the overlay buffer,
the splice mask and the profile system all keep working over it, because the
evaluator writes the same `buffer` a preset writes and leaves the strand in
the same state afterwards.

The composition and everything it points at must **outlive the strand** -
Pattern Studio emits all of it as namespace-scope constants, the same way it
emits a Profile. Re-installing the composition already playing is a no-op, so
a Sequencer phase that re-issues it every 200 ms costs a pointer comparison
rather than rebuilding every pattern five times a second.

Calling any preset - `rainbow()`, `pulse()`, `off()` - takes the base buffer
back.

## Driving one

```cpp
void setDriverLevel(uint8_t driverIdx, uint8_t value);
uint8_t getDriverLevel(uint8_t driverIdx) const;
void restartComposition();
void composeMusicSeek(uint32_t positionMs);
void composeMusicPause(bool paused = true);
uint32_t composeMusicPositionMs() const;
```

`setDriverLevel()` is the composition's `setLevel()`: it stores a byte, so it
is cheap enough to call every control-loop iteration. The export names each
driver, so robot code says what it means and a reordered driver becomes a
compile error rather than a wrong animation:

```cpp
namespace anim = hitlib::profiles::anim::heatWash;
strand.setDriverLevel(anim::driver::alert, intakeJammed ? 255 : 0);
```

A composition's MUSIC drivers and its layers' timing windows run on one
clock, so `composeMusicSeek()` moves both and `composeMusicPause()` holds
both: a paused song leaves every window where it is, and resuming carries on
from there. Motion inside a layer - a scroll, a flash - keeps running.

@note These are a separate family from `setLevel()`, `musicSeek()` and
      `setSensitivity()`, which drive the *preset* meter and do not see a
      composition.

---

## Layers

| Kind | What it draws |
|---|---|
| `OFF` | nothing - its background, or transparency under `KEY` |
| `SOLID` | one colour |
| `PULSE` | a run of colour, scrolling or bouncing |
| `FLASH` | on and off, on its own timing |
| `FLOW` | a two-colour gradient, scrolling |
| `RAINBOW` | the hue wheel, scrolling |
| `TWINKLE` | sparks from a palette |
| `BITSCROLL` | a pattern of coloured runs - **several**, unlike the preset |
| `GAUGE` | a colour scale, as a whole-span colour or a bar |
| `METER` | the level meter, scoped to a span |
| `FRAMES` | a hand-drawn strip: across is pixel, down is time. Each frame holds still or scrolls at the layer's speed, its own choice |

Each layer has a **span** (start, width, and an optional stride that repeats
it along the strip), a **blend**, an **opacity**, an optional **timing
window** with fades, and up to four **bindings**.

A repeat is never wider than its stride, so a span wider than the stride -
width 0, "to the end", included - shows its first *stride* pixels in each
repeat, and no pixel is blended twice. A **continuous** stride is one
animation seen through the repeats rather than a copy in each: its scroll,
bounce or sparkle runs along the whole strip and passes through every window.

A `GAUGE` with no stops of its own ramps `color` at `emptyAt` to `color2` at
`fullAt`, as a gauge splice region does.

### Blends

`OVER` replaces. **`KEY` is the one that makes stacking work**: a pulse over a
wash paints only its lit run and lets the wash through the gaps, where `OVER`
would blank them. Every generator reports a coverage per pixel - a bitscroll
gap, an unlit meter pixel, a half-faded spark - and `KEY` is what reads it.
`ADD`, `SCREEN` and `MULTIPLY` do the usual; `MASK` uses this layer's
brightness to decide how much of the layers below survives. Opacity and fades
blend each of them in from "not there" - a mask at half opacity does half of
what it does at full.

A converted preset blends `OVER`, deliberately: `OVER` ignores coverage, so
the layer paints its own background exactly as the preset paints its own.
Every layer *after* the first defaults to `KEY`.

### Timing

`startMs`/`endMs` place a layer inside the composition's loop; an end at or
before the start runs across the loop point. Two layers with overlapping
windows and matching fades **are** a crossfade - there is no separate
crossfade mechanism.

A window the loop never reaches - one that starts at or past the loop's
length, as shortening a loop can leave behind - never opens, and one that
ends past the loop point ends at it.

### Bindings and curves

A binding maps a driver's 0-255 onto a parameter between `atZero` and
`atFull`; `atFull` below `atZero` reverses it. An optional 17-point curve
reshapes the driver first. A curve that eases makes a binding an envelope; a
curve that **steps** makes it a threshold, which is how this design gets
conditionals without a node graph.

Bindable: opacity, speed, run length, level, start, width, density, and the
mix between a layer's two colours. A speed binding moves the pixels per step;
the time between steps (`Speed::everyMs()`) stays the layer's own.

---

## What it reproduces

Audited against `src/led_strand.cpp` and asserted frame-for-frame by
Pattern Studio's test suite.

| Preset | Result |
|---|---|
| `off`, `setColor` | **exact** |
| `pulse`, incl. bounce and invert | **exact** |
| `flash`, incl. sub-tick periods | **exact** |
| `flow`, both seam modes, both directions | **exact** |
| `rainbow`, any speed | **exact** (and a layer can reverse it, which the preset cannot) |
| `bitscroll`, all tiling and bounce modes | **exact** |
| `levelFill` + `setLevel` / `levelSource` | **exact**, partial edge pixel included |
| `musicSync` | **exact** |
| Gauge regions, both styles, both scale blends | **exact** |
| `twinkle` | **algorithmically exact, not frame-identical** - see below |
| `spliceMaskCustom`, up to 8 regions | **exact** (one layer per region) |
| `spliceMask` alternating | **approximate** - within one refresh tick per toggle |

### Twinkle

`TwinkleState::reset()` seeds one process-wide `std::rand()` shared by every
strand, so the preset is not frame-reproducible against *itself*, let alone
against the desktop preview's generator. The spawn rate, fade and hold are
identical; the individual sparks are not. Compared statistically instead.

---

## What it cannot do

Stated here rather than discovered halfway through a build.

1. **Nothing reads the previous frame.** Fire, comets and phosphor trails
   need a feedback buffer; `TWINKLE`'s fade is the only persistence in the
   design.
2. **No randomness outside `TWINKLE`** - no per-layer jitter, no random phase
   per stride repeat.
3. **No true conditionals.** A step-shaped binding curve and two layers with
   complementary opacity ranges cover most of the need.
4. **Whole-pixel motion.** A layer moves whole pixels at a time, as the
   presets do; `Speed::everyMs()` sets how often, down to one step a tick.
5. **A `FRAMES` layer scrolls but does not bounce** - a bounce needs a padded
   master pattern, which a frame strip does not build.
6. **Eight layers and eight drivers.** Past that the splice mask still exists
   and still composites *over* a composition.

---

## Cost

A worst-case composition - eight layers over 64 pixels with four curved
bindings each - is about 20,000 integer operations per tick, roughly 60 µs,
and allocates nothing, a layer restarting its window included. Set against the 12 ms that `flushBuffer()` already
spends pacing the ADI driver under a process-wide lock, that is about half a
percent of what a tick costs before the composition is there at all.

The budgets it actually spends are flash - under 2 KB of descriptor for a
full eight-layer composition, about a fifth of one baked song table - and a
one-time scratch arena sized in `compose()`. The Animate tab shows both.

@see @ref animations for the preset animations a layer reproduces
@see @ref profiles for modes and sequencing, which compose over this
