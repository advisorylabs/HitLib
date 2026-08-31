# Profiles & Modes {#profiles_page}

The **profile system** lets you define named animation modes with a priority level,
then activate and deactivate them at runtime. The highest-priority currently-active
mode wins and drives the strand.

---

## Built-in Profiles

Three ready-made profiles are available in `hitlib/profiles/classic.hpp`.

### Mode Index Table

| Index | Name | Classic | Modern | Showy |
|---|---|---|---|---|
| 0 | Showoff | rainbow | rainbow | - |
| 1 | Idle | magenta flow | pink pulse | purple flow |
| 2 | Alliance Red | red pulse | red pulse + orange bg | white pulse / red bg |
| 3 | Alliance Blue | blue pulse | blue pulse + cyan bg | white pulse / blue bg |
| 4 | Scoring | green pulse | green flash | teal pulse |
| 5 | Matchloading | yellow pulse | yellow pulse | - |
| 6 | Endgame | warn -> white -> cycle | solid green -> pulse | yellow -> rainbow |

```cpp
#include "hitlib/profiles/classic.hpp"

strand.attachProfile(&hitlib::profiles::classic);
strand.activateMode(1);                   // Idle - persistent
strand.activateModeTimed(4, 1500);        // Scoring - expires after 1.5 s
strand.deactivateMode(1);                 // remove Idle from stack
strand.detachProfile();                   // detach and turn off
```

---

## Custom Profiles

### 1. Write setup functions

```cpp
static void myIdle  (hitlib::LedStrand& s) { s.rainbow(1); }
static void myAlert (hitlib::LedStrand& s) { s.flash(0xFF0000, 100, 100); }
```

### 2. Declare modes

```cpp
static const hitlib::ProfileMode myModes[] = {
    {"Idle",  10, myIdle,  nullptr},
    {"Alert", 80, myAlert, nullptr},
};
static const hitlib::Profile myProfile = {"MyRobot", myModes, 2};
```

### 3. Attach and activate

```cpp
strand.attachProfile(&myProfile);
strand.activateMode(0);   // Idle
```

---

## Exported from Pattern Studio

