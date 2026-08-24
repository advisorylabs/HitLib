#pragma once
#include "led_profile.hpp"
#include "pros/adi.hpp"
#include "pros/rtos.hpp"
#include <cstdint>
#include <vector>

/**
 * @file led_strand.hpp
 * @brief Single addressable LED strip driver with layered animation engine.
 */

namespace hitlib {

// ============================================================================

/**
 * @brief Driver for a single WS2812B-compatible LED strip on a VEX ADI port.
 *
 * LedStrand owns one hardware `pros::adi::Led` and drives it from a
 * task-managed refresh loop via LedGroup.  All public animation methods are
 * **thread-safe**, they take effect on the next refresh tick.
 *
 * ### Layer model
 * ```
 * base buffer      <- flow / rainbow / pulse / bitscroll / twinkle
 * overlay buffer   <- second independent animation
 * spreadMask       <- CENTER_SPREAD composites base <-> overlay per-pixel
 * spliceMask       <- final per-pixel override (bgColor or overlay), by
 *                     equal alternating bins or by arbitrary regions
 * ```
 *
 * ### Typical setup
 * @code{.cpp}
 * hitlib::LedStrand strand(6, 63);   // ADI port 6, 63 LEDs
 * hitlib::LedGroup  group;
 *
 * void initialize() {
 *     group.add(&strand);
 *     group.init();
 *     group.start();
 * }
 *
 * void opcontrol() {
 *     strand.rainbow(1);
 * }
 * @endcode
 *
 * @see LedGroup
 */
class LedStrand {
public:

    // ========================================================================
    /// @name Types
    /// @{

    /**
     * @brief One colored segment in a bitscroll pattern.
     */
    struct BitScrollSegment {
        uint32_t color; ///< Segment color (0xRRGGBB).
        uint8_t  width; ///< Width in pixels.
    };

    /**
     * @brief What a custom splice mask region shows, mirrors the overlay*()
     * animation vocabulary (see @ref overlaySetColor "Overlay Animations"),
     * since each region gets a buffer built and animated the same way.
     */
    enum class SpliceRegionAnimKind : uint8_t { OFF, SOLID, PULSE, FLASH, FLOW, RAINBOW };

    /**
     * @brief One independently placed override region for a custom splice mask.
     *
     * Each region gets its own animation buffer, generated over just that
     * region's width, and animates independently of every other region and
     * of the base/overlay buffers. Unlike spliceMask()'s single shared
     * overlay, every region here can show something different at once.
     */
    struct SpliceRegion {
        uint8_t  start;                                        ///< First pixel index covered by this region.
        uint8_t  width;                                         ///< Number of pixels covered.
        SpliceRegionAnimKind kind = SpliceRegionAnimKind::OFF;  ///< What this region shows.
        uint32_t color     = 0xFFFFFF; ///< Foreground color (SOLID/PULSE/FLASH/FLOW start).
        uint32_t color2    = 0x0000FF; ///< FLOW end color.
        uint32_t bgColor   = 0x000000; ///< PULSE/FLASH background. OFF always shows black.
        uint8_t  runLength = 5;        ///< PULSE run length.
        uint8_t  speed     = 1;        ///< PULSE/FLOW/RAINBOW animation speed.
        uint32_t onMs      = 250;      ///< FLASH lit duration (ms).
        uint32_t offMs     = 250;      ///< FLASH blank duration (ms).
    };

    /**
     * @brief Signature for animation-setup callbacks used by stacked spreads.
     *
     * Called with the strand when a new layer is about to become active.
     * The callback should call any animation method (rainbow, flow, etc.).
     */
    using AnimSetupFn = void (*)(LedStrand&);

    /// @}

    // ========================================================================
    /// @name Construction
    /// @{

    /**
     * @brief Construct a strand on a standard ADI port.
     *
     * @param adiPort    ADI port number (1–8).
     * @param length     Number of LEDs.  Clamped to MAX_LEDS (64).
     * @param refreshMs  Refresh interval in milliseconds (default 20 ms = 50 Hz).
     */
    LedStrand(uint8_t adiPort, uint8_t length, uint32_t refreshMs = 20);

