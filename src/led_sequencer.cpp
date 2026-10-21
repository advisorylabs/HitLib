#include "hitlib/led_sequencer.hpp"
#include "hitlib/led_strand.hpp"
#include "hitlib/platform.hpp"

namespace hitlib {

// Before C++17 a static constexpr member used by reference needs a definition
// of its own; see the same block in led_strand.cpp.
#if __cplusplus < 201703L
constexpr uint8_t Sequencer::MAX_STRANDS;
#endif

// Guards every Sequencer's run table. A built-in profile's Sequencer is shared
// by every strand it is attached to, and strands in different groups are
// ticked by different tasks. Never held while a phase's startFn runs: that is
// user code, and it calls straight back into the strand.
static platform::Mutex s_runsMutex;

int8_t Sequencer::indexOf(const LedStrand& strand) const {
    for (uint8_t i = 0; i < MAX_STRANDS; ++i) {
        if (runs[i].strand == &strand) return (int8_t)i;
    }
    return -1;
}

void Sequencer::start(LedStrand& strand) {
    uint32_t now = platform::millis();

    s_runsMutex.take();
    int8_t idx = indexOf(strand);
    if (idx < 0) {
        // A free slot, or failing that the strand that has gone longest
        // without an update(): one whose mode was replaced, so nothing calls
        // update() for it any more.
        idx = 0;
        for (uint8_t i = 0; i < MAX_STRANDS; ++i) {
            if (!runs[i].strand) { idx = (int8_t)i; break; }
            if ((int32_t)(runs[i].lastTouch - runs[idx].lastTouch) < 0) idx = (int8_t)i;
        }
    }
    Run& run         = runs[idx];
    run.strand       = &strand;
    run.phase        = 0;
    run.phaseStartMs = now;
    run.lastTouch    = ++touches;
    s_runsMutex.give();

    if (phaseCount > 0 && phases[0].startFn) phases[0].startFn(strand);
}

void Sequencer::stop() {
    s_runsMutex.take();
    for (Run& run : runs) run.strand = nullptr;
    s_runsMutex.give();
}

void Sequencer::stop(const LedStrand& strand) {
    s_runsMutex.take();
    int8_t idx = indexOf(strand);
    if (idx >= 0) runs[idx].strand = nullptr;
    s_runsMutex.give();
}

void Sequencer::update(LedStrand& strand) {
    if (phaseCount == 0) return;

    uint32_t now = platform::millis();
    int16_t  started = -1;   // the phase to call startFn for, once unlocked

    s_runsMutex.take();
    int8_t idx = indexOf(strand);
    if (idx >= 0) {
        Run& run = runs[idx];
        run.lastTouch = ++touches;
        if (now - run.phaseStartMs >= phases[run.phase].durationMs) {
            run.phase        = (uint8_t)((run.phase + 1) % phaseCount);
            run.phaseStartMs = now;
            started          = run.phase;
        }
    }
    s_runsMutex.give();

    if (started >= 0 && phases[started].startFn) phases[started].startFn(strand);
}

bool Sequencer::isRunning() const {
    s_runsMutex.take();
    bool any = false;
    for (const Run& run : runs) any = any || run.strand != nullptr;
    s_runsMutex.give();
    return any;
}

bool Sequencer::isRunning(const LedStrand& strand) const {
    s_runsMutex.take();
    bool running = indexOf(strand) >= 0;
    s_runsMutex.give();
    return running;
}

} // namespace hitlib
