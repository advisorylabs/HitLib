#pragma once
#include "hitlib/led_driver.hpp"
#include "hitlib/led_sequencer.hpp"
#include <cstdint>

namespace leds {

// ---------------------------------------------------------------
// Mode & Priority
// ---------------------------------------------------------------
enum class Mode : uint8_t {
    OFF,
    IDLE,
    ALLIANCERED,
    ALLIANCEBLUE,
    SCRAPER,
    SCORING,
    WARNING,
    ENDGAME,
    SHOWCASE
};

enum class Priority : uint8_t {
    BASE     = 0,
    ALLIANCE = 10,
    SCRAPER  = 40,
    SCORING  = 90,
    ENDGAME  = 100
};

enum class Alliance : uint8_t { RED = 0, BLUE = 1 };

// ---------------------------------------------------------------
// MatchProfile
//
// Define one or more of these and pass them to leds::init().
// Each field is a function pointer to code you write.
//
// idleFn        — shown before a match / during disabled
// matchRedFn    — shown when alliance is RED
// matchBlueFn   — shown when alliance is BLUE
// endgamePrepFn — called once when endgame starts; use it to
//                 set up any state your endgameSeq needs.
//                 Can be nullptr if endgameSeq handles everything.
// endgameSeq    — Sequencer that runs for the duration of endgame.
//                 Can be nullptr if you only need endgamePrepFn.
//
// Use the leds::anim*() functions inside your callbacks —
// they handle brightness scaling automatically.
// ---------------------------------------------------------------
struct MatchProfile {
    const char* name;
    void (*idleFn)();
    void (*matchRedFn)();
    void (*matchBlueFn)();
    void (*endgamePrepFn)();  // nullable
    Sequencer*  endgameSeq;   // nullable
};

// ---------------------------------------------------------------
// ModeOverrides
//
// Optionally replace the built-in animations for the fixed modes.
// Leave any field nullptr to use the library default.
//
// Defaults:
//   scraperFn  — solid amber  (0xFFB030)
//   scoringFn  — green pulse
//   warningFn  — yellow flash
//   showcaseFn — internal rainbow → pulse → flow cycle
// ---------------------------------------------------------------
struct ModeOverrides {
    void (*scraperFn)()  = nullptr;
    void (*scoringFn)()  = nullptr;
    void (*warningFn)()  = nullptr;
    void (*showcaseFn)() = nullptr;
};

// ---------------------------------------------------------------
// Core API
// ---------------------------------------------------------------

// Call once in initialize() after ledManager.initialize().
//
//   manager      — your LedManager (you own the lifetime)
//   profiles     — pointer to your MatchProfile array
//   profileCount — number of profiles in the array
//   overrides    — optional, omit to use all library defaults
//
void init(hitlib::LedManager& manager,
          MatchProfile*        profiles,
          uint8_t              profileCount,
          ModeOverrides        overrides = {});

// Call every ~20ms from a background task or opcontrol loop.
void periodic();

// Set the base mode (shown when no overrides are active).
void setMode(Mode mode);
Mode getCurrentMode();

// Persistent override: stays active until explicitly disabled.
void setPersistent(Mode mode, bool enabled, Priority prio);

// Timed override: auto-expires after durationMs.
void playTimed(Mode mode, uint32_t durationMs, Priority prio);

// ---------------------------------------------------------------
// Animation functions
//
// Use these inside your MatchProfile callbacks and ModeOverride
// functions. They apply brightness scaling automatically.
//
// Do NOT call ledManager directly from profile code — these
// wrappers ensure brightness is consistent everywhere.
// ---------------------------------------------------------------
void animOff();
void animSetColor(uint32_t color);
void animPulse(uint32_t color, uint8_t runLength, uint8_t speed, uint32_t bgColor);
void animFlash(uint32_t color, uint8_t speed, uint32_t bgColor);
void animFlow(uint32_t color1, uint32_t color2, uint8_t speed);
void animRainbow(uint8_t speed);

// ---------------------------------------------------------------
// Convenience helpers
// ---------------------------------------------------------------
inline void setScraperOverride(bool enabled) {
    setPersistent(Mode::SCRAPER, enabled, Priority::SCRAPER);
}
inline void playScoringFx(uint32_t durationMs = 1500) {
    playTimed(Mode::SCORING, durationMs, Priority::SCORING);
}
inline void startEndgame() {
    setPersistent(Mode::ENDGAME, true, Priority::ENDGAME);
}
inline void stopEndgame() {
    setPersistent(Mode::ENDGAME, false, Priority::ENDGAME);
}

// ---------------------------------------------------------------
// Profile selection (index saved to SD)
// ---------------------------------------------------------------
const char* getMatchProfileName(uint8_t idx);
uint8_t     getMatchProfile();
void        setMatchProfile(uint8_t idx);

// ---------------------------------------------------------------
// Brightness (0–100%, applies to all anim*() calls)
// ---------------------------------------------------------------
uint8_t getBrightnessPct();
void    setBrightnessPct(uint8_t pct);

// ---------------------------------------------------------------
// Alliance (saved to SD, switches ALLIANCERED <-> ALLIANCEBLUE)
// ---------------------------------------------------------------
Alliance getAlliance();
void     setAlliance(Alliance a);

} // namespace leds