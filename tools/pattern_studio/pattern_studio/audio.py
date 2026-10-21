"""Decodes an audio file and measures its per-frame loudness, band by band.

Uses Qt Multimedia's QAudioDecoder, which PySide6 ships with an ffmpeg backend,
so MP3, M4A/AAC, FLAC, OGG, WMA and WAV all decode with no added dependency and
no codec to install. The formats a design can use are therefore the same on
every machine.

### Why the arithmetic looks the way it does

A three-and-a-half minute track is around nine million samples, which is far
too many to loop over in Python one at a time. Everything here is therefore
expressed as whole-array operations that run inside CPython's own C loops -
strided slices, `map` over `operator.add`/`mul`, and slice-wide `sum`. Bands
come from a cascade of halvings (average adjacent pairs, keep half the samples),
each of which is one such C-level pass and leaves a signal band-limited to half
the previous corner. That gets a full analysis of a normal song done in a few
seconds with nothing but the standard library.
"""

from __future__ import annotations

from array import array
from itertools import repeat
from operator import add, mul
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer, QUrl
from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat

from .envelope import (
    ANALYSIS_FRAME_MS,
    BAND_BASS,
    BAND_FULL,
    BAND_MID,
    BAND_TREBLE,
    TrackAnalysis,
    analysis_from_power,
)

#: Extensions offered in the file dialog. The decoder will attempt anything the
#: backend recognises, so this is about what to *suggest*, not a hard limit.
AUDIO_SUFFIXES = (".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wav", ".wma")

#: Band corners, in Hz. Chosen for what they isolate on a strip rather than for
#: any standard: kick and bass guitar below the first, the body of the mix below
#: the second, cymbals and air above it.
BASS_HZ = 250
MID_HZ = 2000

#: How long to wait for the decoder to produce anything before giving up. The
#: clock restarts on every buffer, so this bounds a *stall*, not the length of
#: the file - a stalled decode would otherwise block the UI thread forever.
_STALL_TIMEOUT_MS = 20_000

#: Sample formats QAudioBuffer can hand back, as (array typecode, scale, offset)
#: to bring them into -1..1. Which one arrives depends on the file - MP3 decodes
#: to float, FLAC to Int16 - so all of them have to be handled.
_SAMPLE_FORMATS = {
    QAudioFormat.UInt8: ("B", 1 / 128.0, -1.0),
    QAudioFormat.Int16: ("h", 1 / 32768.0, 0.0),
    QAudioFormat.Int32: ("i", 1 / 2147483648.0, 0.0),
    QAudioFormat.Float: ("f", 1.0, 0.0),
}


class AudioError(RuntimeError):
    """A file the audio backend could not decode."""


def is_audio_file(path: str | Path) -> bool:
    return Path(path).suffix.lower() in AUDIO_SUFFIXES


# ============================================================================
# Decoding
# ============================================================================


def _decode(path: Path, on_progress: Callable[[float], None] | None):
    """Decode to interleaved native samples.

    The decoder's own output format is used rather than asking it to convert:
    setAudioFormat() is honoured by some backends and silently produces nothing
    on others (ffmpeg + MP3 among them), so resampling and downmixing are done
    here instead, where they either work or raise.
    """
    app = QCoreApplication.instance()
    if app is None:
        raise AudioError("Audio decoding needs a running Qt application.")

    decoder = QAudioDecoder()
    decoder.setSource(QUrl.fromLocalFile(str(path)))

    chunks: list[bytes] = []
    state: dict = {"error": None, "done": False}
    loop = QEventLoop()

    # Restarted on every buffer, so it bounds a stall rather than the file.
    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.setInterval(_STALL_TIMEOUT_MS)

    def stop(error: str | None = None) -> None:
        if error and not state["error"]:
            state["error"] = error
        state["done"] = True
        watchdog.stop()
        loop.quit()

    def drain() -> None:
        if "raised" in state:
            return  # abandoned: a buffer queued before the loop quit is unwanted
        watchdog.start()
        while decoder.bufferAvailable():
            buffer = decoder.read()
            if "format" not in state:
                fmt = buffer.format()
                if fmt.sampleFormat() not in _SAMPLE_FORMATS:
                    stop(f"Unsupported sample format in {path.name}.")
                    return
                state["format"] = fmt
            chunks.append(bytes(buffer.data()))
        if on_progress is not None and decoder.duration() > 0:
            try:
                on_progress(min(0.75, 0.75 * decoder.position() / decoder.duration()))
            except Exception as exc:
                # This runs as a Qt slot, where an exception is printed and
                # dropped instead of reaching the caller. It is carried out of
                # the loop instead, so a callback can abandon the decode - the
                # Song bar's Cancel button works by raising here.
                state["raised"] = exc
                stop()

    decoder.bufferReady.connect(drain)
    decoder.finished.connect(lambda: stop())
    decoder.error.connect(
        lambda _e: stop(decoder.errorString() or f"Couldn't decode {path.name}.")
    )
    watchdog.timeout.connect(lambda: stop(f"Timed out decoding {path.name}."))

    watchdog.start()
    decoder.start()
    # A short file can decode entirely inside start(), emitting finished before
    # there is a loop to quit. Entering exec() then would block forever.
    if not state["done"]:
        loop.exec()
    watchdog.stop()
    decoder.stop()

    if "raised" in state:
        raise state["raised"]
    if state["error"]:
        raise AudioError(state["error"])
    if "format" not in state or not chunks:
        raise AudioError(
            f"{path.name} produced no audio. It may be an unsupported format, "
            f"or the file may be a video with no sound."
        )

    fmt = state["format"]
    typecode, scale, offset = _SAMPLE_FORMATS[fmt.sampleFormat()]
    samples = array(typecode)
    samples.frombytes(b"".join(chunks))
    return samples, fmt.sampleRate(), fmt.channelCount(), scale, offset


