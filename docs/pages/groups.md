# Groups {#groups_page}

A **LedGroup** owns a set of LedStrand pointers and fans every animation command
out to all of them simultaneously.  Each group runs its own PROS task, multiple
independent groups are fully supported with no shared state between them.

---

## Setup

```cpp
#include "hitlib/hitapi.hpp"

hitlib::LedStrand strand1(6, 63);   // ADI port 6
hitlib::LedStrand strand2(7, 63);   // ADI port 7
hitlib::LedStrand strand3(8, 63);   // ADI port 8

hitlib::LedGroup groupLeft;
hitlib::LedGroup groupRight;

void initialize() {
    // Build groups before calling init()
    groupLeft.add(&strand1);
    groupLeft.add(&strand2);

    groupRight.add(&strand3);

    // init() initialises hardware on every strand in the group
    groupLeft.init();
    groupRight.init();

    // start() launches the refresh task
    groupLeft.start();
    groupRight.start();
}
```

---

## Fan-out behaviour

Every animation method on LedGroup calls the same method on each strand in order.
All strands receive the command before any of them tick, so they stay in sync.

```cpp
// Both strands in groupLeft get rainbow simultaneously
groupLeft.rainbow(1);

// groupRight gets something different
groupRight.pulse(0xFF0000, 5, 1);
```

---

## Targeting individual strands

Use the subscript operator to reach a specific strand when you need different
animations within the same group:

```cpp
groupLeft[0]->setColor(0xFF0000);   // strand1 only
groupLeft[1]->setColor(0x0000FF);   // strand2 only

// Fan-out still works normally for shared commands
groupLeft.setBrightness(75);        // both strands
```

---

## refresh rate

`init()` accepts an optional refresh interval in milliseconds (default 20 ms = 50 Hz).

```cpp
groupLeft.init(20);    // 50 Hz - default, good for most animations
groupLeft.init(10);    // 100 Hz - smoother twinkle / fast pulse bounce
groupLeft.init(33);    // ~30 Hz - lighter CPU load
```

Pass `0` to fall back to each strand's own configured interval.

---

## Multiple groups example

```cpp
hitlib::LedGroup drivetrainLeds;
hitlib::LedGroup intakeLeds;

void initialize() {
    drivetrainLeds.add(&strand1);
    drivetrainLeds.add(&strand2);
    intakeLeds.add(&strand3);

    drivetrainLeds.init();
    intakeLeds.init();
    drivetrainLeds.start();
    intakeLeds.start();
}

void opcontrol() {
    drivetrainLeds.rainbow(1);
    intakeLeds.twinkle({0x00FFFF, 0xFFFFFF}, 40);
}
```
