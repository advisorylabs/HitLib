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

Generates a gradient between two colours and scrolls it continuously.
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
// Moving run of colour over a background
strand.pulse(0xFF0000, /*runLength*/ 5, /*speed*/ 1);
strand.pulse(0xFF0000, 5, 1, /*bgColor*/ 0x330000);

// Bounce — run reverses direction at each end
strand.pulse(0xFF0000, 5, 1, 0x000000, /*invert*/ false, /*bounce*/ true);
```

---

## Flash

```cpp
strand.flash(0x00FF00, /*speed*/ 2);
strand.flash(0x00FF00, 2, /*bgColor*/ 0x003300);
```

Scrolls a full-strip block of colour followed by `speed` strips of background,
creating a repeating flash effect.

---

## Twinkle

```cpp
strand.twinkle(
    {0xFFFFFF, 0xFFDD88},   // colour palette
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
    /*spacing*/  2,        // gap pixels between segments
    /*repeating*/ true     // tile pattern vs single pass
);

// Bounce — pattern rocks back and forth
strand.bitscroll(segments, 2, false, 0, /*bounce*/ true);
```

---

## Overlay Animations

A second animation buffer that is composited over the base by the
[center spread](\ref LedStrand::centerSpread) mask.

```cpp
strand.overlaySetColor(0xFFFFFF);
strand.overlayRainbow(1);
strand.overlayPulse(0x0000FF, 5, 1);
strand.overlayFlow(0xFF0000, 0x0000FF, 1);
strand.overlayFlash(0xFFFFFF, 2);
```

---

## Center Spread

Reveals the overlay buffer from the center outward (or edges inward).

```cpp
// Set up base, then overlay, then trigger the spread
strand.flow(0xFF00DD, 0x000000, 1);
strand.overlayRainbow(1);
strand.centerSpread(/*tickInterval*/ 8);

// Edges → center
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

## Splice Mask

Splits the strip into equal-width bins; masked bins show `bgColor` (or the overlay
buffer if one is active), unmasked bins show the base animation.

```cpp
// Two halves: left shows animation, right shows bgColor
strand.spliceMask(1);

// Alternating — toggles every 100 ms (creates a strobe/interleave effect)
strand.spliceMask(3, false, /*alternating*/ true, /*periodMs*/ 100);

// Clear
strand.clearSpliceMask();
```

---

## Brightness

```cpp
strand.setBrightness(60);   // 60% — applied non-destructively at flush time
strand.setBrightness(100);  // restore full brightness
```
