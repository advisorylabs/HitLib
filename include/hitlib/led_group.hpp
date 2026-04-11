#pragma once
#include "led_strand.hpp"
#include "led_profile.hpp"
#include <vector>

/**
 * @file led_group.hpp
 * @brief Fan-out controller for multiple LedStrand instances.
 */

namespace hitlib {

// ============================================================================

/**
 * @brief Owns a set of LedStrand pointers and fans all animation commands out
 *        to each strand simultaneously.
 *
 * LedGroup starts a single PROS task that calls LedStrand::tick() on every
 * strand it owns at the configured refresh interval.  Multiple independent
 * groups are fully supported, each runs its own task with no shared state.
 *
 * ### Setup
 * @code{.cpp}
 * hitlib::LedStrand strand1(6, 63);
 * hitlib::LedStrand strand2(7, 63);
 * hitlib::LedGroup  group;
 *
 * void initialize() {
 *     group.add(&strand1);
 *     group.add(&strand2);
 *     group.init();    // initializes hardware on all strands
 *     group.start();   // starts the refresh task
 * }
 * @endcode
 *
 * @see LedStrand
 */
class LedGroup {
public:

    // ========================================================================
    /// @name Setup
    /// @{

    /**
     * @brief Register a strand with this group.
     *
     * Must be called **before** init().
     *
     * @param strand  Pointer to a LedStrand.  The strand is not owned by the
     *                group, it must outlive the group.
     */
    void add(LedStrand* strand);

    /**
     * @brief Initialize hardware on all registered strands.
     *
     * Calls LedStrand::init() on each strand.  Safe to call from PROS
     * `initialize()`.
     *
     * @param refreshMs  Refresh interval passed to the group task in milliseconds
     *                   (default 20 ms = 50 Hz).  Pass 0 to use each strand's
     *                   own configured interval.
     */
    void init(uint32_t refreshMs = 20);

    /**
     * @brief Start the group refresh task.
     *
     * Call after init().  The task calls tick() on every strand each cycle.
     */
    void start();

    /// @}

    // ========================================================================
    /// @name Base Animations
    ///
    /// Fan-out wrappers, each call is forwarded to every strand in the group.
    /// @{

    /** @copydoc LedStrand::off */
    void off();

    /** @copydoc LedStrand::setColor */
    void setColor(uint32_t color);

    /** @copydoc LedStrand::pulse */
    void pulse(uint32_t color, uint8_t runLength, uint8_t speed,
               uint32_t bgColor = 0x000000, bool invert = false, bool bounce = false);

    /** @copydoc LedStrand::flash */
    void flash(uint32_t color, uint8_t speed, uint32_t bgColor = 0x000000);

    /** @copydoc LedStrand::flow */
    void flow(uint32_t color1, uint32_t color2, uint8_t speed, bool invert = false);

    /** @copydoc LedStrand::rainbow */
    void rainbow(uint8_t speed);

    /** @copydoc LedStrand::twinkle */
    void twinkle(const std::vector<uint32_t>& colors, uint8_t densityPct = 30,
                 uint8_t fadeStep = 16, uint32_t bgColor = 0x000000);

    /** @copydoc LedStrand::bitscroll */
    void bitscroll(const std::vector<LedStrand::BitScrollSegment>& segments, uint8_t speed,
                   bool invert = false, uint32_t bgColor = 0x000000, bool bounce = false,
                   uint8_t spacing = 0, bool repeating = true);

    /** @copydoc LedStrand::spliceMask */
    void spliceMask(uint8_t sections, bool invert = false, bool alternating = false,
                    uint32_t altPeriodMs = 100, uint32_t bgColor = 0x000000);

    /** @copydoc LedStrand::clearSpliceMask */
    void clearSpliceMask();

    /** @copydoc LedStrand::setBrightness */
    void setBrightness(uint8_t pct);

    /// @}

    // ========================================================================
    /// @name Overlay Animations
    /// @{

    /** @copydoc LedStrand::overlaySetColor */
    void overlaySetColor(uint32_t color);

    /** @copydoc LedStrand::overlayPulse */
    void overlayPulse(uint32_t color, uint8_t runLength, uint8_t speed,
                      uint32_t bgColor = 0x000000);

    /** @copydoc LedStrand::overlayFlash */
    void overlayFlash(uint32_t color, uint8_t speed, uint32_t bgColor = 0x000000);

    /** @copydoc LedStrand::overlayFlow */
    void overlayFlow(uint32_t color1, uint32_t color2, uint8_t speed);

    /** @copydoc LedStrand::overlayRainbow */
    void overlayRainbow(uint8_t speed);

    /// @}

    // ========================================================================
    /// @name Center Spread
    /// @{

    /** @copydoc LedStrand::centerSpread */
    void centerSpread(uint8_t tickInterval = 8, bool invert = false);

    /** @copydoc LedStrand::centerSpreadStacked */
    void centerSpreadStacked(const std::vector<LedStrand::AnimSetupFn>& layers,
                             uint8_t tickInterval = 8, bool invert = false);

    /** @copydoc LedStrand::centerSpreadBounce */
    void centerSpreadBounce(uint8_t tickInterval = 8, bool invert = false);

    /** @copydoc LedStrand::centerSpreadBounceStacked */
    void centerSpreadBounceStacked(const std::vector<LedStrand::AnimSetupFn>& layers,
                                   uint8_t tickInterval = 8, bool invert = false);

    /// @}

    // ========================================================================
    /// @name Profile System
    /// @{

    /** @copydoc LedStrand::attachProfile */
    void attachProfile(const Profile* profile);

    /** @copydoc LedStrand::detachProfile */
    void detachProfile();

    /** @copydoc LedStrand::activateMode */
    void activateMode(uint8_t modeIdx);

    /** @copydoc LedStrand::activateModeTimed */
    void activateModeTimed(uint8_t modeIdx, uint32_t durationMs);

    /** @copydoc LedStrand::deactivateMode */
    void deactivateMode(uint8_t modeIdx);

    /// @}

    // ========================================================================
    /// @name Direct Access
    /// @{

    /**
     * @brief Access an individual strand by index.
     *
     * Useful when you need to apply different animations to specific strands
     * while still using the group for shared commands.
     *
     * @param i  Zero-based index in the order strands were added.
     */
    LedStrand* operator[](size_t i) { return strands[i]; }

    /**
     * @brief Return the number of strands in this group.
     */
    size_t size() const { return strands.size(); }

    /// @}

private:
    std::vector<LedStrand*> strands;
    uint32_t refreshMs = 20;
    void groupTask();
};

} // namespace hitlib