    /**
     * @brief Construct a strand on an ADI expander port.
     *
     * @param smartPort  Smart port the expander is connected to (1–21).
     * @param adiPort    ADI port on the expander (1–8).
     * @param length     Number of LEDs.  Clamped to MAX_LEDS (64).
     * @param refreshMs  Refresh interval in milliseconds (default 20 ms).
     */
    LedStrand(uint8_t smartPort, uint8_t adiPort, uint8_t length, uint32_t refreshMs = 20);

    /// @}

    // ========================================================================
    /// @name Initialization
    /// @{

    /**
     * @brief Initialize the hardware LED object.
     *
     * Must be called from PROS `initialize()` (or before the first tick).
     * Safe to call multiple times, subsequent calls are no-ops.
     */
    void init();

    /**
     * @brief Advance one animation frame.
     *
     * Called automatically by LedGroup's internal task.
     * **Do not call this directly.**
     */
    void tick();

    /// @}

    // ========================================================================
    /// @name Base Animations
    ///
    /// All methods are thread-safe.
    /// Changes take effect on the next refresh tick.
    /// @{

    /**
     * @brief Turn all LEDs off (set to black).
     */
    void off();

    /**
     * @brief Set all LEDs to a solid color.
     *
     * @param color  24-bit RGB color (0xRRGGBB).
     */
    void setColor(uint32_t color);

    /**
     * @brief Animate a run of color moving across a background.
     *
     * @param color      Foreground color (0xRRGGBB).
     * @param runLength  Number of lit pixels in the run.
     * @param speed      Pixels advanced per tick (1 = slowest).
     * @param bgColor    Background color (default black).
     * @param invert     Reverse the travel direction (default @c false).
     * @param bounce     Reverse direction at each end instead of wrapping
     *                   (default @c false).
     */
    void pulse(uint32_t color, uint8_t runLength, uint8_t speed,
               uint32_t bgColor = 0x000000, bool invert = false, bool bounce = false);

    /**
     * @brief Blink the whole strip on and off.
     *
     * Every pixel lights at once for @p onMs, then the whole strip shows
     * @p bgColor for @p offMs, and the cycle repeats.  On and off times are
     * independent, so rate and duty cycle are set separately.
     *
     * Durations are rounded to whole refresh ticks and clamped to a minimum
     * of one tick, so the strand can't be asked to blink faster than its
     * refresh interval (see the @c refreshMs constructor parameter).
     *
     * @param color    Lit color (0xRRGGBB).
     * @param onMs     How long the strip stays lit, in milliseconds.
     * @param offMs    How long the strip stays blank, in milliseconds.
     * @param bgColor  Background color shown while blank (default black).
     */
    void flash(uint32_t color, uint32_t onMs, uint32_t offMs, uint32_t bgColor = 0x000000);

    /**
     * @brief Scroll a two-color gradient across the strip.
     *
     * @param color1  Start color (0xRRGGBB).
     * @param color2  End color (0xRRGGBB).
     * @param speed   Pixels shifted per tick.
     * @param invert  Scroll in the reverse direction (default @c false).
     */
    void flow(uint32_t color1, uint32_t color2, uint8_t speed, bool invert = false);

    /**
     * @brief Scroll a full HSV rainbow across the strip.
     *
     * @param speed  Pixels shifted per tick.
     */
    void rainbow(uint8_t speed);

    /**
     * @brief Spawn randomly fading sparkles from a color palette.
     *
     * Each sparkle fades in to full brightness, holds briefly, then fades out.
     * New sparkles are spawned at most one per tick to stagger phase offsets.
     *
     * @param colors       Color palette, one color is chosen at random per sparkle.
     * @param densityPct   Target percentage of LEDs simultaneously lit (0–100).
     * @param fadeStep     Brightness step applied each tick (higher = faster fade).
     * @param bgColor      Background color shown on unlit pixels (default black).
     */
    void twinkle(const std::vector<uint32_t>& colors, uint8_t densityPct = 30,
                 uint8_t fadeStep = 16, uint32_t bgColor = 0x000000);

