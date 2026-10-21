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
 * descriptors, and keeps each strand's place in it separately: one Sequencer
 * can run on several strands at once, each at its own phase.  That is what a
 * profile attached to a whole LedGroup needs, since every strand in the group
 * calls the same @c onActivate and @c onTick, and so the same Sequencer.  Up
 * to MAX_STRANDS strands can run at a time; see start() for what happens past
 * that.
 *
 * ### Typical usage inside a Profile
 * @code{.cpp}
 * static void phase1(hitlib::LedStrand& s) { s.flash(0xFFFF00, 150, 150); }
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
 * @note start(), update() and stop() may be called from different tasks, as
 *       they are when two groups share a profile.  The phases' @c startFn
 *       runs with no lock held, so it is free to call any LedStrand method.
 */
class Sequencer {
public:
    /// Strands one Sequencer can run on at the same time.
    static constexpr uint8_t MAX_STRANDS = 8;

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
     * @brief Start the sequence on @p strand from phase 0.
     *
     * Calls `phases[0].startFn(strand)` immediately.
     * Typically called from a ProfileMode @c onActivate callback.  Restarts
     * @p strand's run if it already has one, and leaves every other strand's
     * alone.
     *
     * With MAX_STRANDS strands already running, the one that has gone longest
     * without an update() - normally one whose mode has since been replaced -
     * gives its place up to @p strand, and stops.
     *
     * @param strand  The strand to animate.
     */
    void start(LedStrand& strand);

    /**
     * @brief Stop the sequence on every strand without advancing.
     *
     * The strands' animations are **not** changed, they simply stop being
     * updated.
     */
    void stop();

    /// @brief Stop the sequence on @p strand only.  See stop().
    void stop(const LedStrand& strand);

    /**
     * @brief Advance the sequence, call every tick from an @c onTick callback.
     *
     * Checks whether @p strand's current phase has expired.  If so, advances
     * it to the next phase (wrapping to 0) and calls that phase's @c startFn.
     * Does nothing if the sequence is not running on @p strand.
     *
     * @param strand  The strand to animate.
     */
    void update(LedStrand& strand);

    /**
     * @brief Returns @c true if the sequence is running on any strand.
     */
    bool isRunning() const;

    /// @brief Returns @c true if the sequence is running on @p strand.
    bool isRunning(const LedStrand& strand) const;

private:
    /// Where one strand is in the sequence.
    struct Run {
        const LedStrand* strand;        ///< nullptr for a free slot.
        uint32_t         phaseStartMs;  ///< Timestamp when its phase began.
        uint32_t         lastTouch;     ///< @c touches at its last start() or update().
        uint8_t          phase;         ///< Index of its current phase.
    };

    const Phase* phases;                 ///< Pointer to the phase array (not owned).
    uint8_t      phaseCount;             ///< Number of phases.
    Run          runs[MAX_STRANDS] = {}; ///< One per strand it is running on.
    uint32_t     touches = 0;            ///< Bumped by every start() and update().

    int8_t indexOf(const LedStrand& strand) const;
};

} // namespace hitlib