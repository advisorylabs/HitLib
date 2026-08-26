import pytest

from midi_fixtures import (
    TEMPO_120_BPM,
    TPQN,
    name_event,
    note_off,
    note_on,
    simple_song,
    tempo_event,
    track,
    write_midi,
)
from pattern_studio.envelope import BAND_BASS, BAND_MID, BAND_TREBLE
from pattern_studio.midi import MidiError, analyse_midi, band_of, read_midi


# ============================================================================
# Parsing
# ============================================================================


def test_reads_notes_at_absolute_times(tmp_path):
    song = read_midi(simple_song(tmp_path / "song.mid"))

    assert [n.pitch for n in song.notes] == [60, 64, 67]
    # 120 BPM, quarter notes: 500 ms apart on the nose.
    assert [n.start_ms for n in song.notes] == [0.0, 500.0, 1000.0]
    assert [n.end_ms for n in song.notes] == [500.0, 1000.0, 1500.0]
    assert song.duration_ms == 1500.0


def test_track_names_and_note_counts_are_reported(tmp_path):
    song = read_midi(simple_song(tmp_path / "song.mid"))

    assert [t.name for t in song.tracks] == ["Tempo", "Lead"]
    assert [t.note_count for t in song.tracks] == [0, 3]
    # The tempo track has no notes, so it is not worth offering in the picker.
    assert song.playable_tracks == [1]


