#pragma once
#include <cstdint>

/**
 * @file led_sequencer.hpp
 * @brief Multi-phase timed animation sequencer.
 */

namespace hitlib {

class LedStrand; ///< Forward declaration.

// ============================================================================

/**
 * @brief Drives a looping sequence of timed animation phases on a LedStrand.
 *
 * A Sequencer holds a pointer to a **statically-allocated** array of Phase
 * descriptors.  It carries its own runtime state (current phase, timestamp,
 * running flag) so that multiple Sequencer instances can run independently on
 * different strands without interfering with each other.
 *
 * ### Typical usage inside a Profile
 * @code{.cpp}
 * static void phase1(hitlib::LedStrand& s) { s.flash(0xFFFF00, 3); }
 * static void phase2(hitlib::LedStrand& s) { s.rainbow(2); }
 *
 * static const hitlib::Sequencer::Phase egPhases[] = {
 *     {2000, phase1},   ///< 2 s yellow flash
 *     {8000, phase2},   ///< 8 s rainbow
 * };
 * static hitlib::Sequencer endgameSeq(egPhases, 2);
 *
 * static void egActivate(hitlib::LedStrand& s) { endgameSeq.start(s); }
 * static void egTick    (hitlib::LedStrand& s) { endgameSeq.update(s); }
 *
 * static const hitlib::ProfileMode modes[] = {
 *     {"Endgame", 100, egActivate, egTick},
 * };
 * @endcode
 *
 * @note Sequencer is **not** thread-safe by itself.  It is driven from
 *       LedStrand::tick() which already holds the strand mutex when calling
 *       @c onTick, so no additional locking is required in normal use.
 */
class Sequencer {
public:
    // ------------------------------------------------------------------------

    /**
     * @brief One phase in the animation sequence.
     */
    struct Phase {
        uint32_t durationMs;            ///< How long this phase runs (milliseconds).
        void (*startFn)(LedStrand&);    ///< Called once when the phase begins.
    };

    // ------------------------------------------------------------------------

    /**
     * @brief Construct a Sequencer from a static phase array.
     *
     * @param phases    Pointer to a statically-allocated Phase array.
     *                  Must remain valid for the lifetime of the Sequencer.
     * @param count     Number of elements in @p phases.
     */
    constexpr Sequencer(const Phase* phases, uint8_t count)
        : phases(phases), phaseCount(count) {}

    // ------------------------------------------------------------------------

    /**
     * @brief Start the sequence from phase 0.
     *
     * Calls `phases[0].startFn(strand)` immediately.
     * Typically called from a ProfileMode @c onActivate callback.
     *
     * @param strand  The strand to animate.
     */
    void start(LedStrand& strand);

    /**
     * @brief Stop the sequence without advancing.
     *
     * The strand animation is **not** changed, it simply stops being updated.
     */
    void stop();

    /**
     * @brief Advance the sequence, call every tick from an @c onTick callback.
     *
     * Checks whether the current phase has expired.  If so, advances to the
     * next phase (wrapping to 0) and calls its @c startFn.  Does nothing if
     * the sequencer is not running.
     *
     * @param strand  The strand to animate.
     */
    void update(LedStrand& strand);

    /**
     * @brief Returns @c true if the sequence is currently running.
     */
    bool isRunning() const { return running; }

private:
    const Phase* phases;            ///< Pointer to the phase array (not owned).
    uint8_t      phaseCount;        ///< Number of phases.
    uint8_t      currentPhase = 0;  ///< Index of the currently-active phase.
    uint32_t     phaseStartMs = 0;  ///< Timestamp when the current phase began.
    bool         running      = false; ///< Whether the sequencer is active.
};

} // namespace hitlib