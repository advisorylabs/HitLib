"""Reads a Standard MIDI File and measures its per-frame loudness, band by band.

audio.py is the path most designs will use - MIDI versions of real songs are
hard to come by. MIDI is kept because when you do have one it is strictly
better source material: note timing is exact rather than inferred from a
waveform, and the parts are already separated.

Both analysers produce the same TrackAnalysis, so everything that decides how
the strip actually looks lives once, in envelope.py.

The SMF parser is written out here rather than pulled in from a library. It is
about a hundred lines of well-specified format, and Pattern Studio ships as a
frozen executable - a new runtime dependency costs more (in build config and
bundle size) than the parser does.

Anything the format allows but loudness doesn't care about (sysex, controllers,
pitch bend, key signatures) is skipped by length rather than decoded, so
unusual files load instead of failing.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path

from .envelope import (
    ANALYSIS_FRAME_MS,
    BAND_BASS,
    BAND_FULL,
    BAND_MID,
    BAND_TREBLE,
    TrackAnalysis,
    analysis_from_power,
)

#: Microseconds per quarter note before any Set Tempo event says otherwise -
#: 120 BPM, per the SMF spec.
DEFAULT_TEMPO_US = 500_000

#: Longest track worth analysing, in analysis frames. At 10 ms that is about
#: twenty minutes - well past any song, and a guard against a pathological file.
MAX_FRAMES = 120_000


class MidiError(ValueError):
    """A file that isn't a MIDI file, or is one this parser can't make sense of."""


@dataclass(frozen=True)
class Note:
    track: int
    channel: int
    pitch: int
    velocity: int
    start_ms: float
    end_ms: float


@dataclass(frozen=True)
class TrackInfo:
    """One MIDI track, as the track picker shows it."""

    index: int
    name: str
    note_count: int
    channels: tuple[int, ...]

    @property
    def label(self) -> str:
        name = self.name or f"Track {self.index + 1}"
        return f"{name}  ({self.note_count} notes)"


@dataclass(frozen=True)
class MidiSong:
    name: str
    tracks: list[TrackInfo] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    duration_ms: float = 0.0
    source_path: str = ""

    @property
    def playable_tracks(self) -> list[int]:
        """Indices of tracks that actually carry notes - the ones worth
        offering. Track 0 of a format-1 file is usually tempo/meta only."""
        return [t.index for t in self.tracks if t.note_count > 0]


# ============================================================================
# Byte-level reading
# ============================================================================


class _Reader:
    """Cursor over a bytes buffer, raising MidiError instead of IndexError."""

    def __init__(self, data: bytes, pos: int = 0, end: int | None = None):
        self.data = data
        self.pos = pos
        self.end = len(data) if end is None else end

    def eof(self) -> bool:
        return self.pos >= self.end

    def byte(self) -> int:
        if self.pos >= self.end:
            raise MidiError("Unexpected end of MIDI data.")
        b = self.data[self.pos]
        self.pos += 1
        return b

    def peek(self) -> int:
        if self.pos >= self.end:
            raise MidiError("Unexpected end of MIDI data.")
        return self.data[self.pos]

    def take(self, n: int) -> bytes:
        if self.pos + n > self.end:
            raise MidiError("Unexpected end of MIDI data.")
        chunk = self.data[self.pos : self.pos + n]
        self.pos += n
        return chunk

    def u16(self) -> int:
        return int.from_bytes(self.take(2), "big")

    def u32(self) -> int:
        return int.from_bytes(self.take(4), "big")

    def vlq(self) -> int:
        """Variable-length quantity: 7 bits per byte, high bit continues."""
        value = 0
        for _ in range(4):  # the spec caps a VLQ at four bytes
            b = self.byte()
            value = (value << 7) | (b & 0x7F)
            if not b & 0x80:
                return value
        raise MidiError("Malformed variable-length value.")


# ============================================================================
# Tick -> millisecond conversion
# ============================================================================


class _TickClock:
    """Converts absolute ticks to milliseconds under a tempo map.

    Tempo changes are cumulative, so the map is precomputed as (tick, ms at
    that tick, tempo from that tick on) and looked up by bisection rather than
    replayed from the top for every note.
    """

    def __init__(self, ticks_per_quarter: int, tempo_events: list[tuple[int, int]]):
        self.tpqn = max(1, ticks_per_quarter)
        ticks: list[int] = [0]
        times: list[float] = [0.0]
        tempos: list[int] = [DEFAULT_TEMPO_US]

        for tick, tempo in sorted(tempo_events):
            if tick <= ticks[-1]:
                tempos[-1] = tempo  # same instant: the later event wins
                continue
            elapsed = (tick - ticks[-1]) * tempos[-1] / self.tpqn / 1000.0
            ticks.append(tick)
            times.append(times[-1] + elapsed)
            tempos.append(tempo)

        self._ticks = ticks
        self._times = times
        self._tempos = tempos

    def ms(self, tick: int) -> float:
        i = max(0, bisect_right(self._ticks, tick) - 1)
        return self._times[i] + (tick - self._ticks[i]) * self._tempos[i] / self.tpqn / 1000.0


class _SmpteClock:
    """Absolute-time division: ticks are a fixed fraction of a second, and
    Set Tempo has no effect at all."""

    def __init__(self, ticks_per_second: float):
        self.ticks_per_second = max(1.0, ticks_per_second)

    def ms(self, tick: int) -> float:
        return tick * 1000.0 / self.ticks_per_second


# ============================================================================
# File parsing
# ============================================================================


def _parse_track(reader: _Reader, track_index: int):
    """Pull one track's note and tempo events out, still measured in ticks.

    Returns (raw_notes, tempo_events, track_name, end_tick), where a raw note
    is (channel, pitch, velocity, start_tick, end_tick).
    """
    raw_notes: list[tuple[int, int, int, int, int]] = []
    tempo_events: list[tuple[int, int]] = []
    # (channel, pitch) -> queue of (start_tick, velocity). A queue, not a
    # single slot: the same pitch can legally be retriggered before its first
    # note-off arrives, and dropping the earlier one would lose a note.
    sounding: dict[tuple[int, int], list[tuple[int, int]]] = {}
    name = ""
    tick = 0
    status = 0

    while not reader.eof():
        tick += reader.vlq()
        if reader.peek() & 0x80:
            status = reader.byte()
        elif status == 0:
            raise MidiError("MIDI data byte with no running status to apply it to.")

        if status == 0xFF:  # meta event
            meta_type = reader.byte()
            data = reader.take(reader.vlq())
            if meta_type == 0x51 and len(data) == 3:
                tempo_events.append((tick, int.from_bytes(data, "big")))
            elif meta_type == 0x03 and not name:
                name = data.decode("latin-1", "replace").strip()
            elif meta_type == 0x2F:
                break
            continue

        if status in (0xF0, 0xF7):  # sysex, skipped by length
            reader.take(reader.vlq())
            continue

        kind = status & 0xF0
        channel = status & 0x0F
        if kind in (0xC0, 0xD0):  # program change / channel pressure: one data byte
            reader.byte()
            continue

        data1 = reader.byte()
        data2 = reader.byte()
        if kind == 0x90 and data2 > 0:
            sounding.setdefault((channel, data1), []).append((tick, data2))
        elif kind == 0x80 or (kind == 0x90 and data2 == 0):
            queue = sounding.get((channel, data1))
            if queue:
                start_tick, velocity = queue.pop(0)
                raw_notes.append((channel, data1, velocity, start_tick, tick))

    # Notes still held when the track ended (or that never got a note-off) are
    # closed at the track's end rather than dropped.
    for (channel, pitch), queue in sounding.items():
        for start_tick, velocity in queue:
            raw_notes.append((channel, pitch, velocity, start_tick, tick))

    return raw_notes, tempo_events, name, tick


def read_midi(path: str | Path) -> MidiSong:
    """Parse a Standard MIDI File into absolute-time notes.

    Raises MidiError if the file isn't a readable SMF.
    """
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise MidiError(f"Couldn't read {path.name}: {exc}") from exc

    reader = _Reader(data)
    if reader.take(4) != b"MThd":
        raise MidiError(f"{path.name} is not a MIDI file (no MThd header).")
    header_len = reader.u32()
    header_end = reader.pos + header_len
    reader.u16()  # format: 0/1/2 are all walked the same way here
    reader.u16()  # declared track count: the chunks actually present win
    division = reader.u16()
    reader.pos = header_end  # some writers pad the header past its six bytes

    # Chunks other than MTrk are to be skipped by length, per the spec.
    per_track = []
    while reader.pos + 8 <= len(data):
        chunk_id = reader.take(4)
        chunk_len = reader.u32()
        chunk_end = min(reader.pos + chunk_len, len(data))
        if chunk_id == b"MTrk":
            per_track.append(_parse_track(_Reader(data, reader.pos, chunk_end), len(per_track)))
        reader.pos = chunk_end

    if not per_track:
        raise MidiError(f"{path.name} has no track data.")

    if division & 0x8000:
        # SMPTE: the high byte is a negative frames-per-second, the low byte is
        # ticks within one frame.
        fps = 256 - (division >> 8)
        clock = _SmpteClock(fps * (division & 0xFF))
    else:
        tempo_events = [ev for _, tempos, _, _ in per_track for ev in tempos]
        clock = _TickClock(division or 96, tempo_events)

    notes: list[Note] = []
    tracks: list[TrackInfo] = []
    duration_ms = 0.0
    for index, (raw_notes, _tempos, name, end_tick) in enumerate(per_track):
        channels = set()
        for channel, pitch, velocity, start_tick, stop_tick in raw_notes:
            channels.add(channel)
            notes.append(
                Note(
                    track=index,
                    channel=channel,
                    pitch=pitch,
                    velocity=velocity,
                    start_ms=clock.ms(start_tick),
                    end_ms=clock.ms(stop_tick),
                )
            )
        tracks.append(
            TrackInfo(
                index=index,
                name=name,
                note_count=len(raw_notes),
                channels=tuple(sorted(channels)),
            )
        )
        duration_ms = max(duration_ms, clock.ms(end_tick))

    notes.sort(key=lambda n: n.start_ms)
    if notes:
        duration_ms = max(duration_ms, max(n.end_ms for n in notes))
    return MidiSong(
        name=path.stem, tracks=tracks, notes=notes,
        duration_ms=duration_ms, source_path=str(path),
    )




# ============================================================================
# Loudness analysis
# ============================================================================

#: General MIDI percussion lives on channel 10 (9 zero-based), where the note
#: number means instrument rather than pitch. Mapping those by pitch would put
#: the kick drum in the treble band, so they get their own table.
_DRUM_BANDS = {
    35: BAND_BASS, 36: BAND_BASS, 41: BAND_BASS, 45: BAND_BASS,       # kicks, low toms
    37: BAND_MID, 38: BAND_MID, 39: BAND_MID, 40: BAND_MID,           # snare, claps, sticks
    43: BAND_MID, 47: BAND_MID, 48: BAND_MID, 50: BAND_MID,           # toms
    42: BAND_TREBLE, 44: BAND_TREBLE, 46: BAND_TREBLE,                # hats
    49: BAND_TREBLE, 51: BAND_TREBLE, 52: BAND_TREBLE,                # cymbals
    53: BAND_TREBLE, 55: BAND_TREBLE, 57: BAND_TREBLE, 59: BAND_TREBLE,
}
_PERCUSSION_CHANNEL = 9

#: Pitch boundaries for melodic notes: below C3 is bass, C3 to B4 is the body
#: of the arrangement, above that is treble.
_BASS_MAX_PITCH = 47
_MID_MAX_PITCH = 71

#: Extra power on a note's first frame. A struck string or a hit drum has an
#: attack transient; a MIDI note is a rectangle. Without this, two legato notes
#: at the same velocity read as one continuous tone, and Beat mode - which
#: follows *rises* in loudness - sees nothing at all where a listener plainly
#: hears a new note.
_ONSET_BOOST = 3.0


def band_of(note: Note) -> str:
    if note.channel == _PERCUSSION_CHANNEL:
        return _DRUM_BANDS.get(note.pitch, BAND_MID)
    if note.pitch <= _BASS_MAX_PITCH:
        return BAND_BASS
    if note.pitch <= _MID_MAX_PITCH:
        return BAND_MID
    return BAND_TREBLE


def analyse_midi(
    song: MidiSong,
    *,
    tracks: list[int] | None = None,
    frame_ms: int = ANALYSIS_FRAME_MS,
) -> TrackAnalysis:
    """Measure per-frame loudness per band from a parsed MIDI song.

    A note contributes velocity squared - velocity is a loudness-ish scale, and
    squaring puts it in the same power domain the audio analyser works in - to
    every frame it sounds through, and to its band alone. `full` gets all of
    them, so it behaves like the audio path's full-mix band.
    """
    selected = set(song.playable_tracks if tracks is None else tracks)
    notes = [n for n in song.notes if n.track in selected and n.velocity > 0]
    if not notes:
        return TrackAnalysis(name=song.name, frame_ms=frame_ms, source_path=song.source_path)

    frame_count = min(MAX_FRAMES, int(max(n.end_ms for n in notes) / frame_ms) + 1)
    bands = {name: [0.0] * frame_count for name in (BAND_BASS, BAND_MID, BAND_TREBLE, BAND_FULL)}
    full = bands[BAND_FULL]

    for note in notes:
        power = float(note.velocity * note.velocity)
        first = max(0, int(note.start_ms // frame_ms))
        # Half-open on the end, so a note stopping exactly where the next one
        # starts hands the frame over instead of both claiming it.
        last = min(frame_count - 1, max(first, int(-(-note.end_ms // frame_ms)) - 1))
        band = bands[band_of(note)]
        for f in range(first, last + 1):
            band[f] += power
            full[f] += power
        band[first] += power * (_ONSET_BOOST - 1.0)
        full[first] += power * (_ONSET_BOOST - 1.0)

    return analysis_from_power(
        bands, frame_ms=frame_ms, name=song.name, source_path=song.source_path
    )
