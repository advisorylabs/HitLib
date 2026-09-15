# HitLib

**LED animation and control library for VEX V5, for [PROS](https://pros.cs.purdue.edu/) and VEXcode.**

Drive WS2812B strips off the V5's ADI ports with animations: flow,
rainbow, pulse, flash, twinkle, and bitscroll composed into named,
prioritized modes you switch at runtime. Strips can also fill like a meter -
tracking a motor's heat, an arm's rotation or the battery on their own, or
filling in time with a song. One strip can be split into several independent
gauges, so a strand under the drivebase can show all six motors' temperatures
at once, each segment colored by its own. Comes with
**Pattern Studio**, a desktop pattern selection tool that previews patterns live
and exports them as ready-to-include C++.

📖 **[Documentation](https://advisorylabs.github.io/HitLib/)** ·
📦 **[Releases](https://github.com/advisorylabs/HitLib/releases)**

---

## Install

```bash
pros c fetch https://github.com/advisorylabs/hitlib/releases/download/1.4.0/hitlib@1.4.0.zip
pros c apply hitlib
```

Or paste that URL into **Install Template** in the PROS VS Code extension.

**VEXcode** (V5, Pro V5, or the VS Code extension): download
`hitlib-vexcode@1.4.0.zip` from the Releases page and copy its `include/hitlib/`
and `src/hitlib/` folders into your project. Its headers end in `.h`, since
VEXcode Pro V5 only shows `.h` files, so VEXcode code includes
`hitlib/hitapi.h`. See the
[installation guide](https://advisorylabs.github.io/HitLib/install_page.html).

## Hello, strip

```cpp
#include "main.h"
#include "hitlib/hitapi.hpp"

hitlib::LedStrand strand(6, 63);   // ADI port 6, 63 LEDs
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

A background task refreshes every strand on its own interval, so animations keep
running without anything in your control loop. Every call is thread-safe and
takes effect on the next tick.

## Profiles and modes

Rather than scattering animation calls through your match code, declare the
looks up front and switch between them by name. The highest-priority active mode
wins; timed modes expire on their own.

```cpp
#include "hitlib/profiles/classic.hpp"

group.attachProfile(&hitlib::profiles::classic);
group.activateMode(1);                // Idle
group.activateModeTimed(4, 1500);     // Scoring, for 1.5 s, then back to Idle
```

Three profiles ship in the box (Classic, Modern, Showy), and a `Sequencer`
handles multi-phase looks like a timed endgame sequence.

## Pattern Studio

A desktop app for designing patterns against a live preview instead of a robot,
then exporting them as a HitLib profile header.

Drop a song into its Song bar - MP3, M4A, FLAC, OGG, WAV or MIDI - and it
measures the track's loudness in three frequency bands and bakes each into a
table the strip fills to. Play it back and you hear the song while watching
every strand react to it; scrub anywhere; point one strip at the kick and
another at the hi-hats. The tables ship inside the exported header, which is
how a strip syncs to music on a brain that can't hear any.

```bash
cd tools/pattern_studio
pip install -e .
pattern-studio
```

Prebuilt Windows binaries are on the
[Releases page](https://github.com/advisorylabs/HitLib/releases)
(`HitLibPatternStudio-*-windows.zip`).

Show it your PROS or VEXcode project once - drag the project folder onto the
window, or **Export > Choose Robot Project...** - and **Deploy** writes the header
straight into the project's `include/`, in the form that project's compiler
wants. Everything the design knows comes with it: ports,
lengths, refresh intervals, brightness, named mode constants, and the strands
themselves. Wiring it up is two lines, written once:

```cpp
#include "hitlib_studio.hpp"

namespace myRobot = hitlib::profiles::myRobot;

void initialize() {
    hitlib::studio::begin();   // registers, starts and activates every strand
}

void opcontrol() {
    myRobot::strand.activateMode(myRobot::mode::scoring);
}
```

Re-deploying overwrites that file, so changing a port or adding a mode is a
click and a rebuild; `main.cpp` does not change again. In a VEXcode project the
header is `hitlib_studio.h` (VEXcode Pro V5 lists only `.h` headers), the same
lines go in `pre_auton()` and `usercontrol()`, and robot code reads identically.

To wire the strands up yourself: `hitlib::studio::begin(yourGroup)` adds them
to a group you own, and `#define HITLIB_STUDIO_NO_AUTOWIRE` before the include
drops the strands and group entirely, leaving the profile and constants.

## Requirements

- PROS 4.x or later, or VEXcode (V5, Pro V5, or the VEX VS Code extension)
- VEX V5 brain
- WS2812B-compatible strip on an ADI port (or an ADI expander), up to 64 LEDs
  per strand

## Building from source

Requires an `arm-none-eabi-gcc` toolchain on `PATH` (PROS installs one).

```bash
make                                          # PROS: bin/hitlib.a
python tools/package_vexcode.py vexcode_pkg   # VEXcode: sources, headers as .h
```

Full packaging steps are in the
[installation guide](https://advisorylabs.github.io/HitLib/install_page.html).

## License

[Mozilla Public License 2.0](LICENSE).