    /**
     * @brief Scroll a pattern of colored segments across the strip.
     *
     * @param segments   List of BitScrollSegment descriptors defining the pattern.
     * @param speed      Pixels advanced per tick.
     * @param invert     Scroll in the reverse direction (default @c false).
     * @param bgColor    Color shown between segments and in blank areas.
     * @param bounce     Rock the pattern back and forth instead of wrapping.
     * @param spacing    Gap pixels inserted between segments (default 5).
     * @param repeating  Tile the pattern across the whole strip (@c true) or
     *                   show a single copy of it (@c false).  Honoured for
     *                   both wrapping and @p bounce travel.
     */
    void bitscroll(const std::vector<BitScrollSegment>& segments, uint8_t speed,
                   bool invert = false, uint32_t bgColor = 0x000000, bool bounce = false,
                   uint8_t spacing = 5, bool repeating = true);

    /// @}

    // ========================================================================
    /// @name Splice Mask
    ///
    /// Overrides part of the strip, either as equal alternating bins sharing
    /// one overlay buffer (spliceMask) or as arbitrarily placed regions that
    /// each animate independently (spliceMaskCustom). The two are mutually
    /// exclusive, whichever was called most recently is what's active.
    /// @{

    /**
     * @brief Apply a splice mask that overrides alternating equal-width bins.
     *
     * The strip is divided into `sections + 1` equal bins.  Even-indexed bins
     * (or their complement when @p invert is @c true) show @p bgColor, or the
     * overlay buffer when @p useOverlay is @c true, instead of the active base
     * animation.
     *
     * @param sections      Number of divider boundaries (e.g. 1 = two halves).
     *                      Pass @c 0 to disable.
     * @param invert        Swap which bins are overridden (default @c false).
     * @param alternating   Toggle @p invert automatically every @p altPeriodMs.
     * @param altPeriodMs   Toggle period when @p alternating is @c true (ms).
     * @param bgColor       Color shown in masked bins when @p useOverlay is @c false.
     * @param useOverlay    Show the overlay buffer in masked bins instead of @p bgColor.
     */
    void spliceMask(uint8_t sections, bool invert = false, bool alternating = false,
                    uint32_t altPeriodMs = 100, uint32_t bgColor = 0x000000,
                    bool useOverlay = false);

    /**
     * @brief Apply a splice mask made of independently placed/sized regions,
     * each running its own animation.
     *
     * Unlike spliceMask(), regions can start and end anywhere on the strip,
     * don't alternate, and each gets a dedicated buffer generated over just
     * its own width. So e.g. one region can rainbow-scroll while another
     * pulses, simultaneously. Regions stay fixed until spliceMaskCustom() or
     * clearSpliceMask() is called again. Later entries win where regions
     * overlap.
     *
     * @param regions  Override regions to apply.
     */
    void spliceMaskCustom(const std::vector<SpliceRegion>& regions);

    /**
     * @brief Remove the active splice mask, whichever kind is active.
     */
    void clearSpliceMask();

    /// @}

    // ========================================================================
    /// @name Overlay Animations
    ///
    /// Write into a second buffer.  The overlay is revealed by the spread mask
    /// during a centerSpread animation.
    /// @{

    /** @brief Set the overlay buffer to a solid color. */
    void overlaySetColor(uint32_t color);

    /** @brief Animate a moving run in the overlay buffer. */
    void overlayPulse(uint32_t color, uint8_t runLength, uint8_t speed,
                      uint32_t bgColor = 0x000000);

    /** @brief Blink the overlay buffer on and off (see flash()). */
    void overlayFlash(uint32_t color, uint32_t onMs, uint32_t offMs,
                      uint32_t bgColor = 0x000000);

