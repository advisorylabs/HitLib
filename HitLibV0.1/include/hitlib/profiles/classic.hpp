#pragma once
// Ready-made profiles: Classic, Modern, Showy.
// Profile functions take LedStrand& — each strand is driven independently.
//
// Mode indices per profile:
//   0 = Idle
//   1 = Alliance Red
//   2 = Alliance Blue
//   3 = Scoring
//   4 = Endgame
//
// Usage:
//   strand.attachProfile(&hitlib::profiles::classic);
//   strand.activateMode(0);   // idle
//   strand.activateModeTimed(3, 1500); // scoring flash

#include "hitlib/led_strand.hpp"
#include "hitlib/led_profile.hpp"
#include "hitlib/led_sequencer.hpp"

namespace hitlib::profiles {

// ================================================================
// Classic
// Idle: flowing magenta. Match: color pulse. Endgame: warn → white → cycle.
// ================================================================

inline void classicIdle        (LedStrand& s) { s.flow(0xFF00DD, 0x000000, 1); }
inline void classicRed         (LedStrand& s) { s.pulse(0xFF0000, 5, 1); }
inline void classicBlue        (LedStrand& s) { s.pulse(0x0000FF, 5, 1); }
inline void classicScore       (LedStrand& s) { s.pulse(0x00FF00, 5, 1); }
inline void classicMatchloader (LedStrand& s) { s.pulse(0xEED202, 5, 1); }
inline void showoff            (LedStrand& s) { s.rainbow(1); }

inline void classicEgWarn  (LedStrand& s) { s.flash(0xFFFF00, 3); }
inline void classicEgWhite (LedStrand& s) { s.setColor(0xFFFFFF); }
inline void classicEgCycle (LedStrand& s) { s.pulse(0xFF0000, 5, 1); }

inline const Sequencer::Phase classicEgPhases[] = {
    {1500, classicEgWarn},
    {8500, classicEgWhite},
    {2000, classicEgCycle},
};
inline Sequencer classicEndgameSeq(classicEgPhases, 3);

inline void classicEgActivate(LedStrand& s) { classicEndgameSeq.start(s); }
inline void classicEgTick    (LedStrand& s) { classicEndgameSeq.update(s); }

inline const ProfileMode classicModes[] = {
    {"Showoff",      15,  showoff,            nullptr},         // 0
    {"Idle",         10,  classicIdle,        nullptr},         // 1
    {"Red",          20,  classicRed,         nullptr},         // 2
    {"Blue",         20,  classicBlue,        nullptr},         // 3
    {"Scoring",      70,  classicScore,       nullptr},         // 4
    {"Matchloading", 90,  classicMatchloader, nullptr},         // 5
    {"Endgame",      100, classicEgActivate,  classicEgTick},   // 6
};
inline const Profile classic = {"Classic", classicModes, 7};

// ================================================================
// Modern
// Idle: pink pulse. Match: alliance pulse with accent bg. Endgame: solid green → cycle.
// ================================================================

inline void modernIdle        (LedStrand& s) { s.pulse(0xFF66CC, 8, 1); }
inline void modernRed         (LedStrand& s) { s.pulse(0xFF0000, 14, 1, 0xFF6200); }
inline void modernBlue        (LedStrand& s) { s.pulse(0x0000FF, 14, 1, 0x009DFF); }
inline void modernScore       (LedStrand& s) { s.flash(0x11FF00, 2); }
inline void modernMatchloader (LedStrand& s) { s.pulse(0xEED202, 5, 1); }

inline void modernEgGreen (LedStrand& s) { s.setColor(0x11FF00); }
inline void modernEgPulse (LedStrand& s) { s.pulse(0x11FF00, 8, 2); }

inline const Sequencer::Phase modernEgPhases[] = {
    {15000, modernEgGreen},
    {4000,  modernEgPulse},
};
inline Sequencer modernEndgameSeq(modernEgPhases, 2);

inline void modernEgActivate(LedStrand& s) { modernEndgameSeq.start(s); }
inline void modernEgTick    (LedStrand& s) { modernEndgameSeq.update(s); }

inline const ProfileMode modernModes[] = {
    {"Showoff",  15,  showoff,           nullptr}, 
    {"Idle",     10,  modernIdle,        nullptr},
    {"Red",      20,  modernRed,         nullptr},
    {"Blue",     20,  modernBlue,        nullptr},
    {"Scoring",  70,  modernScore,       nullptr},
    {"Matchloading",  90,  modernMatchloader, nullptr},
    {"Endgame",  100, modernEgActivate,  modernEgTick},
};
inline const Profile modern = {"Modern", modernModes, 7};

// ================================================================
// Showy
// Idle: purple flow. Match: white pulse on alliance bg. Endgame: yellow pulse → rainbow.
// ================================================================

inline void showyIdle  (LedStrand& s) { s.flow(0xAC00FF, 0x000000, 1); }
inline void showyRed   (LedStrand& s) { s.pulse(0xFFFFFF, 4, 1, 0xFF0000); }
inline void showyBlue  (LedStrand& s) { s.pulse(0xFFFFFF, 4, 1, 0x0000FF); }
inline void showyScore (LedStrand& s) { s.pulse(0x00FF88, 6, 2); }

inline void showyEgPulse   (LedStrand& s) { s.pulse(0xFFFF00, 6, 1); }
inline void showyEgRainbow (LedStrand& s) { s.rainbow(2); }

inline const Sequencer::Phase showyEgPhases[] = {
    {2000, showyEgPulse},
    {8000, showyEgRainbow},
};
inline Sequencer showyEndgameSeq(showyEgPhases, 2);

inline void showyEgActivate(LedStrand& s) { showyEndgameSeq.start(s); }
inline void showyEgTick    (LedStrand& s) { showyEndgameSeq.update(s); }

inline const ProfileMode showyModes[] = {
    {"Idle",     10,  showyIdle,       nullptr},
    {"Red",      20,  showyRed,        nullptr},
    {"Blue",     20,  showyBlue,       nullptr},
    {"Scoring",  90,  showyScore,      nullptr},
    {"Endgame",  100, showyEgActivate, showyEgTick},
};
inline const Profile showy = {"Showy", showyModes, 5};

} // namespace hitlib::profiles