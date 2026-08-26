"""Turns a track's per-frame loudness into the table a strand fills to.

This is the middle of the pipeline, and the only part that decides how the
strip *looks*. Either analyser - audio.py for a real audio file, midi.py for a
MIDI - produces the same thing: per-frame loudness in dB, split into frequency
bands. Everything after that happens here, identically for both.

### Why dB, and why auto-contrast

Raw amplitude is the obvious thing to fill a meter with and it looks dead. A
mastered pop track lives in the top few dB of its range, so peak-normalised
amplitude leaves the meter hovering at a tenth of the strip with occasional
twitches. Loudness is logarithmic, so the fix is to work in dB and then map the
song's *own* distribution onto the strip:

  * the useful dB span is found from percentiles, not from the peak, so one
    stray transient can't squash everything else;
  * a curve is fitted so this song's median frame lands mid-strip, which is
    what makes the meter swing around the middle instead of hugging an end;
  * an optional rolling ceiling (AGC) normalises against the last few seconds
    rather than the whole song, so a quiet intro still uses the strip and a
    loud chorus still has somewhere to go.

### Level vs Beat

LEVEL follows how loud the band is. BEAT follows how fast it is *getting*
louder (the positive change in dB, i.e. spectral flux in one band), which locks
the strip to the kick instead of to the mix's overall loudness. BEAT is the
default because it is what reads as "in time with the music" on a strip.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

#: Analysis grid. Fine enough to place an onset within a hair of the beat, and
#: coarse enough that a five-minute track is a few tens of thousands of frames.
ANALYSIS_FRAME_MS = 10

#: Quietest loudness the analysis distinguishes. Everything below is silence.
DB_FLOOR = -80.0

#: Bands every analyser produces, in the order the UI offers them.
BAND_BASS = "bass"
BAND_MID = "mid"
BAND_TREBLE = "treble"
BAND_FULL = "full"
BAND_NAMES = (BAND_BASS, BAND_MID, BAND_TREBLE, BAND_FULL)

BAND_LABELS: dict[str, str] = {
    BAND_BASS: "Bass",
    BAND_MID: "Mid",
    BAND_TREBLE: "Treble",
    BAND_FULL: "Full mix",
}

BAND_HELP: dict[str, str] = {
    BAND_BASS: "Kick and bass - pumps on the beat. The one most strips want.",
    BAND_MID: "Vocals, snare, guitars - follows the song's body.",
    BAND_TREBLE: "Hats and cymbals - sparse and sparkly.",
    BAND_FULL: "Everything at once - steadiest, least rhythmic.",
}


class EnvelopeMode(str, Enum):
    """What the meter follows."""

    BEAT = "beat"
    LEVEL = "level"


ENVELOPE_MODE_LABELS: dict[EnvelopeMode, str] = {
    EnvelopeMode.BEAT: "Beat",
    EnvelopeMode.LEVEL: "Level",
}

ENVELOPE_MODE_HELP: dict[EnvelopeMode, str] = {
    EnvelopeMode.BEAT: "Beat - punches when the band gets louder, so the strip moves with the rhythm.",
    EnvelopeMode.LEVEL: "Level - follows how loud the band is, so the strip breathes with the mix.",
}


@dataclass
class EnvelopeSettings:
    """How a band's loudness becomes a fill. Shared by every band and strand -
    only the band choice is per-strand."""

    mode: EnvelopeMode = EnvelopeMode.BEAT
    #: Smoothing applied to loudness before anything else. Stops the meter
    #: chasing sample-level noise, which reads as flicker on a real strip.
    smooth_ms: int = 40
    attack_ms: int = 20
    release_ms: int = 250
    #: 0-100. How far to normalise against a rolling few seconds instead of the
    #: whole track, so quiet and loud sections both use the strip. Mostly a
    #: LEVEL-mode control: BEAT works on differences of dB, which already cancel
    #: out how loud the passage happens to be.
    auto_gain: int = 50
    #: 50-200. Bends the auto-fitted curve: below 100 lifts the quiet frames,
    #: above 100 pushes them down for a punchier, darker look.
    contrast: int = 100
    #: Widest dB span LEVEL mode will stretch across the strip.
    range_db: int = 34
    #: Milliseconds per exported sample - the size of the table on the brain.
    frame_ms: int = 25


@dataclass
class TrackAnalysis:
    """Per-frame loudness per band, quantised to a byte over DB_FLOOR..0 dB.

    Source-independent: audio and MIDI both reduce to this, so everything
    downstream (and every test of it) is written once. Quantising to a byte is
    what makes it small enough to save inside a design, which is what lets the
    bake settings stay live after the source file has gone.
    """

    #: band name -> one quantised dB value per ANALYSIS_FRAME_MS frame.
    bands: dict[str, list[int]] = field(default_factory=dict)
    frame_ms: int = ANALYSIS_FRAME_MS
    #: Where the loudness came from, for the Song bar to show.
    name: str = ""
    source_path: str = ""

    @property
    def frame_count(self) -> int:
        for values in self.bands.values():
            return len(values)
        return 0

    @property
    def duration_ms(self) -> int:
        return self.frame_count * self.frame_ms

    @property
    def loaded(self) -> bool:
        return self.frame_count > 0


def quantise_db(db_values: list[float]) -> list[int]:
    """dB (DB_FLOOR..0) -> 0..255. About a third of a dB per step."""
    scale = 255.0 / -DB_FLOOR
    return [max(0, min(255, round((v - DB_FLOOR) * scale))) for v in db_values]


def dequantise_db(values: list[int]) -> list[float]:
    scale = -DB_FLOOR / 255.0
    return [v * scale + DB_FLOOR for v in values]


def power_to_db(power: float) -> float:
    return max(DB_FLOOR, 10.0 * math.log10(power)) if power > 1e-12 else DB_FLOOR


def analysis_from_power(bands: dict[str, list[float]], **kwargs) -> TrackAnalysis:
    """Build a TrackAnalysis from raw per-frame power, which is what both
    analysers naturally produce.

    Power is scaled so the loudest frame of the loudest band sits at 0 dB. The
    two analysers otherwise work on incompatible scales - audio samples are
    bounded at 1.0, MIDI velocities square up into the thousands - and either
    would fall outside the quantisation window. Scaling jointly rather than per
    band keeps the bands' relative loudness intact, which is what makes picking
    a band a meaningful choice.
    """
    peak = max((max(values, default=0.0) for values in bands.values()), default=0.0)
    scale = 1.0 / peak if peak > 0 else 0.0
    return TrackAnalysis(
        bands={
            name: quantise_db([power_to_db(p * scale) for p in values])
            for name, values in bands.items()
        },
        **kwargs,
    )


# ============================================================================
# Shaping
# ============================================================================


def bake(analysis: TrackAnalysis, band: str, settings: EnvelopeSettings) -> list[int]:
    """Render one band of `analysis` into the 0-255 table a strand plays.

    Cheap enough to re-run on every slider drag: it only touches the frame
    arrays, never the audio.
    """
    quantised = analysis.bands.get(band)
    if not quantised:
        return []

    db = _smooth(dequantise_db(quantised), settings.smooth_ms, analysis.frame_ms)
    if settings.mode == EnvelopeMode.BEAT:
        # No auto-gain here, and none needed: flux is a difference of dB, so
        # scaling a passage shifts both terms equally and cancels.
        level = _flux(db)
    else:
        if settings.auto_gain > 0:
            db = _auto_gain(db, settings.auto_gain / 100.0, analysis.frame_ms)
        level = _span(db, settings.range_db)

    level = _fit_curve(level, settings.contrast / 100.0)
    level = _ballistics(level, settings.attack_ms, settings.release_ms, analysis.frame_ms)
    return _resample(level, analysis.frame_ms, settings.frame_ms)


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    i = int(q / 100.0 * (len(ordered) - 1))
    return ordered[max(0, min(len(ordered) - 1, i))]


def _smooth(values: list[float], window_ms: int, frame_ms: int) -> list[float]:
    """Trailing moving average. Trailing rather than centred on purpose: a
    centred window would let the meter start rising before the beat lands."""
    n = max(1, round(window_ms / max(1, frame_ms)))
    if n < 2:
        return list(values)
    out: list[float] = []
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= n:
            running -= values[i - n]
        out.append(running / min(i + 1, n))
    return out


def _flux(db: list[float]) -> list[float]:
    """Positive change in loudness, normalised to its own 99th percentile.

    Only rises count: a band getting louder is an onset, a band getting quieter
    is just the last one decaying, and the release ballistics already draw that.
    """
    rises = [0.0] + [max(0.0, db[i] - db[i - 1]) for i in range(1, len(db))]
    # Scaled against the frames that actually rose, not against every frame. A
    # percentile over the whole track is zero whenever onsets are sparse - true
    # of any MIDI, and of quiet passages in audio - which would flatten the
    # envelope to nothing exactly where the onsets matter most.
    positive = [v for v in rises if v > 0.0]
    top = _percentile(positive, 98.0) if positive else 0.0
    if top <= 0:
        return [0.0] * len(rises)
    return [min(1.0, v / top) for v in rises]


def _span(db: list[float], range_db: int) -> list[float]:
    """Map the band's own useful dB span onto 0..1.

    The ceiling is a high percentile rather than the maximum, so one transient
    can't push the rest of the song down the strip; the floor is whichever is
    higher of a low percentile and `range_db` below the ceiling, so a track with
    a near-silent passage doesn't stretch the scale across dead air.
    """
    top = _percentile(db, 99.0)
    bottom = max(_percentile(db, 2.0), top - range_db)
    span = max(1e-6, top - bottom)
    return [max(0.0, min(1.0, (v - bottom) / span)) for v in db]


def _auto_gain(db: list[float], amount: float, frame_ms: int, window_ms: int = 4000) -> list[float]:
    """Pull each moment's loudness towards the track's typical loudness.

    Works in dB, before anything clamps: the span mapping below discards
    everything under its floor, so a quiet passage normalised afterwards would
    have already been flattened to nothing and there would be nothing left to
    lift.

    The rolling ceiling is a peak follower run forwards *and* backwards and
    combined, so it rises into a loud section rather than lagging a beat behind
    it, and `amount` blends between leaving the track alone and pinning every
    section to the same ceiling.
    """
    fall = -DB_FLOOR * frame_ms / max(1.0, window_ms)
    forward: list[float] = []
    peak = DB_FLOOR
    for v in db:
        peak = max(v, peak - fall)
        forward.append(peak)
    ceiling = [0.0] * len(db)
    peak = DB_FLOOR
    for i in range(len(db) - 1, -1, -1):
        peak = max(db[i], peak - fall)
        ceiling[i] = max(peak, forward[i])
    # Referenced against the track's own usual ceiling rather than 0 dB, so a
    # quiet recording is not simply amplified to full throughout.
    reference = _percentile(ceiling, 90.0)
    return [v - amount * (c - reference) for v, c in zip(db, ceiling)]


def _fit_curve(level: list[float], contrast: float) -> list[float]:
    """Bend the distribution so this track's median frame lands mid-strip.

    Without this every track needs its own hand-tuned gamma: a dense mix sits
    high and a sparse one sits low. Solving for the exponent that puts the
    median at 0.5 makes one set of settings work across tracks, and leaves
    `contrast` as a taste control on top rather than a correction.
    """
    median = _percentile(level, 50.0)
    gamma = math.log(0.5) / math.log(median) if 0.02 < median < 0.98 else 1.0
    gamma = max(0.2, min(5.0, gamma * contrast))
    if abs(gamma - 1.0) < 1e-3:
        return list(level)
    return [v ** gamma for v in level]


def _ballistics(level: list[float], attack_ms: int, release_ms: int, frame_ms: int) -> list[float]:
    """Limit how fast the fill may rise and fall, in full-scale-per-duration
    terms: `attack_ms` to climb from empty to full, `release_ms` to fall back."""
    rise = 1.0 if attack_ms <= 0 else frame_ms / attack_ms
    fall = 1.0 if release_ms <= 0 else frame_ms / release_ms
    out: list[float] = []
    current = 0.0
    for target in level:
        current = min(target, current + rise) if target > current else max(target, current - fall)
        out.append(current)
    return out


def _resample(level: list[float], from_ms: int, to_ms: int) -> list[int]:
    """Onto the export grid, taking each output frame's peak.

    The peak, not the mean: a kick that lands between two output frames should
    still reach full, and dropping it would quietly undo the attack.
    """
    if not level:
        return []
    count = max(1, int(len(level) * from_ms / to_ms))
    out: list[int] = []
    for f in range(count):
        i = int(f * to_ms / from_ms)
        j = max(i + 1, int((f + 1) * to_ms / from_ms))
        window = level[i:j] or level[-1:]
        out.append(max(0, min(255, round(max(window) * 255))))
    return out
