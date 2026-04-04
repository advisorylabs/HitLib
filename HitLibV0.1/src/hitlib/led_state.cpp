#include "hitlib/led_state.hpp"
#include "hitlib/led_sequencer.hpp"
#include "hitlib/led_driver.hpp"
#include "pros/rtos.hpp"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>

namespace leds {

// ---------------------------------------------------------------
// Manager + registered profiles
// ---------------------------------------------------------------
static hitlib::LedManager* mgr          = nullptr;
static MatchProfile*       userProfiles = nullptr;
static uint8_t             profileCount = 0;
static uint8_t             activeProfile = 0;
static ModeOverrides       modeOverrides {};

// ---------------------------------------------------------------
// Mode state
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

// ---------------------------------------------------------------
// Public animation wrappers (brightness-scaled)
// These are what profile code should call.
// ---------------------------------------------------------------
void animOff()                                                              { mgr->off(); }
void animSetColor(uint32_t c)                                               { mgr->setColor(scaleColor(c)); }
void animPulse(uint32_t c, uint8_t run, uint8_t spd, uint32_t bg)          { mgr->pulse(scaleColor(c), run, spd, scaleColor(bg)); }
void animFlash(uint32_t c, uint8_t spd, uint32_t bg)                       { mgr->flash(scaleColor(c), spd, scaleColor(bg)); }
void animFlow(uint32_t c1, uint32_t c2, uint8_t spd)                       { mgr->flow(scaleColor(c1), scaleColor(c2), spd); }
void animRainbow(uint8_t spd)                                               { mgr->rainbow(spd); }

// ---------------------------------------------------------------
// Alliance
// ---------------------------------------------------------------
static bool allianceIsRed = true;

static inline void setAllianceInternal(bool red) {
    allianceIsRed = red;
}

// ---------------------------------------------------------------
// SD persistence (alliance + active profile index)
// ---------------------------------------------------------------
static constexpr const char* SETTINGS_PATH = "/usd/match_led_settings.bin";

struct SettingsV1 {
    char    magic[4];
    uint8_t version;
    uint8_t alliance;
    uint8_t profile;
};

static void saveSettingsToSD(uint8_t profile) {
    SettingsV1 s{{'M','L','D','1'}, 1, (uint8_t)(allianceIsRed ? 0 : 1), profile};
    std::FILE* f = std::fopen(SETTINGS_PATH, "wb");
    if (!f) return;
    std::fwrite(&s, 1, sizeof(s), f);
    std::fclose(f);
}

static void loadSettingsFromSD(uint8_t& profileOut) {
    std::FILE* f = std::fopen(SETTINGS_PATH, "rb");
    if (!f) return;

    SettingsV1 s{};
    size_t n = std::fread(&s, 1, sizeof(s), f);
    std::fclose(f);

    if (n != sizeof(s)) return;
    if (std::memcmp(s.magic, "MLD1", 4) != 0) return;
    if (s.version != 1) return;

    setAllianceInternal(s.alliance == 0);
    profileOut = s.profile;
}

// ---------------------------------------------------------------
// Built-in default mode animations
// Used when ModeOverrides fields are nullptr.
// ---------------------------------------------------------------
static void defaultScraper()  { animSetColor(0xFFB030); }
static void defaultScoring()  { animPulse(0x00FF00, 5, 1, 0x000000); }
static void defaultWarning()  { animFlash(0xFFFF00, 3, 0x000000); }

// Built-in showcase cycles through rainbow → pulse → flow
static void showcaseRainbow() { animRainbow(1); }
static void showcasePulse()   { animPulse(0xFF0000, 8, 1, 0x000000); }
static void showcaseFlow()    { animFlow(0xAC00FF, 0x000000, 1); }

static const Sequencer::Phase defaultShowcasePhases[] = {
    {5000, showcaseRainbow},
    {5000, showcasePulse},
    {5000, showcaseFlow},
};
static Sequencer defaultShowcaseSeq(defaultShowcasePhases, 3);

// ---------------------------------------------------------------
// Sequencer management
// ---------------------------------------------------------------
static void stopAllSequencers() {
    defaultShowcaseSeq.stop();
    if (!userProfiles) return;
    for (uint8_t i = 0; i < profileCount; i++) {
        if (userProfiles[i].endgameSeq)
            userProfiles[i].endgameSeq->stop();
    }
}

// ---------------------------------------------------------------
// applyMode
// ---------------------------------------------------------------
static void applyMode(Mode mode) {
    stopAllSequencers();

    // Guard against unregistered profiles
    if (!userProfiles || profileCount == 0) {
        animOff();
        return;
    }

    MatchProfile& p = userProfiles[activeProfile];

    switch (mode) {
        case Mode::OFF:
            animOff();
            break;

        case Mode::IDLE:
            if (p.idleFn) p.idleFn();
            break;

        case Mode::ALLIANCERED:
            if (p.matchRedFn) p.matchRedFn();
            break;

        case Mode::ALLIANCEBLUE:
            if (p.matchBlueFn) p.matchBlueFn();
            break;

        case Mode::SCRAPER:
            if (modeOverrides.scraperFn) modeOverrides.scraperFn();
            else defaultScraper();
            break;

        case Mode::SCORING:
            if (modeOverrides.scoringFn) modeOverrides.scoringFn();
            else defaultScoring();
            break;

        case Mode::WARNING:
            if (modeOverrides.warningFn) modeOverrides.warningFn();
            else defaultWarning();
            break;

        case Mode::ENDGAME:
            if (p.endgamePrepFn) p.endgamePrepFn();
            if (p.endgameSeq)    p.endgameSeq->start();
            break;

        case Mode::SHOWCASE:
            if (modeOverrides.showcaseFn) modeOverrides.showcaseFn();
            else defaultShowcaseSeq.start();
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
void init(hitlib::LedManager& manager,
          MatchProfile*        profiles,
          uint8_t              count,
          ModeOverrides        overrides_in)
{
    mgr           = &manager;
    userProfiles  = profiles;
    profileCount  = count;
    modeOverrides = overrides_in;

    overrides.clear();
    lastApplied = Mode::OFF;

    uint8_t loaded = 0;
    loadSettingsFromSD(loaded);
    if (loaded < profileCount) activeProfile = loaded;

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

    defaultShowcaseSeq.update();
    if (userProfiles) {
        for (uint8_t i = 0; i < profileCount; i++) {
            if (userProfiles[i].endgameSeq)
                userProfiles[i].endgameSeq->update();
        }
    }

    pruneExpired(now);

    Mode effective = computeEffectiveMode(now);
    if (effective != lastApplied) {
        applyMode(effective);
        lastApplied = effective;
    }
}

const char* getMatchProfileName(uint8_t idx) {
    if (!userProfiles || idx >= profileCount) return "";
    return userProfiles[idx].name;
}

uint8_t getMatchProfile() { return activeProfile; }

void setMatchProfile(uint8_t idx) {
    if (!userProfiles || idx >= profileCount || activeProfile == idx) return;
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