"""The shaping stage - the part that decides how the strip actually looks.

Several of these pin down behaviour that the first version of this feature got
wrong and that produced a strip which "stayed bouncing around 40%": a linear
amplitude mapping, a flux normaliser that collapsed on sparse onsets, and no
per-track fitting. They are written against the distribution of the output
rather than exact values, because that is what "looks good" actually means
here.
"""

import random

from pattern_studio.envelope import (
    BAND_BASS,
    DB_FLOOR,
    EnvelopeMode,
    EnvelopeSettings,
    TrackAnalysis,
    _fit_curve,
    analysis_from_power,
    bake,
    dequantise_db,
    quantise_db,
)


def _percentile(values, q):
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, int(q / 100 * (len(ordered) - 1))))]


def _analysis(power, frame_ms=10):
    return analysis_from_power({BAND_BASS: power}, frame_ms=frame_ms)


def _pulse_train(beats=64, period=50, decay=0.86, floor=0.002, seed=7):
    """Something shaped like a kick drum track: a hit every `period` frames,
    decaying between them, over a restless noise floor.

    The jitter matters. A mathematically clean decay has exactly one rising
    frame per beat and zero everywhere else, which no recording does - real
    material has small rises all through it, and a fixture without them
    exercises a distribution the shaping stage will never actually see.
    """
    rng = random.Random(seed)
    out = []
    for _ in range(beats):
        value = 1.0
        for _ in range(period):
            out.append(max(floor, value) * (0.6 + 0.8 * rng.random()))
            value *= decay
    return out


# ============================================================================
# Quantisation
# ============================================================================


def test_db_survives_a_round_trip_through_a_byte():
    original = [0.0, -6.0, -20.0, -47.5, DB_FLOOR]
    restored = dequantise_db(quantise_db(original))
    assert all(abs(a - b) < 0.2 for a, b in zip(original, restored))


def test_power_is_scaled_so_the_loudest_frame_is_full():
    # The two analysers work on incompatible scales - audio samples are bounded
    # at 1.0, MIDI velocities square into the thousands - and either would fall
    # outside the quantisation window without this.
    quiet = analysis_from_power({BAND_BASS: [0.0001, 0.00005]})
    loud = analysis_from_power({BAND_BASS: [16129.0, 8064.0]})
    assert max(quiet.bands[BAND_BASS]) == 255
    assert max(loud.bands[BAND_BASS]) == 255


def test_bands_are_scaled_together_so_their_balance_survives():
    analysis = analysis_from_power({"bass": [1.0, 1.0], "treble": [0.01, 0.01]})
    # 20 dB apart before, and still 20 dB apart after.
    bass = dequantise_db(analysis.bands["bass"])[0]
    treble = dequantise_db(analysis.bands["treble"])[0]
    assert abs((bass - treble) - 20.0) < 0.5


# ============================================================================
# Dynamic range
# ============================================================================


def test_a_compressed_track_still_uses_the_whole_strip():
    # The original failure: real music lives in the top few dB of its range, so
    # a linear mapping left the meter hovering near the bottom and never
    # reaching the top. Whatever the input distribution, the output has to span
    # the strip.
    analysis = _analysis(_pulse_train())
    envelope = bake(analysis, BAND_BASS, EnvelopeSettings())

    assert _percentile(envelope, 5) < 60
    assert _percentile(envelope, 95) > 180
    assert max(envelope) > 230


def test_auto_contrast_centres_whatever_distribution_it_is_given():
    # What auto-contrast is for: a dense mix would otherwise sit pinned high and
    # a sparse one pinned low, and every track would need hand-tuning. Asserted
    # against _fit_curve rather than the finished table because that is where
    # the property holds - the ballistics afterwards deliberately drag the fill
    # down between hits, and are supposed to.
    for skew in (0.3, 1.0, 3.0):
        values = [(i / 500) ** skew for i in range(500)]
        fitted = _fit_curve(values, 1.0)
        assert abs(_percentile(fitted, 50) - 0.5) < 0.05, skew


def test_the_finished_table_spans_the_strip_across_material():
    for decay in (0.80, 0.92, 0.97):
        envelope = bake(_analysis(_pulse_train(decay=decay)), BAND_BASS, EnvelopeSettings())
        assert _percentile(envelope, 10) < 80, decay
        assert _percentile(envelope, 90) > 170, decay


