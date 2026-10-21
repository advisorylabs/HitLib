# Groups {#groups_page}

A **LedGroup** owns a set of LedStrand pointers and fans every animation command
out to all of them simultaneously. Each group runs its own PROS task, multiple
independent groups are fully supported. All of them share the V5's ADI link,
one frame at a time, as described under *refresh rate* below.

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

Adding strands before `init()` is the usual order, but not a requirement: a
strand added later is initialized on the way in and picks up the group's
refresh interval, and one added after `start()` is ticked from the next cycle.
Adding a strand the group already has does nothing, and so does a second
`start()`.

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

The group ticks every strand at this one interval, so it is also the interval
each strand counts its durations in - flash times, `Speed::everyMs()` and the
rest - whatever the strand itself was constructed with. Pass `0` to use the
first strand's own interval for the whole group.

Every frame a strand sends holds the V5's ADI link for 12 ms, one strand at a
time across every group; the link falls behind if it is written any faster.
A strand whose frame has not changed since its last write sends nothing (apart
from a resend once a second, which brings back a strip that was unplugged or
browned out), so static colors and idle meters cost nothing. A cycle cannot
take less than 12 ms per strand whose frame did change, though. Four strands
that all animate on every tick cannot cycle faster than about 48 ms, and
anything timed in ticks on them runs slower than the interval says.

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
