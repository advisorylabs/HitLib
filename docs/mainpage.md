# HitLib {#mainpage}

**HitLib** is a VEX V5 LED animation library for [PROS](https://pros.cs.purdue.edu/).
It drives WS2812B-compatible LED strips connected to ADI ports, including strips on
ADI expanders, with a fully thread-safe, task-driven animation engine.

---

## Getting Started

New to HitLib? Start here:

1. \subpage install_page "Installation" - wire up your strip and install the template
2. \subpage groups_page "Groups" - understand strands and groups before writing code
3. \subpage animations "Animation Reference" - every animation with examples
4. \subpage profiles_page "Profiles & Modes" - event-driven animation for competition

---

## What is HitLib?

HitLib gives you a two-layer animation engine on top of the PROS LED API.
Every strip has a **base layer** and an **overlay layer**.
Animations run continuously in a background task, you just call a method and it plays.

```
LedGroup
 └─ LedStrand
      ├─ Base layer      ← flow / rainbow / pulse / bitscroll / twinkle
      ├─ Overlay layer   ← second independent animation
      ├─ Spread mask     ← wipes overlay over base from center outward
      ├─ Splice mask     ← splits strip into alternating sections
      └─ Profile         ← priority-based mode stack for match events
```

---

## Feature Overview

| Feature | Description |
|---|---|
| **Animations** | Static, flow gradient, rainbow, pulse, flash, twinkle, bitscroll |
| **Bounce variants** | Pulse bounce, bitscroll bounce |
| **Overlay system** | Two independent layers composited by a spread mask |
| **Center spread** | Wipe from center outward, or edges inward |
| **Splice mask** | Alternating section override with optional auto-toggle |
| **Profiles** | Priority mode stack, activateMode / activateModeTimed |
| **Sequencer** | Multi-phase timed driver for endgame sequences |
| **Groups** | One call fans out to every strand in the group |
| **Brightness** | 0–100% applied non-destructively at flush time |

---

## Minimal Example

```cpp
#include "hitlib/hitapi.hpp"

hitlib::LedStrand strand(6, 63);
hitlib::LedGroup  group;

void initialize() {
    group.add(&strand);
    group.init();
    group.start();
}

void opcontrol() {
    group.rainbow(1);
}
```