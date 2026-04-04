#pragma once
#include "hitlib/led_driver.hpp"
#include <cstdint>

namespace leds {

enum class Mode {
    OFF,
    IDLE,
    ALLIANCERED,
    ALLIANCEBLUE,
    SCRAPER,
    SCORING,
    WARNING,
    MOTOR_FOLLOW,
    ENDGAME,
    SHOWCASE
};

// Priority: higher number wins
enum class Priority : uint8_t {
    BASE         = 0,
    ALLIANCE     = 10,
    MOTOR_FOLLOW = 20,
    SCRAPER      = 40,
    SCORING      = 90,
    ENDGAME      = 100
};

enum class Alliance : uint8_t { RED = 0, BLUE = 1 };

// ---------------------------------------------------------------
// MotorFollowConfig
// startDeg is intentionally absent — captured automatically from
// the motor's current position when startMotorFollow() is called.
// ---------------------------------------------------------------
struct MotorFollowConfig {
    int8_t            motorPort;
    float             endDeg;
    hitlib::FillStyle style;
    uint32_t          color1;
    uint32_t          color2     = 0x000000;
    uint8_t           speed      = 1;
    uint8_t           runLength  = 3;
    uint32_t          bgColor    = 0x000000;
    bool              inverse    = false;
};

// ---------------------------------------------------------------
// Core API
// ---------------------------------------------------------------

// manager is the LedManager the user constructed and initialized.
// The library holds a pointer to it — user owns the lifetime.
void init(hitlib::LedManager& manager);
void periodic();

void setMode(Mode mode);
Mode getCurrentMode();

void setPersistent(Mode mode, bool enabled, Priority prio);
void playTimed(Mode mode, uint32_t durationMs, Priority prio);

// ---------------------------------------------------------------
// Motor follow
// ---------------------------------------------------------------
void configureMotorFollow(const MotorFollowConfig& config);

// ---------------------------------------------------------------
// Helpers
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

inline void startMotorFollow(const MotorFollowConfig& config) {
    configureMotorFollow(config);
    setPersistent(Mode::MOTOR_FOLLOW, true, Priority::MOTOR_FOLLOW);
}
inline void stopMotorFollow() {
    setPersistent(Mode::MOTOR_FOLLOW, false, Priority::MOTOR_FOLLOW);
}

// ---------------------------------------------------------------
// Profiles
// ---------------------------------------------------------------
const char* getMatchProfileName(uint8_t idx);
uint8_t     getMatchProfile();
void        setMatchProfile(uint8_t idx);

// ---------------------------------------------------------------
// Brightness (0–100%)
// ---------------------------------------------------------------
uint8_t getBrightnessPct();
void    setBrightnessPct(uint8_t pct);

// ---------------------------------------------------------------
// Alliance (saved to SD)
// ---------------------------------------------------------------
Alliance getAlliance();
void     setAlliance(Alliance a);

} // namespace leds