def test_level_mode_tracks_loudness_rather_than_change():
    ramp = [10 ** (db / 10.0) for db in [-40 + i * 0.05 for i in range(800)]]
    envelope = bake(_analysis(ramp), BAND_BASS, EnvelopeSettings(mode=EnvelopeMode.LEVEL))
    # A steadily rising level should come out steadily rising.
    assert envelope[0] < envelope[len(envelope) // 2] < envelope[-1]


def test_beat_mode_punches_on_rises_and_ignores_a_steady_level():
    steady = bake(_analysis([0.5] * 600), BAND_BASS, EnvelopeSettings())
    assert max(steady) == 0

    pulses = bake(_analysis(_pulse_train()), BAND_BASS, EnvelopeSettings())
    assert max(pulses) > 200


def test_beat_mode_survives_sparse_onsets():
    # Normalising flux against a percentile over every frame gives zero when
    # onsets are sparse, as in any MIDI, flattening the envelope where the
    # onsets matter most.
    sparse = [0.001] * 2000
    for i in range(0, 2000, 400):
        # A few frames wide, like anything that has actually been struck: a
        # single-frame impulse can't drive the fill past the attack limit.
        for k in range(6):
            sparse[i + k] = 1.0
    envelope = bake(_analysis(sparse), BAND_BASS, EnvelopeSettings())
    assert max(envelope) > 200


def test_beat_mode_does_not_care_how_loud_the_passage_is():
    # Flux is a difference of dB, so scaling the whole signal shifts both terms
    # equally and cancels. A quiet verse therefore drives the strip as hard as
    # the chorus without any help - which is also why auto-gain is a Level-mode
    # control (see the test below).
    loud = _pulse_train(beats=20)
    quiet = [v * 0.001 for v in loud]
    assert bake(_analysis(loud), BAND_BASS, EnvelopeSettings()) == bake(
        _analysis(quiet), BAND_BASS, EnvelopeSettings()
    )


def test_auto_gain_lifts_a_quiet_passage_in_level_mode():
    # Half the track 30 dB down. Level mode follows loudness, so without
    # auto-gain the quiet half sits near the bottom of the strip; with it, the
    # rolling ceiling brings it back up.
    loud = _pulse_train(beats=30)
    quiet = [v * 0.001 for v in _pulse_train(beats=30)]
    analysis = _analysis(loud + quiet)

    without = bake(analysis, BAND_BASS, EnvelopeSettings(mode=EnvelopeMode.LEVEL, auto_gain=0))
    with_agc = bake(analysis, BAND_BASS, EnvelopeSettings(mode=EnvelopeMode.LEVEL, auto_gain=100))
    tail = len(without) // 2
    assert _percentile(with_agc[tail:], 50) > _percentile(without[tail:], 50) + 20


def test_contrast_moves_the_distribution():
    analysis = _analysis(_pulse_train())
    punchy = bake(analysis, BAND_BASS, EnvelopeSettings(contrast=180))
    busy = bake(analysis, BAND_BASS, EnvelopeSettings(contrast=60))
    assert _percentile(punchy, 50) < _percentile(busy, 50)


# ============================================================================
# Ballistics and resampling
# ============================================================================


def test_release_sets_how_long_the_fall_takes():
    spike = [0.001] * 400
    spike[10] = 1.0
    fast = bake(_analysis(spike), BAND_BASS, EnvelopeSettings(release_ms=100))
    slow = bake(_analysis(spike), BAND_BASS, EnvelopeSettings(release_ms=1000))
    # Ten frames after the hit the fast one is out and the slow one is not.
    assert fast[14] < slow[14]


def test_attack_limits_how_fast_the_fill_climbs():
    spike = [0.001] * 400
    spike[10] = 1.0
    instant = bake(_analysis(spike), BAND_BASS, EnvelopeSettings(attack_ms=0, frame_ms=10))
    slow = bake(_analysis(spike), BAND_BASS, EnvelopeSettings(attack_ms=300, frame_ms=10))
    assert max(instant[:14]) > max(slow[:14])


def test_a_single_frame_impulse_cannot_outrun_the_attack():
    # Not a defect: the attack is a rate limit, so ten milliseconds of input
    # moves the fill ten milliseconds' worth. Real onsets are wider than one
    # frame, which is how they reach the top.
    spike = [0.001] * 400
    spike[10] = 1.0
    envelope = bake(_analysis(spike), BAND_BASS, EnvelopeSettings(attack_ms=20, smooth_ms=0))
    assert 100 < max(envelope) < 160


def test_frame_ms_sets_the_exported_table_size():
    analysis = _analysis(_pulse_train())
    fine = bake(analysis, BAND_BASS, EnvelopeSettings(frame_ms=10))
    coarse = bake(analysis, BAND_BASS, EnvelopeSettings(frame_ms=40))
    assert len(fine) == analysis.frame_count
    assert abs(len(coarse) * 4 - len(fine)) <= 4


def test_resampling_keeps_the_peak_of_each_window():
    # A hit landing between two output frames must still reach full, or the
    # coarser export would quietly undo the attack. Instant attack, so this
    # isolates the resampling from the ballistics.
    spike = [0.0005] * 400
    spike[37] = 1.0
    coarse = bake(
        _analysis(spike), BAND_BASS,
        EnvelopeSettings(attack_ms=0, smooth_ms=0, frame_ms=100),
    )
    assert max(coarse) > 200


def test_an_empty_or_missing_band_bakes_to_nothing():
    assert bake(TrackAnalysis(), BAND_BASS, EnvelopeSettings()) == []
    assert bake(_analysis([]), "treble", EnvelopeSettings()) == []


def test_silence_does_not_produce_noise():
    assert max(bake(_analysis([0.0] * 500), BAND_BASS, EnvelopeSettings())) == 0
