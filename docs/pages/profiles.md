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
static void myAlert (hitlib::LedStrand& s) { s.flash(0xFF0000, 2); }
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

## Mode Stack Rules

- Modes are stored in a **priority stack**; the highest-priority active mode wins.
- Multiple modes can be active simultaneously, the winner updates every tick.
- Timed modes auto-expire; persistent modes stay until `deactivateMode()` is called.
- Calling `activateModeTimed()` on an already-timed mode extends its deadline rather
  than creating a duplicate entry.

---

## Sequencer

hitlib::Sequencer drives multi-phase endgame / event sequences inside `onActivate`
and `onTick` profile callbacks.

```cpp
static void phase1(hitlib::LedStrand& s) { s.flash(0xFFFF00, 3); }
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
