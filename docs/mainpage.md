# HitLib {#mainpage}

**HitLib** is a VEX V5 LED animation library for [PROS](https://pros.cs.purdue.edu/).
It drives WS2812B-compatible LED strips connected to ADI ports, including strips on
ADI expanders, with a fully thread-safe, task-driven animation engine.

---

## Feature Overview

| Feature | Description |
|---|---|
| **Animations** | Static color, flow gradient, rainbow, pulse, flash, twinkle, bitscroll |
| **Bounce variants** | Pulse bounce, bitscroll bounce |
| **Overlay system** | Two independent animation layers composited by a spread mask |
| **Center spread** | Wipe animations expanding from center outward (or edges inward) |
| **Splice mask** | Split the strip into alternating sections showing different content |
| **Profiles** | Priority-based mode stack, activate/deactivate named modes at runtime |
| **Sequencer** | Multi-phase timed animation driver for endgame / match sequences |
| **Groups** | Fan-out one call to multiple strands simultaneously |
| **Brightness** | Global per-strand brightness 0–100%, applied non-destructively at flush |

---

## Quick Start

### 1. Install

```bash
pros c fetch https://github.com/advisorylabs/hitlib/releases/download/v0.6.0/hitlib@0.6.0.zip
pros c apply hitlib
```

### 2. Wire up strands and a group

```cpp
#include "hitlib/hitapi.hpp"

hitlib::LedStrand strand1(6, 63);   // ADI port 6, 63 LEDs
hitlib::LedStrand strand2(7, 63);   // ADI port 7, 63 LEDs
hitlib::LedGroup  group;

void initialize() {
    group.add(&strand1);
    group.add(&strand2);
    group.init();
    group.start();
}
```

### 3. Play an animation

```cpp
void opcontrol() {
    group.bitscroll(
        {{0xFF0000, 4}, {0x00FF00, 4}, {0x0000FF, 4}},
        /*speed*/ 2
    );
}
```

### 4. Use a built-in profile

```cpp
#include "hitlib/profiles/classic.hpp"

void initialize() {
    group.init();
    group.start();
    group.attachProfile(&hitlib::profiles::classic);
    group.activateMode(1);   // Idle
}

void autonomous() {
    group.activateModeTimed(4, 1500);   // Scoring flash for 1.5 s
}

void opcontrol() {
    group.activateMode(6);   // Endgame sequence
}
```

---

## Architecture

```
LedGroup
 └─ LedStrand <----- tick() called by group task every refreshMs
      ├─ base buffer     (flow / rainbow / pulse / bitscroll / twinkle)
      ├─ overlay buffer  (second animation layer)
      ├─ spreadMask      (CENTER_SPREAD composites base <-> overlay)
      ├─ spliceMask      (splits strip into alternating sections)
      └─ Profile / Sequencer  (event-driven mode stack)
```

---

## Pages

- \subpage install_page    "Installation"
- \subpage animations      "Animation Reference"
- \subpage profiles_page   "Profiles & Modes"
- \subpage groups_page     "Groups"
