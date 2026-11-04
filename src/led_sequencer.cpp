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
    run.lap          = 0;
    run.phaseStartMs = now;
    run.lastTouch    = ++touches;
    s_runsMutex.give();

    // No wipe: there is no phase before the first one for it to come in over.
    if (phaseCount > 0) begin(phases[0], strand, false);
}

// A phase's transition first, so it snapshots what the strand is showing
// before startFn replaces it - when @p wipe says there is a different phase to
// wipe away from. Called with no lock held: both reach straight back into the
// strand.
void Sequencer::begin(const Phase& phase, LedStrand& strand, bool wipe) {
    // A cut too, not just a wipe: a phase that cuts in cuts short whatever
    // wipe the phase before it was still in, rather than letting it carry on
    // over this one from a frame two phases old.
    if (wipe) strand.transition(phase.transition);
    if (phase.startFn) phase.startFn(strand);
}

// Moves @p run on to the next phase still due to play, counting a lap each
// time it wraps. Trying each phase once, the current one last, is enough: if
// none of them is due, nothing will be on a later lap either. False, with
// the run left where it was, when every phase has run out of plays.
bool Sequencer::advance(Run& run) const {
    uint8_t idx = run.phase;
    uint8_t lap = run.lap;
    for (uint8_t tries = 0; tries < phaseCount; ++tries) {
        if (++idx >= phaseCount) {
            idx = 0;
            if (lap < 255) ++lap;
        }
        if (phases[idx].plays == 0 || lap < phases[idx].plays) {
            run.phase = idx;
            run.lap   = lap;
            return true;
        }
    }
    return false;
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
    bool     wipe    = false;

    s_runsMutex.take();
    int8_t idx = indexOf(strand);
    if (idx >= 0) {
        Run& run = runs[idx];
        run.lastTouch = ++touches;
        if (now - run.phaseStartMs >= phases[run.phase].durationMs) {
            // A run with nothing left to play holds its last phase, and
            // checks again a whole phase later rather than every tick.
            uint8_t was = run.phase;
            if (advance(run)) {
                started = run.phase;
                // A phase going round into itself - the only one in the loop,
                // or the only one left once the intros have played - restarts
                // without a wipe: there is nothing different to wipe away.
                wipe = run.phase != was;
            }
            run.phaseStartMs = now;
        }
    }
    s_runsMutex.give();

    if (started >= 0) begin(phases[started], strand, wipe);
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