def test_tempo_change_mid_song_shifts_later_notes(tmp_path):
    # Two quarter notes: the first at 120 BPM (500 ms), then the tempo doubles
    # so the second lasts 250 ms.
    events = [
        (0, tempo_event(TEMPO_120_BPM)),
        (0, note_on(60)),
        (TPQN, note_off(60)),
        (0, tempo_event(TEMPO_120_BPM // 2)),
        (0, note_on(62)),
        (TPQN, note_off(62)),
    ]
    song = read_midi(write_midi(tmp_path / "tempo.mid", [track(events)]))

    assert [n.start_ms for n in song.notes] == [0.0, 500.0]
    assert [n.end_ms for n in song.notes] == [500.0, 750.0]


def test_running_status_and_note_on_zero_velocity_are_note_offs(tmp_path):
    # Both shorthands real files use: the status byte omitted on the second
    # message, and a note-off written as note-on with velocity 0.
    events = [
        (0, note_on(60, 90)),
        (TPQN, bytes([60, 0])),  # running status: still 0x90, velocity 0
    ]
    song = read_midi(write_midi(tmp_path / "running.mid", [track(events)]))

    assert len(song.notes) == 1
    assert song.notes[0].velocity == 90
    assert song.notes[0].end_ms == 500.0


def test_note_held_past_the_end_is_closed_at_the_track_end(tmp_path):
    events = [(0, note_on(60)), (TPQN * 2, name_event("filler"))]
    song = read_midi(write_midi(tmp_path / "dangling.mid", [track(events)]))

    assert len(song.notes) == 1
    assert song.notes[0].end_ms == 1000.0


def test_repeated_pitch_before_its_note_off_keeps_both_notes(tmp_path):
    # A retrigger while the first note is still down. Keeping one slot per
    # pitch would silently drop the earlier note.
    events = [
        (0, note_on(60, 80)),
        (TPQN, note_on(60, 110)),
        (TPQN, note_off(60)),
        (TPQN, note_off(60)),
    ]
    song = read_midi(write_midi(tmp_path / "retrigger.mid", [track(events)]))

    assert sorted(n.velocity for n in song.notes) == [80, 110]


def test_unknown_chunks_and_sysex_are_skipped(tmp_path):
    import struct

    padding = b"XYZW" + struct.pack(">I", 4) + b"junk"
    events = [
        (0, b"\xF0" + bytes([3]) + b"\x7E\x7F\xF7"),  # sysex, skipped by length
        (0, note_on(60)),
        (TPQN, note_off(60)),
    ]
    path = tmp_path / "odd.mid"
    with open(path, "wb") as handle:
        from midi_fixtures import header

        handle.write(header(1) + padding + track(events))

    song = read_midi(path)
    assert [n.pitch for n in song.notes] == [60]


def test_smpte_division_uses_absolute_frame_timing(tmp_path):
    # Division 0xE728: -25 fps, 40 ticks per frame -> 1000 ticks per second,
    # and Set Tempo has no say in it.
    events = [(0, tempo_event(TEMPO_120_BPM)), (0, note_on(60)), (500, note_off(60))]
    song = read_midi(write_midi(tmp_path / "smpte.mid", [track(events)], division=0xE728))

    assert song.notes[0].end_ms == pytest.approx(500.0)


def test_a_file_that_is_not_midi_raises(tmp_path):
    path = tmp_path / "notmidi.mid"
    path.write_bytes(b"this is not a midi file at all")
    with pytest.raises(MidiError):
        read_midi(path)


# ============================================================================
# Loudness analysis
# ============================================================================


def _analysis(tmp_path, **kwargs):
    return analyse_midi(read_midi(simple_song(tmp_path / "song.mid")), frame_ms=10, **kwargs)


def test_notes_land_in_bands_by_pitch(tmp_path):
    from pattern_studio.midi import Note

    low = Note(track=0, channel=0, pitch=36, velocity=100, start_ms=0, end_ms=1)
    mid = Note(track=0, channel=0, pitch=60, velocity=100, start_ms=0, end_ms=1)
    high = Note(track=0, channel=0, pitch=90, velocity=100, start_ms=0, end_ms=1)
    assert (band_of(low), band_of(mid), band_of(high)) == (BAND_BASS, BAND_MID, BAND_TREBLE)


def test_percussion_is_mapped_by_instrument_not_pitch(tmp_path):
    from pattern_studio.midi import Note

    # Channel 10 (9 zero-based): note number means instrument. A kick is note 36,
    # which as a pitch would land in the bass band anyway - a hi-hat is note 42,
    # which as a pitch would too, and must not.
    kick = Note(track=0, channel=9, pitch=36, velocity=100, start_ms=0, end_ms=1)
    hat = Note(track=0, channel=9, pitch=42, velocity=100, start_ms=0, end_ms=1)
    snare = Note(track=0, channel=9, pitch=38, velocity=100, start_ms=0, end_ms=1)
    assert (band_of(kick), band_of(hat), band_of(snare)) == (BAND_BASS, BAND_TREBLE, BAND_MID)


def test_analysis_covers_the_song_and_every_band(tmp_path):
    analysis = _analysis(tmp_path)
    assert set(analysis.bands) == {"bass", "mid", "treble", "full"}
    # 1.5 s of notes on a 10 ms grid.
    assert 145 <= analysis.frame_count <= 155
    assert analysis.duration_ms == analysis.frame_count * 10


def test_the_melody_lands_in_mid_and_leaves_bass_silent(tmp_path):
    analysis = _analysis(tmp_path)
    assert max(analysis.bands[BAND_MID]) > 0
    assert max(analysis.bands[BAND_BASS]) == 0


def test_note_onsets_get_a_transient_so_beat_mode_can_see_them(tmp_path):
    # A MIDI note is a rectangle: two legato notes at one velocity are a single
    # continuous level, and Beat mode - which follows rises - would see nothing
    # where a listener plainly hears a new note.
    analysis = _analysis(tmp_path)
    mid = analysis.bands[BAND_MID]
    onset = int(500 / 10)  # the second note starts at 500 ms
    assert mid[onset] > mid[onset - 1]


def test_only_selected_tracks_feed_the_analysis(tmp_path):
    song = read_midi(simple_song(tmp_path / "song.mid"))
    assert song.playable_tracks == [1]
    assert not analyse_midi(song, tracks=[]).loaded
    assert analyse_midi(song, tracks=[1]).loaded


def test_a_song_with_no_notes_analyses_to_nothing(tmp_path):
    song = read_midi(write_midi(tmp_path / "empty.mid", [track([(0, name_event("Silent"))])]))
    assert song.playable_tracks == []
    assert not analyse_midi(song).loaded