[Pattern Studio](#install_page) writes the three steps above for you, and can
put the result straight into your project.

Show it your PROS project once - **Export > Choose PROS Project...**, or drag
the project folder onto the window - and **Deploy** writes
`include/hitlib_studio.hpp` every time you click it. Re-deploying overwrites
that one file, so changing a port or adding a mode is: click Deploy, rebuild.

### What you write once

```cpp
#include "hitlib_studio.hpp"

namespace myRobot = hitlib::profiles::myRobot;

void initialize() {
    hitlib::studio::begin();
}

void opcontrol() {
    myRobot::strand.activateModeTimed(myRobot::mode::endgame, 30000);
}
```

`begin()` registers every strand in the design, starts its refresh task at the
design's interval, attaches each profile and activates each strand's first
mode. A second call does nothing, so a routine that re-runs its own init is
safe.

The exported file opens with this same snippet as a comment, filled in with your
design's real identifiers and mode names.

### What the file contains

Each strand gets its own namespace under `hitlib::profiles`:

```cpp
namespace hitlib::profiles {
namespace myRobot {

// --- Hardware: the strand this design was previewed against ---
constexpr uint8_t  adiPort    = 6;
constexpr uint8_t  length     = 63;
constexpr uint32_t refreshMs  = 25;
constexpr uint8_t  brightness = 80;

// --- Mode indices: pass these to activateMode() / activateModeTimed() ---
namespace mode {
constexpr uint8_t idle    = 0;  // "Idle", priority 10
constexpr uint8_t endgame = 1;  // "Endgame", priority 100
}  // namespace mode

namespace detail { /* one setup function per mode and per sequenced phase */ }

inline const ProfileMode modeTable[] = { ... };
inline const Profile profile = {"My Robot", modeTable, 2};

inline void apply(LedStrand& s);   // setBrightness + attachProfile
inline void apply(LedGroup& g);

inline LedStrand strand{adiPort, length, refreshMs};   // the strand itself

}  // namespace myRobot
}  // namespace hitlib::profiles

namespace hitlib::studio {
inline LedGroup  groups[1];         // one per refresh interval in the design
inline LedGroup& group = groups[0];
inline void begin();                // add, init, start, apply, activateMode
inline void begin(LedGroup& existing);
}
```

The `constexpr` hardware values are why the strand on the robot matches the one
in the preview: the port, length and refresh interval come from the design
rather than being retyped. `strand` is an ordinary hitlib::LedStrand built from
those constants - every API call still works on it directly. The `mode::`
constants replace counting rows in `modeTable` to work out what index
`activateMode()` wants.

### Fitting it to your own code

The strand and the group are optional. Two ways to opt out, each one line:

```cpp
hitlib::studio::begin(yourGroup);
```

puts the design's strands into a group you already own and attaches their
profiles, leaving `init()`, `start()` and the refresh interval to you. Or:

```cpp
#define HITLIB_STUDIO_NO_AUTOWIRE
#include "hitlib_studio.hpp"
```

and the file defines no strands and no group - only the profile and the
constants:

```cpp
namespace myRobot = hitlib::profiles::myRobot;

hitlib::LedStrand myRobotStrand(myRobot::adiPort, myRobot::length, myRobot::refreshMs);
hitlib::LedGroup  group;

void initialize() {
    group.add(&myRobotStrand);
    group.init(myRobot::refreshMs);
    group.start();
    myRobot::apply(group);                      // brightness + attachProfile
    group.activateMode(myRobot::mode::idle);
}
```

One deployed header per project: it defines `hitlib::studio`, so two in one
build would define `begin()` twice. Keep every strand in one document, which is
what Deploy exports.

### Multiple strands

Deploy writes the whole document, so multi-strand designs need nothing extra.
Two *separately* exported headers can collide: if both have an `Idle` mode,
each defines its own setup function for it and they cannot share a `.cpp`.

```cpp
namespace left  = hitlib::profiles::left;
namespace right = hitlib::profiles::right;

void initialize() {
    hitlib::studio::begin();       // both strands, both profiles, both started
}

void opcontrol() {
    left::strand.activateMode(left::mode::idle);
    right::strand.activateMode(right::mode::idle);
}
```

Strands designed at different refresh intervals get a group each, since a group
ticks everything it owns at one rate. `begin()` handles that; it is still one
call.

Strand names become namespace names, so each strand needs a distinct one.
Pattern Studio blocks the export until they are unique.

### Exporting to a file instead

**Export > Export Current Strand as C++...** and **Export All Strands as
C++...** still save through a normal file dialog, for a project Pattern Studio
cannot see or a header you want to check in by hand. The generated code is
identical.

---

## Mode Stack Rules

- Modes are stored in a **priority stack**, the highest-priority active mode wins.
- Multiple modes can be active simultaneously, the winner updates every tick.
- Timed modes auto-expire, persistent modes stay until `deactivateMode()` is called.
- Calling `activateModeTimed()` on an already-timed mode extends its deadline rather
  than creating a duplicate entry.

---

## Sequencer

hitlib::Sequencer drives multi-phase endgame / event sequences inside `onActivate`
and `onTick` profile callbacks.

```cpp
static void phase1(hitlib::LedStrand& s) { s.flash(0xFFFF00, 150, 150); }
static void phase2(hitlib::LedStrand& s) { s.rainbow(2); }

static const hitlib::Sequencer::Phase phases[] = {
    {2000, phase1},   // 2 s yellow flash
    {8000, phase2},   // 8 s rainbow
};
static hitlib::Sequencer seq(phases, 2);

static void myEgActivate(hitlib::LedStrand& s) { seq.start(s); }
static void myEgTick    (hitlib::LedStrand& s) { seq.update(s); }
```

Phases loop indefinitely until the mode is deactivated.
