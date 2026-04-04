#pragma once
//
// hitlib/profiles/classic.hpp
//
// Three ready-made match profiles: Classic, Modern, Showy.
// Include this if you want a quick starting point, or use it
// as a reference for writing your own profiles.
//
// USAGE:
//   #include "hitlib/profiles/classic.hpp"
//
//   // Pass one or more profiles to leds::init()
//   leds::MatchProfile myProfiles[] = {
//       leds::profiles::classic,
//       leds::profiles::modern,
//       leds::profiles::showy,
//   };
//   leds::init(ledManager, myProfiles, 3);
//
// NOTE: These profiles call leds::anim*() functions, so
//       leds::init() must have been called before any profile
//       function runs (which is always true since the library
//       calls them internally).
//

#include "hitlib/led_state.hpp"
#include "hitlib/led_sequencer.hpp"

namespace leds::profiles {

// ---------------------------------------------------------------
// Shared endgame utilities used by all three profiles.
// You can use these in your own profiles too.
// ---------------------------------------------------------------

inline void solidWhite() { leds::animSetColor(0xFFFFFF); }
inline void warnFlash()  { leds::animFlash(0xFFFF00, 3, 0x000000); }

// A simple color-cycle helper.
// Fill cycleColors[] and cycleLen in your endgamePrepFn,
// then use cycleStep as a phase function in your Sequencer.
inline uint32_t cycleColors[16] = {};
inline uint8_t  cycleLen        = 0;
inline uint8_t  cycleIdx        = 0;

inline void cycleStep() {
    if (cycleLen == 0) return;
    leds::animSetColor(cycleColors[cycleIdx]);
    cycleIdx = (cycleIdx + 1) % cycleLen;
}

// ---------------------------------------------------------------
// Classic
// Idle: flowing magenta. Match: color pulse. Endgame: warn → white → cycle.
// ---------------------------------------------------------------
inline void classicIdle()      { leds::animFlow(0xFF00DD, 0x000000, 1); }
inline void classicMatchRed()  { leds::animPulse(0xFF0000, 5, 1, 0x000000); }
inline void classicMatchBlue() { leds::animPulse(0x0000FF, 5, 1, 0x000000); }

inline void classicEndgamePrep() {
    uint32_t alliance = (leds::getAlliance() == leds::Alliance::RED) ? 0xFF0000u : 0x0000FFu;
    cycleIdx = 0; cycleLen = 6;
    cycleColors[0] = alliance;   cycleColors[1] = 0xFFFFFF;
    cycleColors[2] = alliance;   cycleColors[3] = 0x00FF00;
    cycleColors[4] = alliance;   cycleColors[5] = 0x00FF00;
}

inline const Sequencer::Phase classicEndgamePhases[] = {
    {1500, warnFlash},
    {8500, solidWhite},
    {1000, cycleStep}, {1000, cycleStep}, {1000, cycleStep},
    {1000, cycleStep}, {1000, cycleStep}, {1000, cycleStep},
};
inline Sequencer classicEndgameSeq(classicEndgamePhases, 8);

inline leds::MatchProfile classic = {
    "Classic",
    classicIdle,
    classicMatchRed,
    classicMatchBlue,
    classicEndgamePrep,
    &classicEndgameSeq,
};

// ---------------------------------------------------------------
// Modern
// Idle: pink pulse. Match: alliance pulse with accent bg. Endgame: solid green → cycle.
// ---------------------------------------------------------------
inline void modernIdle()      { leds::animPulse(0xFF66CC, 8, 1, 0x000000); }
inline void modernMatchRed()  { leds::animPulse(0xFF0000, 14, 1, 0xFF6200); }
inline void modernMatchBlue() { leds::animPulse(0x0000FF, 14, 1, 0x009DFF); }

inline void modernEndgamePrep() {
    uint32_t alliance = (leds::getAlliance() == leds::Alliance::RED) ? 0xFF0000u : 0x0000FFu;
    cycleIdx = 0; cycleLen = 4;
    cycleColors[0] = alliance; cycleColors[1] = 0x11FF00;
    cycleColors[2] = alliance; cycleColors[3] = 0x11FF00;
}

inline void modernEndSet() { leds::animSetColor(0x11FF00); }

inline const Sequencer::Phase modernEndgamePhases[] = {
    {15000, modernEndSet},
    {1000, cycleStep}, {1000, cycleStep}, {1000, cycleStep}, {1000, cycleStep},
};
inline Sequencer modernEndgameSeq(modernEndgamePhases, 5);

inline leds::MatchProfile modern = {
    "Modern",
    modernIdle,
    modernMatchRed,
    modernMatchBlue,
    modernEndgamePrep,
    &modernEndgameSeq,
};

// ---------------------------------------------------------------
// Showy
// Idle: purple flow. Match: white pulse on alliance bg. Endgame: yellow pulse → full cycle.
// ---------------------------------------------------------------
inline void showyIdle()      { leds::animFlow(0xAC00FF, 0x000000, 1); }
inline void showyMatchRed()  { leds::animPulse(0xFFFFFF, 4, 1, 0xFF0000); }
inline void showyMatchBlue() { leds::animPulse(0xFFFFFF, 4, 1, 0x0000FF); }

inline void showyEndgamePrep() {
    uint32_t alliance = (leds::getAlliance() == leds::Alliance::RED) ? 0xFF0000u : 0x0000FFu;
    cycleIdx = 0; cycleLen = 8;
    cycleColors[0] = alliance;  cycleColors[1] = 0xFFFFFF;
    cycleColors[2] = 0xFFFF00;  cycleColors[3] = 0xFFFFFF;
    cycleColors[4] = 0x00FF00;  cycleColors[5] = 0xFFFFFF;
    cycleColors[6] = 0x000000;  cycleColors[7] = 0xFFFFFF;
}

inline void showyEndPulse() { leds::animPulse(0xFFFF00, 6, 1, 0x000000); }

inline const Sequencer::Phase showyEndgamePhases[] = {
    {2000, showyEndPulse},
    {1000, cycleStep}, {1000, cycleStep}, {1000, cycleStep}, {1000, cycleStep},
    {1000, cycleStep}, {1000, cycleStep}, {1000, cycleStep}, {1000, cycleStep},
};
inline Sequencer showyEndgameSeq(showyEndgamePhases, 9);

inline leds::MatchProfile showy = {
    "Showy",
    showyIdle,
    showyMatchRed,
    showyMatchBlue,
    showyEndgamePrep,
    &showyEndgameSeq,
};

} // namespace leds::profiles