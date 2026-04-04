#include "hitlib/led_state.hpp"
#include "hitlib/led_sequencer.hpp"
#include "hitlib/led_driver.hpp"
#include "pros/motors.hpp"
#include "pros/rtos.hpp"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>

namespace leds {

// ---------------------------------------------------------------
// Manager pointer — set once in init(), never null after that
// ---------------------------------------------------------------
static hitlib::LedManager* mgr = nullptr;

// ---------------------------------------------------------------
// Base mode + override stack
// ---------------------------------------------------------------
static Mode baseMode    = Mode::OFF;
static Mode lastApplied = Mode::OFF;

struct OverrideEntry {
    Mode     mode;
    Priority prio;
    bool     persistent;
    uint32_t endTimeMs;
};

static std::vector<OverrideEntry> overrides;

// ---------------------------------------------------------------
// Brightness
// ---------------------------------------------------------------
static uint8_t brightnessPct = 100;

static inline uint32_t u24(uint32_t c) { return c & 0x00FFFFFFu; }

static uint32_t scaleColor(uint32_t c) {
    c = u24(c);
    uint32_t r = ((c >> 16) & 0xFF) * brightnessPct / 100;
    uint32_t g = ((c >>  8) & 0xFF) * brightnessPct / 100;
    uint32_t b = ((c >>  0) & 0xFF) * brightnessPct / 100;
    return (r << 16) | (g << 8) | b;
}

static inline void led_setColor(uint32_t c)                             { mgr->setColor(scaleColor(c)); }
static inline void led_off()                                            { mgr->off(); }
static inline void led_pulse(uint32_t c, int a, int b, uint32_t bg)    { mgr->pulse(scaleColor(c), a, b, scaleColor(bg)); }
static inline void led_flash(uint32_t c, int a, uint32_t bg)           { mgr->flash(scaleColor(c), a, scaleColor(bg)); }
static inline void led_flow(uint32_t c1, uint32_t c2, int a)           { mgr->flow(scaleColor(c1), scaleColor(c2), a); }

// ---------------------------------------------------------------
// Alliance
// ---------------------------------------------------------------
static uint32_t allianceColor = 0x00FF0000;
static bool     allianceIsRed = true;

static inline void setAllianceInternal(bool red) {
    allianceIsRed = red;
    allianceColor = red ? 0x00FF0000 : 0x000000FF;
}

// ---------------------------------------------------------------
// SD persistence
// ---------------------------------------------------------------
static constexpr const char* SETTINGS_PATH = "/usd/match_led_settings.bin";

struct SettingsV1 {
    char    magic[4];
    uint8_t version;
    uint8_t alliance;
    uint8_t profile;
};

static void saveSettingsToSD(uint8_t activeProfile);

static void loadSettingsFromSD(uint8_t& activeProfileOut) {
    std::FILE* f = std::fopen(SETTINGS_PATH, "rb");
    if (!f) return;

    SettingsV1 s{};
    size_t n = std::fread(&s, 1, sizeof(s), f);
    std::fclose(f);

    if (n != sizeof(s)) return;
    if (std::memcmp(s.magic, "MLD1", 4) != 0) return;
    if (s.version != 1) return;

    setAllianceInternal(s.alliance == 0);
    activeProfileOut = s.profile;
}

static void saveSettingsToSD(uint8_t activeProfile) {
    SettingsV1 s{{'M','L','D','1'}, 1, (uint8_t)(allianceIsRed ? 0 : 1), activeProfile};
    std::FILE* f = std::fopen(SETTINGS_PATH, "wb");
    if (!f) return;
    std::fwrite(&s, 1, sizeof(s), f);
    std::fclose(f);
}

// ---------------------------------------------------------------
// motorFollow state
// ---------------------------------------------------------------
struct ResolvedMotorFollowConfig {
    int8_t            motorPort;
    float             startDeg;
    float             endDeg;
    hitlib::FillStyle style;
    uint32_t          color1;
    uint32_t          color2;
    uint8_t           speed;
    uint8_t           runLength;
    uint32_t          bgColor;
    bool              inverse;
};

static ResolvedMotorFollowConfig motorFollowConfig {};

void configureMotorFollow(const MotorFollowConfig& cfg) {
    motorFollowConfig = {
        cfg.motorPort,
        (float)pros::Motor(cfg.motorPort).get_position(),
        cfg.endDeg,
        cfg.style,
        cfg.color1,
        cfg.color2,
        cfg.speed,
        cfg.runLength,
        cfg.bgColor,
        cfg.inverse
    };

    if (lastApplied == Mode::MOTOR_FOLLOW) lastApplied = Mode::OFF;
}

// ---------------------------------------------------------------
// Showcase helpers
// ---------------------------------------------------------------
static void showcaseRainbow() { mgr->rainbow(1); }
static void showcasePulse()   { led_pulse(0x00FF0000, 8, 1, 0x000000); }
static void showcaseFlow()    { led_flow(0x00AC00FF, 0x000000, 1); }

// ---------------------------------------------------------------
// Endgame helpers
// ---------------------------------------------------------------
static void solidWhite() { led_setColor(0x00FFFFFF); }
static void warnFlash()  { led_flash(0x00FFFF00, 3, 0x00000000); }

static uint8_t  cycleIdx = 0;
static uint8_t  cycleLen = 0;
static uint32_t cycleColors[16];

static void cycleStep() {
    if (cycleLen == 0) return;
    led_setColor(cycleColors[cycleIdx]);
    cycleIdx = (cycleIdx + 1) % cycleLen;
}

static void scoringFx() { led_pulse(0x0000FF00, 5, 1, 0x000000); }

// ---------------------------------------------------------------
// Match profiles
// ---------------------------------------------------------------
struct MatchProfile {
    const char* name;
    void (*idleFn)();
    void (*matchRedFn)();
    void (*matchBlueFn)();
    void (*endgamePrepFn)();
    Sequencer* endgameSeq;
};

// Profile 0: Classic
static void p0_idle()      { led_flow(0xff00dd, 0x000000, 1); }
static void p0_matchRed()  { setAllianceInternal(true);  led_pulse(0x00FF0000, 5, 1, 0x000000); }
static void p0_matchBlue() { setAllianceInternal(false); led_pulse(0x000000FF, 5, 1, 0x000000); }
static void p0_endgamePrep() {
    cycleIdx = 0; cycleLen = 10;
    cycleColors[0] = allianceColor; cycleColors[1] = 0x00FFFFFF;
    cycleColors[2] = allianceColor; cycleColors[3] = 0x00FFFFFF;
    cycleColors[4] = allianceColor; cycleColors[5] = 0x00FFFFFF;
    cycleColors[6] = allianceColor; cycleColors[7] = 0x0000FF00;
    cycleColors[8] = allianceColor; cycleColors[9] = 0x0000FF00;
}
static const Sequencer::Phase p0_endgamePhases[] = {
    {1500, warnFlash},  {8500, solidWhite},
    {1000, cycleStep},  {1000, cycleStep}, {1000, cycleStep}, {1000, cycleStep}, {1000, cycleStep},
    {1000, cycleStep},  {1000, cycleStep}, {1000, cycleStep}, {1000, cycleStep}, {1000, cycleStep},
};
static Sequencer p0_endgameSeq(p0_endgamePhases, 12);

// Profile 1: Modern
static void p1_idle()      { led_pulse(0xFF66CC, 8, 1, 0x000000); }
static void p1_matchRed()  { setAllianceInternal(true);  led_pulse(allianceColor, 14, 1, 0xFF6200); }
static void p1_matchBlue() { setAllianceInternal(false); led_pulse(allianceColor, 14, 1, 0x009DFF); }
static void p1_endgamePrep() {
    cycleIdx = 0; cycleLen = 5;
    cycleColors[0] = allianceColor; cycleColors[1] = 0x11ff00;
    cycleColors[2] = allianceColor; cycleColors[3] = 0x11ff00;
    cycleColors[4] = allianceColor;
}
static void p1_endSet() { led_setColor(0x11ff00); }
static const Sequencer::Phase p1_endgamePhases[] = {
    {15000, p1_endSet},
    {1000, cycleStep}, {1000, cycleStep}, {1000, cycleStep}, {1000, cycleStep}, {1000, cycleStep},
};
static Sequencer p1_endgameSeq(p1_endgamePhases, 6);

// Profile 2: Showy
static void p2_idle()      { led_flow(0x00AC00FF, 0x000000, 1); }
static void p2_matchRed()  { setAllianceInternal(true);  led_pulse(0x00FFFFFF, 4, 1, allianceColor); }
static void p2_matchBlue() { setAllianceInternal(false); led_pulse(0x00FFFFFF, 4, 1, allianceColor); }
static void p2_endgamePrep() {
    cycleIdx = 0; cycleLen = 8;
    cycleColors[0] = allianceColor; cycleColors[1] = 0x00FFFFFF;
    cycleColors[2] = 0x00FFFF00;   cycleColors[3] = 0x00FFFFFF;
    cycleColors[4] = 0x0000FF00;   cycleColors[5] = 0x00FFFFFF;
    cycleColors[6] = 0x00000000;   cycleColors[7] = 0x00FFFFFF;
}
static void p2_endPulse() { led_pulse(0x00FFFF00, 6, 1, 0x000000); }
static const Sequencer::Phase p2_endgamePhases[] = {
    {2000, p2_endPulse},
    {1000, cycleStep}, {1000, cycleStep}, {1000, cycleStep}, {1000, cycleStep},
    {1000, cycleStep}, {1000, cycleStep}, {1000, cycleStep}, {1000, cycleStep},
};
static Sequencer p2_endgameSeq(p2_endgamePhases, 9);

static MatchProfile profiles[] = {
    {"Classic", p0_idle, p0_matchRed, p0_matchBlue, p0_endgamePrep, &p0_endgameSeq},
    {"Modern",  p1_idle, p1_matchRed, p1_matchBlue, p1_endgamePrep, &p1_endgameSeq},
    {"Showy",   p2_idle, p2_matchRed, p2_matchBlue, p2_endgamePrep, &p2_endgameSeq},
};
static constexpr uint8_t PROFILE_COUNT = sizeof(profiles) / sizeof(profiles[0]);
static uint8_t activeProfile = 0;

// ---------------------------------------------------------------
// Showcase sequencer
// ---------------------------------------------------------------
static const Sequencer::Phase showcasePhases[] = {
    {5000, showcaseRainbow},
    {5000, showcasePulse},
    {5000, showcaseFlow}
};
static Sequencer showcaseSequencer(showcasePhases, 3);

static void stopAllSequencers() {
    showcaseSequencer.stop();
    for (uint8_t i = 0; i < PROFILE_COUNT; i++) profiles[i].endgameSeq->stop();
}

// ---------------------------------------------------------------
// applyMode
// ---------------------------------------------------------------
static void applyMode(Mode mode) {
    stopAllSequencers();
    MatchProfile& p = profiles[activeProfile];

    switch (mode) {
        case Mode::OFF:
            led_off();
            break;

        case Mode::IDLE:
            p.idleFn();
            break;

        case Mode::ALLIANCERED:
            p.matchRedFn();
            break;

        case Mode::ALLIANCEBLUE:
            p.matchBlueFn();
            break;

        case Mode::SCRAPER:
            led_setColor(0x00FFB030);
            break;

        case Mode::SCORING:
            scoringFx();
            break;

        case Mode::WARNING:
            warnFlash();
            break;

        case Mode::MOTOR_FOLLOW:
            mgr->motorFollow(
                motorFollowConfig.motorPort,
                motorFollowConfig.startDeg,
                motorFollowConfig.endDeg,
                motorFollowConfig.style,
                motorFollowConfig.color1,
                motorFollowConfig.color2,
                motorFollowConfig.speed,
                motorFollowConfig.runLength,
                motorFollowConfig.bgColor,
                motorFollowConfig.inverse
            );
            break;

        case Mode::ENDGAME:
            p.endgamePrepFn();
            p.endgameSeq->start();
            break;

        case Mode::SHOWCASE:
            showcaseSequencer.start();
            break;
    }
}

// ---------------------------------------------------------------
// Priority / override logic
// ---------------------------------------------------------------
static bool hasPersistent(Mode mode) {
    for (const auto& o : overrides)
        if (o.persistent && o.mode == mode) return true;
    return false;
}

static bool isEndgameActive() { return hasPersistent(Mode::ENDGAME); }

static Mode computeEffectiveMode(uint32_t now) {
    Mode best         = baseMode;
    Priority bestPrio = Priority::BASE;

    for (const auto& o : overrides) {
        bool active = o.persistent || (now < o.endTimeMs);
        if (!active) continue;
        if (static_cast<uint8_t>(o.prio) > static_cast<uint8_t>(bestPrio)) {
            bestPrio = o.prio;
            best     = o.mode;
        }
    }
    return best;
}

static void pruneExpired(uint32_t now) {
    overrides.erase(
        std::remove_if(overrides.begin(), overrides.end(),
            [&](const OverrideEntry& o) {
                return (!o.persistent) && (now >= o.endTimeMs);
            }),
        overrides.end()
    );
}

// ---------------------------------------------------------------
// Public API
// ---------------------------------------------------------------
void init(hitlib::LedManager& manager) {
    mgr = &manager;

    overrides.clear();
    lastApplied = Mode::OFF;

    uint8_t loadedProfile = activeProfile;
    loadSettingsFromSD(loadedProfile);
    if (loadedProfile < PROFILE_COUNT) activeProfile = loadedProfile;

    baseMode = allianceIsRed ? Mode::ALLIANCERED : Mode::ALLIANCEBLUE;
    applyMode(baseMode);
    lastApplied = baseMode;
}

Mode getCurrentMode() { return baseMode; }

void setMode(Mode mode) { baseMode = mode; }

void setPersistent(Mode mode, bool enabled, Priority prio) {
    if (enabled) {
        for (auto& o : overrides) {
            if (o.persistent && o.mode == mode) { o.prio = prio; return; }
        }
        overrides.push_back(OverrideEntry{mode, prio, true, 0});
        return;
    }
    overrides.erase(
        std::remove_if(overrides.begin(), overrides.end(),
            [&](const OverrideEntry& o) { return o.persistent && o.mode == mode; }),
        overrides.end()
    );
}

void playTimed(Mode mode, uint32_t durationMs, Priority prio) {
    if (mode == Mode::SCORING && isEndgameActive()) return;

    uint32_t now  = pros::millis();
    uint32_t endT = now + durationMs;

    for (auto& o : overrides) {
        if (!o.persistent && o.mode == mode) {
            o.endTimeMs = (endT > o.endTimeMs) ? endT : o.endTimeMs;
            if (static_cast<uint8_t>(prio) > static_cast<uint8_t>(o.prio)) o.prio = prio;
            return;
        }
    }
    overrides.push_back(OverrideEntry{mode, prio, false, endT});
}

void periodic() {
    uint32_t now = pros::millis();

    showcaseSequencer.update();
    for (uint8_t i = 0; i < PROFILE_COUNT; i++) profiles[i].endgameSeq->update();

    pruneExpired(now);

    Mode effective = computeEffectiveMode(now);
    if (effective != lastApplied) {
        applyMode(effective);
        lastApplied = effective;
    }
}

const char* getMatchProfileName(uint8_t idx) {
    if (idx >= PROFILE_COUNT) return "";
    return profiles[idx].name;
}

uint8_t getMatchProfile() { return activeProfile; }

void setMatchProfile(uint8_t idx) {
    if (idx >= PROFILE_COUNT || activeProfile == idx) return;
    activeProfile = idx;
    saveSettingsToSD(activeProfile);
    lastApplied = Mode::OFF;
}

uint8_t getBrightnessPct() { return brightnessPct; }

void setBrightnessPct(uint8_t pct) {
    if (pct > 100) pct = 100;
    brightnessPct = pct;
    lastApplied = Mode::OFF;
}

Alliance getAlliance() { return allianceIsRed ? Alliance::RED : Alliance::BLUE; }

void setAlliance(Alliance a) {
    setAllianceInternal(a == Alliance::RED);
    if (baseMode == Mode::ALLIANCERED || baseMode == Mode::ALLIANCEBLUE)
        baseMode = allianceIsRed ? Mode::ALLIANCERED : Mode::ALLIANCEBLUE;
    saveSettingsToSD(activeProfile);
    lastApplied = Mode::OFF;
}

} // namespace leds