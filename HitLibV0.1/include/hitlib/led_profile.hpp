#pragma once
#include <cstdint>

namespace hitlib {

class LedStrand; // forward

// Called once when a mode becomes active on a strand.
// Call strand.setColor(), strand.pulse(), etc. inside.
using ModeFn = void (*)(LedStrand& strand);

struct ProfileMode {
    const char* name;
    uint8_t     priority;               // higher = wins over lower
    ModeFn      onActivate;             // called once when this mode wins
    ModeFn      onTick = nullptr;       // called every refresh tick while active (nullable)
};

struct Profile {
    const char*        name;
    const ProfileMode* modes;
    uint8_t            modeCount;
};

} // namespace hitlib