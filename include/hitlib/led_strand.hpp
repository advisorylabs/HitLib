#pragma once
#include "led_profile.hpp"
#include "pros/adi.hpp"
#include "pros/rtos.hpp"
#include <cstdint>
#include <utility>
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
 * base buffer      <- flow / rainbow / pulse / bitscroll / twinkle, or a
 *                     level meter driven by hand, by a live value, or by a
 *                     music envelope
 * overlay buffer   <- second independent animation
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
     * @brief Signature for the reader a fill meter follows, see levelSource().
     *
     * Called once per refresh tick, from the LedGroup task rather than from
     * your loop, and expected to just read something and return it, in
     * whatever units that something already speaks.  Mapping the number onto
     * the strip is levelSource()'s job, not the reader's.
     *
     * A capture-less lambda converts to one of these, which is how it usually
     * gets written:
     *
     * @code{.cpp}
     * pros::Motor intake(11);
     * strand.levelSource([] { return intake.get_temperature(); }, 20.0, 70.0);
     * @endcode
     *
     * A lambda has to actually return a @c double, so a reading that comes
     * back as an integer needs saying so: `[] { return (double)rot.get_angle(); }`.
     *
     * @note Because the reader runs on the LED task, it must not block, and
     *       must not take a lock your own loop already holds while waiting on
     *       the LEDs.  Reading a device or a plain variable is fine.
     */
    using LevelFn = double (*)();

    /**
     * @brief What a custom splice mask region shows.
     *
     * OFF through RAINBOW mirror the overlay*() animation vocabulary (see
     * @ref overlaySetColor "Overlay Animations"), since each region gets a
     * buffer built and animated the same way.
     *
     * @c GAUGE is the odd one out: it animates from a *reading* rather than
     * from a clock, which is what turns one region into an independent meter.
     * See @ref GaugeStop and SpliceRegion's gauge fields.
     */
    enum class SpliceRegionAnimKind : uint8_t { OFF, SOLID, PULSE, FLASH, FLOW, RAINBOW, GAUGE };

    /**
     * @brief One color on a GAUGE region's scale.
     *
     * Stops are given in the reading's own units, not in pixels or in 0-255,
     * so a scale reads as what it means: `{55.0, 0xFF7000}` is "orange at
     * 55 °C".  They are sorted and mapped onto the region's @c emptyAt -
     * @c fullAt range once, when the mask is applied.
     *
     * Below the first stop and above the last, the gauge shows that stop's
     * color, so a scale never has to cover a range it doesn't care about.
     *
     * @see GaugeBlend for what happens between two stops.
     */
    struct GaugeStop {
        double   at;    ///< Reading this color belongs to, in the source's units.
        uint32_t color; ///< Color shown at that reading (0xRRGGBB).
    };

    /**
     * @brief How a GAUGE region turns its level into pixels.
     */
    enum class GaugeStyle : uint8_t {
        HEAT, ///< The whole region shows one color, picked off the scale.
        BAR,  ///< The region fills proportionally, like a miniature levelFill().
    };

    /**
     * @brief How a GAUGE region moves between two stops.
     */
    enum class GaugeBlend : uint8_t {
        LERP, ///< Blend smoothly, so a rising reading slides between colors.
        STEP, ///< Hold each stop's color until the next one is reached.
    };

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
        uint32_t bgColor   = 0x000000; ///< PULSE/FLASH background, and a GAUGE BAR's unlit part.
        uint8_t  runLength = 5;        ///< PULSE run length.
        uint8_t  speed     = 1;        ///< PULSE/FLOW/RAINBOW animation speed.
        uint32_t onMs      = 250;      ///< FLASH lit duration (ms).
        uint32_t offMs     = 250;      ///< FLASH blank duration (ms).
        bool     seamless  = true;     ///< FLOW only. See @c flow().

        // ---- GAUGE only, ignored by every other kind ----

        /// Value this gauge follows, polled once per tick.  @c nullptr leaves
        /// the region hand-driven through setRegionLevel().
        LevelFn read = nullptr;
        double  emptyAt = 0.0;    ///< Reading at the bottom of the scale.
        double  fullAt  = 100.0;  ///< Reading at the top of it.
        bool    wrap    = false;  ///< Cycle past @c fullAt instead of pinning at it.
        uint8_t smoothing = 0;    ///< 0-99, as in levelSource().
        bool    invert  = false;  ///< BAR only: fill from the far end of the region.
        GaugeStyle style = GaugeStyle::HEAT; ///< Whole-region color, or a bar.
        GaugeBlend blend = GaugeBlend::LERP; ///< Blend between stops, or hold each.
        /// The scale, in the reading's own units.  Empty falls back to
        /// @c color at @c emptyAt blending to @c color2 at @c fullAt, so a
        /// two-color gauge needs no stops at all.
        std::vector<GaugeStop> stops;
    };

    /**
     * @brief A song's intensity envelope, baked ahead of time into one
     * 8-bit sample per frame.
     *
     * The V5 has no audio input and no MIDI decoder, so a strand can't listen
     * to the music it's syncing to.  Instead Pattern Studio reads a MIDI file
     * on the desktop, renders it down to a level-per-frame envelope, and
     * exports that envelope as a `constexpr` table.  On the robot musicSync()
     * just plays the table back against the wall clock, which costs one array
     * lookup and a lerp per tick regardless of how long or dense the song is.
     *
     * A three-minute song at the default 25 ms frame is about 7 KB of flash.
     *
     * @code{.cpp}
     * // Generated by Pattern Studio, in the exported header:
     * inline const uint8_t songSamples[] = {0, 3, 17, 42, ...};
     * inline const hitlib::LedStrand::MusicTrack song = {songSamples, 8840, 25};
     * @endcode
     *
     * @note Like Profile, a MusicTrack must outlive the strand it is handed
     *       to, the strand stores a pointer, not a copy.
     */
    struct MusicTrack {
        const uint8_t* samples;    ///< Intensity 0-255, one entry per frame.
        uint16_t       frameCount; ///< Number of samples in @p samples.
        uint16_t       frameMs;    ///< Milliseconds between consecutive samples.
    };

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
     * @param color1    Start color (0xRRGGBB).
     * @param color2    End color (0xRRGGBB).
     * @param speed     Pixels shifted per tick.
     * @param invert    Scroll in the reverse direction (default @c false).
     * @param seamless  Loop the gradient back to @p color1 instead of cutting
     *                  straight from @p color2 to @p color1 at the wrap
     *                  (default @c true).
     */
    void flow(uint32_t color1, uint32_t color2, uint8_t speed, bool invert = false,
              bool seamless = true);

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
    /// @name Level Meter & Music Sync
    ///
    /// A meter fills the strip from one end in proportion to a 0-255 level.
    /// levelFill() decides what it looks like, and one of three things decides
    /// how full it is:
    ///
    /// - setLevel(), called from your own code;
    /// - levelSource(), which polls a value for you every tick, so a battery
    ///   gauge or a motor-heat bar needs no code in your control loop at all;
    /// - musicSync(), which plays a baked MusicTrack so the strip fills and
    ///   empties in time with a song.
    ///
    /// The three are mutually exclusive: whichever was set up last is what
    /// drives the meter, so handing it to a song doesn't leave a stale sensor
    /// fighting it for the same pixels.
    /// @{

    /**
     * @brief Configure the strip as a level meter, and take manual control of it.
     *
     * Sets up the colors the meter fills with, then leaves the level itself
     * alone, drive it with setLevel().  A meter starts empty, so nothing
     * lights until the first setLevel() call.
     *
     * With @p gradient the two colors are laid out across the **whole strip**,
     * not across the lit part, so a given pixel is always the same color no
     * matter how full the meter is (a VU meter that runs green at the bottom
     * and red at the top, rather than a gradient that stretches).  With
     * @p gradient @c false only @p color is used.
     *
     * The pixel at the edge of the fill is dimmed proportionally rather than
     * snapping on, so a 30-LED strand shows a smooth ramp instead of 30
     * visible steps.
     *
     * Calling this takes the meter back to manual control: any MusicTrack or
     * levelSource() reader is detached, since either would overwrite whatever
     * setLevel() puts there on the very next tick.
     *
     * @param color    Fill color, and the gradient's start color (0xRRGGBB).
     * @param color2   Gradient end color.  Ignored unless @p gradient.
     * @param gradient Blend @p color to @p color2 across the strip.
     * @param bgColor  Color shown on the unfilled part (default black).
     * @param invert   Fill from the far end of the strip instead of pixel 0.
     */
    void levelFill(uint32_t color, uint32_t color2 = 0x000000, bool gradient = false,
                   uint32_t bgColor = 0x000000, bool invert = false);

    /**
     * @brief Set how full the meter is.
     *
     * Cheap enough to call every control-loop iteration, it only stores a
     * byte, the fill is computed at flush time.  Detaches any MusicTrack or
     * levelSource() reader, so manual updates aren't fighting one of those for
     * the same meter.
     *
     * @param level  0 = empty, 255 = the whole strip lit.
     */
    void setLevel(uint8_t level);

    /** @brief Return the meter's current level (0-255). */
    uint8_t getLevel() const { return levelValue; }

    /**
     * @brief Point the meter at a live value and let it follow it on its own.
     *
     * The strand polls @p read once per refresh tick and maps what comes back
     * onto the strip, so a gauge needs no code in your control loop: set it up
     * once (in @c initialize(), or in a ProfileMode's @c onActivate) and the
     * bar tracks the value from then on.
     *
     * Units are yours to choose, @p emptyAt and @p fullAt are given in
     * whatever the reader returns:
     *
     * @code{.cpp}
     * pros::Motor intake(11);
     *
     * strand.levelFill(0x00FF00, 0xFF0000, true);                 // green -> red scale
     * strand.levelSource([] { return intake.get_temperature(); },
     *                    20.0, 70.0);                             // cool -> shutdown hot
     *
     * // A battery gauge that empties as the battery does:
     * strand.levelSource([] { return pros::battery::get_capacity(); }, 0.0, 100.0);
     *
     * // A bar that fills once per revolution and starts over:
     * strand.levelSource([] { return arm.get_position(); }, 0.0, 360.0, true);
     * @endcode
     *
     * Values outside the range clamp to empty or full, so a gauge parks at
     * either end rather than wrapping around unexpectedly.  With @p wrap it
     * cycles instead, which is what a continuously turning motor or a heading
     * wants: 450° of a 0-360 range shows the bar a quarter full, not full.
     *
     * Putting @p fullAt below @p emptyAt is allowed and reverses the meter, so
     * a "distance remaining" bar that drains as a number climbs needs no
     * arithmetic of its own.
     *
     * This keeps the colors from the preceding levelFill(), it only changes
     * what drives the fill, so call levelFill() first.  Any MusicTrack is
     * detached.
     *
     * @param read       Value to follow.  @c nullptr just clears the source,
     *                   which is what lets exported code name a hook that has
     *                   not been assigned yet.
     * @param emptyAt    Reading that shows an empty strip.
     * @param fullAt     Reading that shows a full one.
     * @param wrap       Cycle back to empty past @p fullAt instead of clamping.
     * @param smoothing  0-99.  How much of the previous frame's fill to keep
     *                   each tick: 0 follows the value exactly, higher glides
     *                   toward it, which is worth having on anything noisy
     *                   (motor velocity, current draw).  The bar still reaches
     *                   its target, it just takes a few ticks to get there.
     */
    void levelSource(LevelFn read, double emptyAt, double fullAt, bool wrap = false,
                     uint8_t smoothing = 0);

    /**
     * @brief Stop following a levelSource() reader, leaving the fill where it is.
     *
     * The meter stays set up and keeps its colors, it just stops moving on its
     * own, so setLevel() takes over cleanly from here.
     */
    void clearLevelSource();

    /** @brief Whether a levelSource() reader is currently driving the meter. */
    bool levelSourceActive() const { return levelRead != nullptr; }

    /**
     * @brief Fill the meter from a baked music envelope, and start playing it.
     *
     * Playback is anchored to the wall clock at the moment of this call, so
     * call it when the music starts, typically from a ProfileMode's
     * @c onActivate.  The strand then tracks the song on its own with no
     * further calls, samples are interpolated between frames so the fill stays
     * smooth even when the envelope's frame rate is coarser than @c refreshMs.
     *
     * Colors, @p gradient and @p invert behave exactly as in levelFill().  Any
     * levelSource() reader is detached, the song drives the meter now.
     *
     * @param track        Envelope to play.  Must outlive the strand.
     * @param color        Fill color, and the gradient's start color.
     * @param color2       Gradient end color.  Ignored unless @p gradient.
     * @param gradient     Blend @p color to @p color2 across the strip.
     * @param bgColor      Color shown on the unfilled part.
     * @param invert       Fill from the far end of the strip.
     * @param sensitivity  Gain applied to every sample, as a percentage.
     *                     100 = the envelope as baked, higher makes quiet
     *                     passages reach further up the strip (and clips loud
     *                     ones at full), lower makes it more selective.
     * @param loop         Restart from the top when the song ends instead of
     *                     going dark.
     */
    void musicSync(const MusicTrack& track, uint32_t color, uint32_t color2 = 0x000000,
                   bool gradient = false, uint32_t bgColor = 0x000000, bool invert = false,
                   uint8_t sensitivity = 100, bool loop = false);

    /**
     * @brief Jump playback to a position in the song.
     *
     * @param positionMs  Offset from the start of the track, in milliseconds.
     */
    void musicSeek(uint32_t positionMs);

    /**
     * @brief Pause or resume playback, holding the meter where it is.
     *
     * @param paused  @c true to pause, @c false to resume.
     */
    void musicPause(bool paused = true);

    /** @brief Whether a MusicTrack is attached and not paused. */
    bool musicPlaying() const { return musicTrack != nullptr && !musicPaused; }

    /** @brief Current playback position in milliseconds. */
    uint32_t musicPositionMs() const;

    /**
     * @brief Change the gain applied to envelope samples without restarting.
     *
     * @param pct  Percentage gain, see musicSync()'s @p sensitivity.
     */
    void setSensitivity(uint8_t pct);

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
     * A GAUGE region is the same idea pointed at a sensor instead of a clock:
     * it polls its own reader every tick and colors itself off its own scale,
     * so one strip can carry six independent meters.  That is the whole point
     * of it - the strand-wide levelFill() meter is one per strand, and a
     * drivebase has six motors.
     *
     * @code{.cpp}
     * // Six motors, six segments of a 60-LED strip under the drivebase, each
     * // colored by how hot its own motor is.
     * pros::Motor drive[6] = {...};
     * std::vector<LedStrand::SpliceRegion> regions;
     * for (uint8_t i = 0; i < 6; ++i) {
     *     regions.push_back(LedStrand::motorHeatGauge(i * 10, 9, readers[i]));
     * }
     * strand.spliceMaskCustom(regions);
     * @endcode
     *
     * @param regions  Override regions to apply.
     */
    void spliceMaskCustom(const std::vector<SpliceRegion>& regions);

    /**
     * @brief Set how full a hand-driven GAUGE region is.
     *
     * The counterpart of setLevel() for one region rather than the whole
     * strand, for gauges whose SpliceRegion left @c read as @c nullptr.  Cheap
     * enough to call every control-loop iteration: it stores a byte, and the
     * region repaints at flush time.
     *
     * Ignored for a region that has a reader of its own, which would overwrite
     * it on the very next tick anyway.
     *
     * @param regionIdx  Index into the vector last handed to spliceMaskCustom().
     * @param level      0 = the bottom of the region's scale, 255 = the top.
     */
    void setRegionLevel(uint8_t regionIdx, uint8_t level);

    /**
     * @brief A GAUGE region pre-loaded with the V5 motor's own heat schedule.
     *
     * The stops are the temperatures the motor itself changes behaviour at, so
     * the colors mean something specific rather than being a pretty ramp:
     *
     * | Reading  | Color   | What the motor is doing        |
     * |----------|---------|--------------------------------|
     * | 20 °C    | green   | cold, full power               |
     * | 45 °C    | yellow  | warm, nearing the first cut    |
     * | 55 °C    | orange  | current limited to 50%         |
     * | 60 °C    | red     | current limited to 25%         |
     * | 65 °C    | deep red| current limited to 12.5%       |
     * | 70 °C    | magenta | shut down                      |
     *
     * Magenta at the top is deliberate - it is the one color on the scale that
     * cannot be mistaken for "a bit hotter than the last one".
     *
     * The returned region is an ordinary SpliceRegion, so anything about it can
     * be overridden before it is handed to spliceMaskCustom().
     *
     * @param start  First pixel of the segment.
     * @param width  Number of pixels in it.
     * @param read   Reader returning a motor temperature in °C.  @c nullptr
     *               leaves the segment driven by setRegionLevel().
     */
    static SpliceRegion motorHeatGauge(uint8_t start, uint8_t width, LevelFn read = nullptr);

    /**
     * @brief Remove the active splice mask, whichever kind is active.
     */
    void clearSpliceMask();

    /// @}

    // ========================================================================
    /// @name Overlay Animations
    ///
    /// Write into a second buffer, shown in spliceMask()'s masked bins instead
    /// of @c bgColor when @p useOverlay is set.
    /// @{

    /** @brief Set the overlay buffer to a solid color. */
    void overlaySetColor(uint32_t color);

    /** @brief Animate a moving run in the overlay buffer. */
    void overlayPulse(uint32_t color, uint8_t runLength, uint8_t speed,
                      uint32_t bgColor = 0x000000);

    /** @brief Blink the overlay buffer on and off (see flash()). */
    void overlayFlash(uint32_t color, uint32_t onMs, uint32_t offMs,
                      uint32_t bgColor = 0x000000);

    /**
     * @brief Scroll a gradient in the overlay buffer.
     * @param seamless  Loop the gradient back to @p color1 instead of
     *                  cutting straight from @p color2 to @p color1 at the
     *                  wrap (default @c true). See @c flow().
     */
    void overlayFlow(uint32_t color1, uint32_t color2, uint8_t speed, bool seamless = true);

    /** @brief Scroll a rainbow in the overlay buffer. */
    void overlayRainbow(uint8_t speed);

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

    enum class AnimMode : uint8_t { STATIC, SHIFT, TWINKLE, FLASH, LEVEL };

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

    // Level meter - buffer holds the meter's colors across the whole strip and
    // levelValue picks how much of it is revealed, so changing the level costs
    // nothing until flush time.
    uint8_t  levelValue  = 0;
    uint32_t levelBg     = 0x000000;
    bool     levelInvert = false;

    // Live fill source - a reader polled once per tick plus the range that
    // maps what it returns onto 0-255. levelPrimed exists so the first sample
    // after levelSource() lands instantly instead of the bar crawling up to it
    // from empty through the smoothing filter.
    LevelFn  levelRead   = nullptr;
    double   levelEmptyAt = 0.0;
    double   levelFullAt  = 100.0;
    bool     levelWrap    = false;
    uint8_t  levelSmooth  = 0;
    bool     levelPrimed  = false;

    // Music sync - playback is a wall-clock anchor rather than a tick counter,
    // so a strand that misses a tick (or refreshes slowly) stays in step with
    // the music instead of drifting behind it.
    const MusicTrack* musicTrack   = nullptr;
    uint32_t musicAnchorMs         = 0;  // millis() that corresponds to position 0
    uint32_t musicPausedAt         = 0;  // position held while paused
    bool     musicPaused           = false;
    bool     musicLoop             = false;
    uint8_t  musicSensitivity      = 100;

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
        // Index in the vector the caller passed to spliceMaskCustom(), which
        // is what setRegionLevel() addresses. Not the index in spliceRegions:
        // SOLID and OFF regions never get a state, so the two disagree.
        uint8_t  userIdx = 0;
        // GAUGE - a per-region copy of the strand-wide meter's state, so each
        // segment follows its own reading at its own range and smoothing.
        bool       gauge       = false;
        LevelFn    levelRead   = nullptr;
        double     levelEmptyAt = 0.0;
        double     levelFullAt  = 100.0;
        bool       levelWrap    = false;
        uint8_t    levelSmooth  = 0;
        uint8_t    levelValue   = 0;
        bool       levelPrimed  = false;
        bool       gaugeInvert  = false;
        GaugeStyle gaugeStyle   = GaugeStyle::HEAT;
        GaugeBlend gaugeBlend   = GaugeBlend::LERP;
        uint32_t   gaugeBg      = 0x000000;
        // The scale, resolved onto the 0-255 axis levelValue lives on, so a
        // tick only has to bracket a byte rather than redo the mapping.
        std::vector<std::pair<uint8_t, uint32_t>> gaugeStops;
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
    // Bumped whenever the mask is replaced. advanceSpliceRegions() releases
    // the mutex to run a gauge's reader (user code, which may call back in),
    // and compares this afterwards to notice that the regions it was walking
    // are gone.
    uint32_t spliceGeneration = 0;

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
    void flowNL(uint32_t c1, uint32_t c2, uint8_t speed, bool invert, bool seamless);
    void rainbowNL(uint8_t speed);
    void twinkleNL(const std::vector<uint32_t>& colors, uint8_t densityPct,
                   uint8_t fadeStep, uint32_t bgColor);
    void bitscrollNL(const std::vector<BitScrollSegment>& segments, uint8_t speed, bool invert,
                     uint32_t bgColor, bool bounce, uint8_t spacing, bool repeating);

    void overlaySetColorNL(uint32_t color);
    void overlayPulseNL(uint32_t color, uint8_t runLen, uint8_t speed, uint32_t bg);
    void overlayFlashNL(uint32_t color, uint32_t onMs, uint32_t offMs, uint32_t bg);
    void overlayFlowNL(uint32_t c1, uint32_t c2, uint8_t speed, bool seamless);
    void overlayRainbowNL(uint8_t speed);

    void levelFillNL(uint32_t color, uint32_t color2, bool gradient, uint32_t bg, bool invert);

    void advanceLevel();
    void advanceTwinkle();
    void advanceFlash();
    void advanceOverlayFlash();
    void advancePulseBounce();
    void advanceBitscrollBounce();
    void fillBitscrollFromMaster();
    void advanceSpliceAlternating(uint32_t nowMs);
    void advanceSpliceRegions();
    void paintGaugeRegion(SpliceRegionState& state);
    void rebuildSpliceMask();

    void     shiftBuffer();
    void     shiftOverlayBuffer();
    void     flushBuffer();
    uint32_t levelPixel(uint8_t i, uint32_t full, uint8_t frac) const;
    uint8_t  sampleMusicNL(uint32_t positionMs) const;
    uint8_t  mapLevelNL(double raw) const;
    uint8_t  smoothLevelNL(uint8_t target) const;

    // The mapping and smoothing above, with the range passed in rather than
    // read off the strand, so a gauge region can reuse them for its own.
    static uint8_t mapLevelTo(double raw, double emptyAt, double fullAt, bool wrap);
    static uint8_t smoothLevelTo(uint8_t target, uint8_t current, uint8_t smoothing, bool wrap);
    static uint32_t gaugeColorAt(const SpliceRegionState& state, uint8_t level);

    int16_t computeEffectiveMode() const;
    void    pruneExpired(uint32_t now);
    uint32_t applyBrightness(uint32_t color) const;
    uint16_t msToTicks(uint32_t ms) const;

    static std::vector<uint32_t> genGradient(uint32_t c1, uint32_t c2, uint8_t len,
                                              bool seamless = false);
    static std::vector<uint32_t> genRainbow(uint8_t len);
};

} // namespace hitlib