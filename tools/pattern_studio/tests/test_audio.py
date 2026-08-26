"""Decoding and band analysis of real audio.

The fixture is a WAV written here rather than a file checked into the repo:
the point is to know what is in it (a 60 Hz thump under a 4 kHz shimmer) so the
band split can be asserted rather than eyeballed.

Everything is skipped if the platform's media backend can't decode. Qt ships
one with PySide6, so that should not happen, but a CI image without it should
report a skip rather than a wall of failures.
"""

import math
import struct
import wave

import pytest

from pattern_studio.audio import AudioError, analyse_audio, is_audio_file
from pattern_studio.envelope import BAND_BASS, BAND_FULL, BAND_MID, BAND_TREBLE

SAMPLE_RATE = 44100


def _write_wav(path, seconds=2.0, *, bass=0.0, treble=0.0, channels=2, pulse_hz=0.0):
    """A WAV of pure tones at known frequencies, so each band has a known answer.

    `pulse_hz` gates the whole thing on and off, which is what gives Beat mode
    something to find.
    """
    frames = bytearray()
    total = int(SAMPLE_RATE * seconds)
    for i in range(total):
        t = i / SAMPLE_RATE
        gate = 1.0
        if pulse_hz:
            phase = (t * pulse_hz) % 1.0
            gate = math.exp(-phase * 14)
        value = gate * (
            bass * math.sin(2 * math.pi * 60 * t) + treble * math.sin(2 * math.pi * 4000 * t)
        )
        sample = int(max(-1.0, min(1.0, value)) * 30000)
        frames += struct.pack("<" + "h" * channels, *([sample] * channels))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(bytes(frames))
    return path


@pytest.fixture(scope="module")
def backend(qapp):
    """Skip the module if the media backend can't decode a plain WAV."""
    import tempfile
    from pathlib import Path

    probe = Path(tempfile.mkdtemp()) / "probe.wav"
    _write_wav(probe, seconds=0.3, bass=0.5)
    try:
        analysis = analyse_audio(probe)
    except AudioError as exc:
        pytest.skip(f"no usable audio backend: {exc}")
    if not analysis.loaded:
        pytest.skip("audio backend decoded nothing")
    return True


def test_suffixes_are_recognised():
    assert is_audio_file("song.mp3")
    assert is_audio_file("Song.FLAC")
    assert not is_audio_file("song.mid")
    assert not is_audio_file("song.hlprofile")


def test_a_wav_analyses_into_every_band(backend, tmp_path):
    analysis = analyse_audio(_write_wav(tmp_path / "tone.wav", bass=0.7, treble=0.3))

    assert set(analysis.bands) == {BAND_BASS, BAND_MID, BAND_TREBLE, BAND_FULL}
    assert analysis.name == "tone"
    # Two seconds on the 10 ms analysis grid, give or take a frame.
    assert 190 <= analysis.frame_count <= 205
    assert analysis.duration_ms == analysis.frame_count * analysis.frame_ms


def test_a_low_tone_lands_in_bass_and_not_in_treble(backend, tmp_path):
    analysis = analyse_audio(_write_wav(tmp_path / "low.wav", bass=0.8))
    middle = analysis.frame_count // 2
    assert analysis.bands[BAND_BASS][middle] > analysis.bands[BAND_TREBLE][middle] + 40


def test_a_high_tone_lands_in_treble_and_not_in_bass(backend, tmp_path):
    analysis = analyse_audio(_write_wav(tmp_path / "high.wav", treble=0.8))
    middle = analysis.frame_count // 2
    assert analysis.bands[BAND_TREBLE][middle] > analysis.bands[BAND_BASS][middle] + 40


def test_mono_and_stereo_of_the_same_tone_agree(backend, tmp_path):
    stereo = analyse_audio(_write_wav(tmp_path / "s.wav", bass=0.6, channels=2))
    mono = analyse_audio(_write_wav(tmp_path / "m.wav", bass=0.6, channels=1))
    middle = min(stereo.frame_count, mono.frame_count) // 2
    # Downmixing must not change the loudness it measures.
    assert abs(stereo.bands[BAND_BASS][middle] - mono.bands[BAND_BASS][middle]) < 8


def test_silence_analyses_to_nothing(backend, tmp_path):
    analysis = analyse_audio(_write_wav(tmp_path / "silent.wav", seconds=0.5))
    assert max(analysis.bands[BAND_FULL]) == 0


def test_progress_is_reported_while_analysing(backend, tmp_path):
    seen = []
    analyse_audio(_write_wav(tmp_path / "p.wav", bass=0.5), on_progress=seen.append)
    assert seen
    assert 0.0 <= min(seen) <= max(seen) <= 1.0
    # It has to actually reach the end, or the dialog would sit unfinished.
    assert max(seen) > 0.9


def test_a_file_that_is_not_audio_raises(backend, tmp_path):
    path = tmp_path / "notaudio.wav"
    path.write_bytes(b"this is definitely not a wave file")
    with pytest.raises(AudioError):
        analyse_audio(path)


def test_a_pulsing_tone_produces_a_beat_envelope(backend, tmp_path):
    from pattern_studio.envelope import EnvelopeSettings, bake

    analysis = analyse_audio(_write_wav(tmp_path / "beat.wav", seconds=4.0, bass=0.8, pulse_hz=2.0))
    envelope = bake(analysis, BAND_BASS, EnvelopeSettings())
    # Eight hits in four seconds, each driving the fill most of the way up.
    assert max(envelope) > 200
    assert min(envelope) < 60
