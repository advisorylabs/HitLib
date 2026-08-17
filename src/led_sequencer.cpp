#include "hitlib/led_sequencer.hpp"
#include "hitlib/led_strand.hpp"
#include "pros/rtos.hpp"

namespace hitlib {

void Sequencer::start(LedStrand& strand) {
    running      = true;
    currentPhase = 0;
    phaseStartMs = pros::millis();
    if (phaseCount > 0 && phases[0].startFn) phases[0].startFn(strand);
}

void Sequencer::stop() {
    running = false;
}

void Sequencer::update(LedStrand& strand) {
    if (!running || phaseCount == 0) return;

    uint32_t now = pros::millis();
    if (now - phaseStartMs >= phases[currentPhase].durationMs) {
        currentPhase = (currentPhase + 1) % phaseCount;
        phaseStartMs = now;
        if (phases[currentPhase].startFn) phases[currentPhase].startFn(strand);
    }
}

} // namespace hitlib
