#include "hitlib/led_sequencer.hpp"
#include "hitlib/led_strand.hpp"
#include "pros/rtos.hpp"

namespace hitlib {

void Sequencer::start(LedStrand& strand) {
    running      = true;
    currentPhase = 0;
    phaseStartMs = pros::millis();
    phases[0].startFn(strand);
}

void Sequencer::stop() { running = false; }

void Sequencer::update(LedStrand& strand) {
    if (!running) return;
    uint32_t now = pros::millis();
    if (now - phaseStartMs >= phases[currentPhase].durationMs) {
        currentPhase = (currentPhase + 1) % phaseCount;
        phaseStartMs = now;
        phases[currentPhase].startFn(strand);
    }
}

} // namespace hitlib