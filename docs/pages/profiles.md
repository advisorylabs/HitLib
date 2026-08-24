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

[Pattern Studio](#install_page) writes the three steps above for you. Design a
strand, pick **Export > Export Current Strand as C++...**, and save the header
into your project's `include/` directory.

The generated file puts everything for one strand in its own namespace under
`hitlib::profiles`:

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

}  // namespace myRobot
}  // namespace hitlib::profiles
```

Using it: the exported file opens with this same snippet as a comment, filled
in with your strand's real ports and mode names:

```cpp
#include "hitlib/hitapi.hpp"
#include "my_robot.hpp"

namespace myRobot = hitlib::profiles::myRobot;

hitlib::LedStrand myRobotStrand(myRobot::adiPort, myRobot::length, myRobot::refreshMs);
hitlib::LedGroup  group;

void initialize() {
    group.add(&myRobotStrand);
    group.init();
    group.start();
    myRobot::apply(group);                      // brightness + attachProfile
    group.activateMode(myRobot::mode::idle);
}

void opcontrol() {
    group.activateModeTimed(myRobot::mode::endgame, 30000);
}
```

The `constexpr` hardware values are why the strand on the robot matches the one
in the preview: the port, length and refresh interval come from the design
rather than being retyped. The `mode::` constants replace counting rows in
`modeTable` to work out what index `activateMode()` wants.

### Multiple strands

Two separately exported headers can collide. If both designs have an `Idle`
mode, each file defines its own setup function for it, and including both in one
`.cpp` won't compile. Use **Export > Export All Strands as C++...** instead: it
writes every strand in the document to one header, each in its own namespace.

```cpp
namespace left  = hitlib::profiles::left;
namespace right = hitlib::profiles::right;

hitlib::LedStrand leftStrand (left::adiPort,  left::length,  left::refreshMs);
hitlib::LedStrand rightStrand(right::adiPort, right::length, right::refreshMs);

void initialize() {
    group.add(&leftStrand);
    group.add(&rightStrand);
    group.init();
    group.start();
    left::apply(leftStrand);      // each strand carries its own profile, so
    right::apply(rightStrand);    // attach per strand rather than per group
    leftStrand.activateMode(left::mode::idle);
    rightStrand.activateMode(right::mode::idle);
}
```

Strand names become namespace names, so each strand needs a distinct one.
Pattern Studio blocks the export until they are unique.

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
