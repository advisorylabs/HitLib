#pragma once
#include <cstdint>

namespace hitlib {

class LedStrand;

// Multi-phase timed animation driver.
// Phases receive the strand they're running on — no global state needed.
// Typical use: seq.start(s) in onActivate, seq.update(s) in onTick.
class Sequencer {
public:
    struct Phase {
        uint32_t durationMs;
        void (*startFn)(LedStrand& strand);
    };

    constexpr Sequencer(const Phase* phases, uint8_t count)
        : phases(phases), phaseCount(count) {}

    void start(LedStrand& strand);
    void stop();
    void update(LedStrand& strand);
    bool isRunning() const { return running; }

private:
    const Phase* phases;
    uint8_t      phaseCount;
    uint8_t      currentPhase = 0;
    uint32_t     phaseStartMs = 0;
    bool         running      = false;
};

} // namespace hitlib