    /** @brief Scroll a gradient in the overlay buffer. */
    void overlayFlow(uint32_t color1, uint32_t color2, uint8_t speed);

    /** @brief Scroll a rainbow in the overlay buffer. */
    void overlayRainbow(uint8_t speed);

    /// @}

    // ========================================================================
    /// @name Center Spread
    ///
    /// Reveals the overlay buffer from the center outward (or edges inward),
    /// then swaps layers and repeats.
    /// @{

    /**
     * @brief Begin a center-outward reveal of the overlay buffer.
     *
     * The spread mask advances one step every @p tickInterval ticks.  When
     * the mask has covered the full strip, the overlay becomes the new base
     * and the animation restarts.
     *
     * @param tickInterval  Ticks between each pixel step (1 = fastest).
     *                      At refreshMs=20, interval=10 → 200 ms/pixel.
     * @param invert        Reveal from the edges inward instead of center out.
     */
    void centerSpread(uint8_t tickInterval = 8, bool invert = false);

    /**
     * @brief center spread that cycles through a list of setup functions.
     *
     * Each function is called with the strand just before it becomes the next
     * spreading overlay.  Requires at least two functions.
     *
     * @param layers        List of AnimSetupFn callbacks, called in order (cycling).
     * @param tickInterval  Ticks between each pixel step.
     * @param invert        Reveal from the edges inward.
     */
    void centerSpreadStacked(const std::vector<AnimSetupFn>& layers,
                             uint8_t tickInterval = 8, bool invert = false);

    /**
     * @brief Center spread that expands, then contracts before swapping layers.
     *
     * Gives a "wipe in, wipe out, next layer" rhythm.
     *
     * @param tickInterval  Ticks between each pixel step.
     * @param invert        Reveal from the edges inward.
     */
    void centerSpreadBounce(uint8_t tickInterval = 8, bool invert = false);

    /**
     * @brief Bounce spread with automatic layer cycling.
     *
     * Combines the behaviour of centerSpreadBounce() and centerSpreadStacked().
     *
     * @param layers        List of AnimSetupFn callbacks.
     * @param tickInterval  Ticks between each pixel step.
     * @param invert        Reveal from the edges inward.
     */
    void centerSpreadBounceStacked(const std::vector<AnimSetupFn>& layers,
                                   uint8_t tickInterval = 8, bool invert = false);

    /// @}

    // ========================================================================
    /// @name Brightness
    /// @{

    /**
     * @brief Set global brightness for this strand.
     *
     * Applied non-destructively at flush time, the animation buffers are not
     * modified.  Does not require the strand to be re-initialized.
     *
     * @param pct  Brightness percentage (0 = off, 100 = full).  Clamped to
     *             [0, 100].
     */
    void setBrightness(uint8_t pct);

    /**
     * @brief Return the current brightness percentage.
     */
    uint8_t getBrightness() const { return brightnessPct; }

    /// @}

    // ========================================================================
    /// @name Profile System
    /// @{

    /**
     * @brief Attach a Profile and reset the mode stack.
     *
     * @param profile  Pointer to a statically-allocated Profile.
     *                 Must remain valid for the lifetime of the attachment.
     */
    void attachProfile(const Profile* profile);

    /**
     * @brief Detach the active profile and turn the strand off.
     */
    void detachProfile();

    /**
     * @brief Push a persistent mode onto the mode stack.
     *
     * The mode remains active until explicitly removed with deactivateMode().
     * Calling this for an already-active persistent mode is a no-op.
     *
     * @param modeIdx  Index into the attached profile's modes array.
     */
    void activateMode(uint8_t modeIdx);

    /**
     * @brief Push a timed mode onto the mode stack.
     *
     * The mode auto-expires after @p durationMs milliseconds.  If the mode
     * is already active (as a timed entry), its deadline is extended rather
     * than creating a duplicate.
     *
     * @param modeIdx     Index into the attached profile's modes array.
     * @param durationMs  How long the mode stays active (milliseconds).
     */
    void activateModeTimed(uint8_t modeIdx, uint32_t durationMs);