def _to_mono(samples, channels: int, scale: float, offset: float):
    """Interleaved native samples -> mono float in -1..1.

    The channels are summed in their native units and scaled once at the end,
    so every step is a C-level pass: scaling each sample first was a Python
    loop over the whole file, holding a Python float per sample while it ran.

    The sum is never held in the input's own type, since FLAC arrives as Int16
    and two channels of that overflow it. Int16 and UInt8 sum exactly as
    Python integers. Int32 has more bits than a float32 holds, so each
    channel is rounded to float32 before it is added, where scaling it used to
    round it. Either way the result is the same to the bit as scaling first.
    """
    channels = max(1, channels)
    mono = samples
    if channels > 1:
        usable = (len(samples) // channels) * channels
        exact = samples.typecode in "Bh"

        def channel(c: int):
            part = samples[c:usable:channels]
            return part if exact or part.typecode == "f" else array("f", part)

        mono = channel(0)
        for c in range(1, channels):
            mono = map(add, mono, channel(c))
            # A float32 sum is rounded after every add, as it always was. An
            # integer one is exact however it is held, so it stays a lazy
            # chain feeding the final pass instead of an array of its own.
            if not exact:
                mono = array("f", mono)

    gain = scale / channels
    # UInt8 centres on 128: its offset in native units, once per channel summed.
    bias = offset / scale * channels
    if gain == 1.0 and not bias and samples.typecode == "f":
        return mono  # mono float already
    if bias:
        mono = map(add, mono, repeat(bias))
    return array("f", map(mul, mono, repeat(gain)))


def _halve(signal):
    """Average adjacent pairs and keep half the samples: one octave of
    low-pass plus decimation, in a single C-level pass. Gain is 2x, which the
    caller folds into its power scaling rather than paying for another pass."""
    n = len(signal) // 2
    return array("f", map(add, signal[0 : 2 * n : 2], signal[1 : 2 * n : 2]))


def _frame_power(squares, rate: float, frame_count: int, frame_ms: int, gain: float = 1.0):
    """Mean square per output frame, from a precomputed array of squares."""
    step = rate * frame_ms / 1000.0
    out = []
    limit = len(squares)
    for f in range(frame_count):
        i = int(f * step)
        j = min(int((f + 1) * step), limit)
        out.append(sum(squares[i:j]) / (j - i) * gain if j > i else 0.0)
    return out


def _band_below(signal, rate: float, gain: float, frame_count: int, frame_ms: int):
    squares = array("f", map(mul, signal, signal))
    return _frame_power(squares, rate, frame_count, frame_ms, gain)


def analyse_audio(
    path: str | Path,
    *,
    frame_ms: int = ANALYSIS_FRAME_MS,
    on_progress: Callable[[float], None] | None = None,
) -> TrackAnalysis:
    """Decode `path` and measure per-frame loudness in each band.

    `on_progress` is called with 0..1 as the work proceeds, so a long file can
    show something moving; it is the only reason this reports progress at all.
    Anything it raises abandons the analysis and comes out of this call, which
    is how a Cancel button stops one.
    """
    path = Path(path)
    samples, rate, channels, scale, offset = _decode(path, on_progress)
    if rate <= 0:
        raise AudioError(f"{path.name} reports no sample rate.")

    mono = _to_mono(samples, channels, scale, offset)
    del samples
    if on_progress:
        on_progress(0.82)

    frame_count = max(1, int(len(mono) / rate * 1000 / frame_ms))
    full = _band_below(mono, rate, 1.0, frame_count, frame_ms)
    if on_progress:
        on_progress(0.9)

    # Halve until the signal is band-limited to each corner, remembering the
    # levels we pass through. Each halving doubles amplitude, so power gain
    # accumulates as 1/4 per level to keep the bands comparable.
    level, level_rate, gain = mono, float(rate), 1.0
    below_mid = below_bass = None
    mid_rate = bass_rate = level_rate
    mid_gain = bass_gain = gain
    while level_rate > 2 * BASS_HZ and len(level) > 4:
        level = _halve(level)
        level_rate /= 2.0
        gain /= 4.0
        if below_mid is None and level_rate <= 2 * MID_HZ:
            below_mid, mid_rate, mid_gain = level, level_rate, gain
    below_bass, bass_rate, bass_gain = level, level_rate, gain
    if below_mid is None:
        below_mid, mid_rate, mid_gain = below_bass, bass_rate, bass_gain

    bass = _band_below(below_bass, bass_rate, bass_gain, frame_count, frame_ms)
    low_mid = _band_below(below_mid, mid_rate, mid_gain, frame_count, frame_ms)
    if on_progress:
        on_progress(0.98)

    # Bands are differences between nested low-passes. Clamped at zero because
    # the halving cascade is a crude filter and its skirts can overlap slightly.
    return analysis_from_power(
        {
            BAND_BASS: bass,
            BAND_MID: [max(0.0, a - b) for a, b in zip(low_mid, bass)],
            BAND_TREBLE: [max(0.0, a - b) for a, b in zip(full, low_mid)],
            BAND_FULL: full,
        },
        frame_ms=frame_ms,
        name=path.stem,
        source_path=str(path),
    )
