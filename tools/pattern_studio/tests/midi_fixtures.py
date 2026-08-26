"""A minimal Standard MIDI File writer, for testing the reader.

Writing the bytes by hand (rather than checking a .mid binary into the repo)
keeps every test's input readable as source: what a case is about - a tempo
change, running status, a dangling note - is visible in the events it builds.
"""

from __future__ import annotations

import struct

#: Ticks per quarter note used by the helpers below. 480 is what most DAWs
#: write, and it divides evenly into the durations the tests use.
TPQN = 480

#: Microseconds per quarter note at 120 BPM, i.e. 500 ms per beat.
TEMPO_120_BPM = 500_000


def vlq(value: int) -> bytes:
    """Encode a variable-length quantity the way the SMF spec defines it."""
    out = bytearray([value & 0x7F])
    value >>= 7
    while value:
        out.insert(0, (value & 0x7F) | 0x80)
        value >>= 7
    return bytes(out)


def track(events: list[tuple[int, bytes]], *, end: bool = True) -> bytes:
    """One MTrk chunk from (delta ticks, event bytes) pairs."""
    data = b"".join(vlq(delta) + payload for delta, payload in events)
    if end:
        data += vlq(0) + b"\xFF\x2F\x00"
    return b"MTrk" + struct.pack(">I", len(data)) + data


def header(track_count: int, division: int = TPQN) -> bytes:
    return b"MThd" + struct.pack(">IHHH", 6, 1, track_count, division)


def name_event(text: str) -> bytes:
    return b"\xFF\x03" + vlq(len(text)) + text.encode("latin-1")


def tempo_event(usec_per_quarter: int) -> bytes:
    return b"\xFF\x51\x03" + usec_per_quarter.to_bytes(3, "big")


def note_on(pitch: int, velocity: int = 100, channel: int = 0) -> bytes:
    return bytes([0x90 | channel, pitch, velocity])


def note_off(pitch: int, channel: int = 0) -> bytes:
    return bytes([0x80 | channel, pitch, 0])


def write_midi(path, chunks: list[bytes], division: int = TPQN) -> str:
    path = str(path)
    with open(path, "wb") as handle:
        handle.write(header(len(chunks), division) + b"".join(chunks))
    return path


def simple_song(path, *, pitches=(60, 64, 67), beats: int = 1, velocity: int = 100) -> str:
    """A tempo track plus one melody track of back-to-back quarter notes.

    At 120 BPM each beat is 500 ms, so `pitches` of length 3 gives a 1.5 s song
    with notes starting at 0, 500 and 1000 ms.
    """
    tempo = track([(0, name_event("Tempo")), (0, tempo_event(TEMPO_120_BPM))])
    events: list[tuple[int, bytes]] = [(0, name_event("Lead"))]
    for pitch in pitches:
        events.append((0, note_on(pitch, velocity)))
        events.append((TPQN * beats, note_off(pitch)))
    return write_midi(path, [tempo, track(events)])