    /**
     * @brief Remove a mode from the stack immediately.
     *
     * @param modeIdx  Index of the mode to remove.
     */
    void deactivateMode(uint8_t modeIdx);

    /// @}

    // ========================================================================
    /// @name Accessors
    /// @{

    /** @brief Return the number of LEDs in this strand. */
    uint8_t getLength() const { return length; }

    /// @}

    // ========================================================================
    // Maximum supported LEDs per strand.
    static constexpr uint8_t MAX_LEDS = 64;

private:
    // ---- Hardware ----
    uint8_t  adiPort;
    uint8_t  smartPort  = 0;
    uint8_t  length;
    uint32_t refreshMs;
    pros::adi::Led* led = nullptr;
    pros::Mutex mutex;

    enum class AnimMode : uint8_t { STATIC, SHIFT, CENTER_SPREAD, TWINKLE, FLASH };

    // Base buffer
    AnimMode              animMode     = AnimMode::STATIC;
    int                   shiftStep    = 0;
    uint8_t               shiftVariant = 0;
    std::vector<uint32_t> buffer;

    // Pulse bounce
    uint8_t  pulseRunLen = 0;
    uint32_t pulseColor  = 0;
    uint32_t pulseBg     = 0;
    uint8_t  pulseSpeed  = 1;
    int16_t  pulseOffset = 0;
    int8_t   pulseDir    = 1;

    // Flash - whole-strip blink driven by tick counts rather than a shifting
    // buffer, so the lit and blank halves can have independent durations.
    uint32_t flashColor    = 0;
    uint32_t flashBgColor  = 0;
    uint16_t flashOnTicks  = 1;
    uint16_t flashOffTicks = 1;
    uint16_t flashCounter  = 0;
    bool     flashLit      = true;

    // Bitscroll bounce
    std::vector<uint32_t> bitscrollMaster;
    int16_t               bounceScrollPos = 0;
    int8_t                bounceScrollDir = 1;
    uint8_t               bounceSpeed     = 1;

    // Twinkle
    std::vector<uint8_t>  twinkleLevel;
    std::vector<uint8_t>  twinkleTarget;
    std::vector<uint8_t>  twinkleColorIdx;
    std::vector<uint8_t>  twinkleHoldTicks;
    std::vector<uint32_t> twinklePalette;
    uint8_t               twinkleDensityPct = 30;
    uint8_t               twinkleFadeStep   = 16;
    uint32_t              twinkleBgColor    = 0x000000;

    // Splice mask
    enum class SpliceMode : uint8_t { SPLIT, CUSTOM };

    // Runtime animation state for one CUSTOM-mode region, a scaled-down
    // version of the overlay buffer (buffer + shift step/speed), but one per
    // region instead of shared.
    struct SpliceRegionState {
        uint8_t                start = 0;
        std::vector<uint32_t>  buffer;
        int                    shiftStep  = 0;
        uint8_t                shiftSpeed = 0;
        // FLASH regions blink their whole buffer on a tick timer instead of
        // shifting it, mirroring the strand-wide flash state.
        bool     flashing      = false;
        uint32_t flashColor    = 0;
        uint32_t flashBgColor  = 0;
        uint16_t flashOnTicks  = 1;
        uint16_t flashOffTicks = 1;
        uint16_t flashCounter  = 0;
        bool     flashLit      = true;
    };

    bool              spliceActive      = false;
    SpliceMode        spliceMode        = SpliceMode::SPLIT;
    uint8_t           spliceSections    = 0;
    bool              spliceInvert      = false;
    bool              spliceAlternating = false;
    uint32_t          spliceAltMs       = 100;
    uint32_t          spliceBgColor     = 0x000000;
    bool              spliceUseOverlay  = false;
    bool              spliceAltPhase    = false;
    uint32_t          spliceLastToggleMs = 0;
    std::vector<bool>     spliceShowAnim;
    std::vector<uint32_t> splicePixelBg;
    std::vector<bool>     splicePixelUseOverlay;
    std::vector<int16_t>  splicePixelRegionIdx;  // CUSTOM mode: index into spliceRegions, or -1
    std::vector<SpliceRegionState> spliceRegions; // CUSTOM mode only

    // Overlay buffer
    AnimMode              overlayAnimMode   = AnimMode::STATIC;
    int                   overlayShiftStep  = 0;
    uint8_t               overlayShiftSpeed = 0;
    std::vector<uint32_t> overlayBuffer;

    // Overlay flash - mirrors the base flash state above.
    uint32_t overlayFlashColor    = 0;
    uint32_t overlayFlashBgColor  = 0;
    uint16_t overlayFlashOnTicks  = 1;
    uint16_t overlayFlashOffTicks = 1;
    uint16_t overlayFlashCounter  = 0;
    bool     overlayFlashLit      = true;

    // Center spread
    std::vector<bool>        spreadMask;
    uint8_t                  spreadPos          = 0;
    uint8_t                  spreadTickInterval = 8;
    uint8_t                  spreadTickCounter  = 0;
    std::vector<AnimSetupFn> spreadLayers;
    uint8_t                  spreadLayerIdx  = 0;
    bool                     spreadInvert    = false;
    bool                     spreadBounce    = false;
    bool                     spreadReturning = false;

    // Brightness
    uint8_t brightnessPct = 100;

    // Profile state
    struct ModeEntry {
        uint8_t  modeIdx;
        bool     persistent;
        uint32_t endMs;
    };
    const Profile*         activeProfile = nullptr;
    std::vector<ModeEntry> modeStack;
    int16_t                lastModeIdx = -1;

    // Internal implementations (no lock)
    void setColorNL(uint32_t color);
    void pulseNL(uint32_t color, uint8_t runLen, uint8_t speed, uint32_t bg, bool invert, bool bounce);
    void flashNL(uint32_t color, uint32_t onMs, uint32_t offMs, uint32_t bg);
    void flowNL(uint32_t c1, uint32_t c2, uint8_t speed, bool invert);
    void rainbowNL(uint8_t speed);
    void twinkleNL(const std::vector<uint32_t>& colors, uint8_t densityPct,
                   uint8_t fadeStep, uint32_t bgColor);
    void bitscrollNL(const std::vector<BitScrollSegment>& segments, uint8_t speed, bool invert,
                     uint32_t bgColor, bool bounce, uint8_t spacing, bool repeating);

    void overlaySetColorNL(uint32_t color);
    void overlayPulseNL(uint32_t color, uint8_t runLen, uint8_t speed, uint32_t bg);
    void overlayFlashNL(uint32_t color, uint32_t onMs, uint32_t offMs, uint32_t bg);
    void overlayFlowNL(uint32_t c1, uint32_t c2, uint8_t speed);
    void overlayRainbowNL(uint8_t speed);

    void advanceCenterSpread();
    void advanceTwinkle();
    void advanceFlash();
    void advanceOverlayFlash();
    void advancePulseBounce();
    void advanceBitscrollBounce();
    void fillBitscrollFromMaster();
    void advanceSpliceAlternating(uint32_t nowMs);
    void advanceSpliceRegions();
    void rebuildSpliceMask();
    void doLayerSwap();

    void     shiftBuffer();
    void     shiftOverlayBuffer();
    void     flushBuffer();
    uint32_t composite(uint32_t base, uint32_t overlay, bool useOverlay) const;

    int16_t computeEffectiveMode() const;
    void    pruneExpired(uint32_t now);
    uint32_t applyBrightness(uint32_t color) const;
    uint16_t msToTicks(uint32_t ms) const;

    static std::vector<uint32_t> genGradient(uint32_t c1, uint32_t c2, uint8_t len);
    static std::vector<uint32_t> genRainbow(uint8_t len);
};

} // namespace hitlib