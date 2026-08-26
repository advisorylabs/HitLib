"""The Song bar and its wiring into MainWindow, headless.

The file-picker dialog is bypassed the same way the File-menu tests bypass
theirs: by driving the handler the dialog would have called.
"""

from PySide6.QtCore import Qt

from midi_fixtures import simple_song
from pattern_studio.envelope import BAND_BASS, BAND_TREBLE, EnvelopeMode, TrackAnalysis
from pattern_studio.main_window import MainWindow
from pattern_studio.midi import analyse_midi, read_midi
from pattern_studio.models import AnimationKind, MusicConfig
from pattern_studio.music_panel import SOURCE_MIDI, MusicPanel, format_time


def _load_midi(panel: MusicPanel, path) -> None:
    """What _load_midi() does once the dialog has produced a path."""
    song = read_midi(path)
    panel._song = song
    panel._config.source_kind = SOURCE_MIDI
    panel._config.tracks = list(song.playable_tracks)
    panel._refresh_track_list()
    panel._adopt(analyse_midi(song, tracks=panel._config.tracks), path)


def _panel_with_song(tmp_path) -> MusicPanel:
    panel = MusicPanel()
    panel.load(MusicConfig())
    _load_midi(panel, simple_song(tmp_path / "song.mid"))
    return panel


# ============================================================================
# The panel itself
# ============================================================================


def test_panel_is_one_row_until_a_song_is_loaded(qapp, tmp_path):
    panel = MusicPanel()
    panel.load(MusicConfig())
    assert panel.scrubber.isHidden()
    assert not panel.play_btn.isEnabled()

    _load_midi(panel, simple_song(tmp_path / "song.mid"))
    assert not panel.scrubber.isHidden()
    assert panel.play_btn.isEnabled()
    assert panel._config.loaded


def test_loading_bakes_every_band_and_lists_midi_tracks(qapp, tmp_path):
    panel = _panel_with_song(tmp_path)

    assert set(panel._config.bands) == {"bass", "mid", "treble", "full"}
    assert panel._config.duration_ms > 1000
    # Only the track with notes is offered; the tempo track is not.
    assert panel.track_list.count() == 1
    assert panel.track_list.item(0).checkState() == Qt.Checked


def test_the_track_list_is_hidden_for_audio(qapp, tmp_path):
    # Audio has no parts to choose between, so the row would be dead space.
    panel = _panel_with_song(tmp_path)
    assert not panel.tracks_widget.isHidden()

    panel._song = None
    panel._update_enabled()
    assert panel.tracks_widget.isHidden()


def test_changing_a_shaping_control_rebakes_and_announces_it(qapp, tmp_path):
    panel = _panel_with_song(tmp_path)

    announced = []
    panel.song_changed.connect(lambda: announced.append(dict(panel._config.bands)))
    panel.mode_combo.setCurrentIndex(panel.mode_combo.findData(EnvelopeMode.LEVEL))
    qapp.processEvents()

    assert panel._config.settings.mode == EnvelopeMode.LEVEL
    assert announced


def test_release_reaches_the_settings_and_changes_the_tables(qapp, tmp_path):
    panel = _panel_with_song(tmp_path)
    before = list(panel._config.table("mid"))

    panel.release_spin.setValue(1200)
    qapp.processEvents()

    assert panel._config.settings.release_ms == 1200
    assert panel._config.table("mid") != before


def test_unchecking_every_track_empties_the_tables(qapp, tmp_path):
    panel = _panel_with_song(tmp_path)

    panel.settings_btn.setChecked(True)
    panel.track_list.item(0).setCheckState(Qt.Unchecked)
    qapp.processEvents()
    assert not panel._config.loaded

    # ...and the settings stay reachable, or there would be no way to check the
    # track back on.
    assert panel.settings_btn.isChecked()
    assert panel.track_list.isEnabled()
    panel.track_list.item(0).setCheckState(Qt.Checked)
    qapp.processEvents()
    assert panel._config.loaded


def test_loop_is_not_a_shaping_control(qapp, tmp_path):
    panel = _panel_with_song(tmp_path)
    before = list(panel._config.table(BAND_BASS))

    panel.loop_check.setChecked(True)
    qapp.processEvents()
    assert panel._config.loop is True
    assert panel._config.table(BAND_BASS) == before


def test_the_scrubber_shows_the_band_the_selected_strand_follows(qapp, tmp_path):
    panel = _panel_with_song(tmp_path)
    panel.set_preview_band(BAND_TREBLE)
    assert panel.scrubber._samples == panel._config.table(BAND_TREBLE)

    panel.set_preview_band(BAND_BASS)
    assert panel.scrubber._samples == panel._config.table(BAND_BASS)


def test_scrubbing_reports_a_position_inside_the_song(qapp, tmp_path):
    panel = _panel_with_song(tmp_path)

    seen = []
    panel.position_changed.connect(seen.append)
    panel._on_seek(700)
    assert panel.position_ms == 700
    assert seen[-1] == 700

    # Past the end clamps rather than running off into a dark strip.
    panel._on_seek(10_000_000)
    assert panel.position_ms == panel._config.duration_ms


def test_a_saved_song_rebakes_from_its_analysis_alone(qapp):
    # Opening a design on a machine without the source file: the analysis is
    # saved, so the tables come back and the shaping controls still work.
    analysis = TrackAnalysis(bands={BAND_BASS: [0, 40, 200, 255, 90] * 40}, frame_ms=10)
    panel = MusicPanel()
    panel.load(MusicConfig(name="ghost", source_path="C:/gone/missing.mp3", analysis=analysis))

    assert panel._config.loaded
    assert panel.play_btn.isEnabled()
    assert panel.mode_combo.isEnabled()
    panel.attack_spin.setValue(200)
    assert panel._config.loaded


def test_format_time_reads_as_a_transport_clock():
    assert format_time(0) == "0:00"
    assert format_time(221_000) == "3:41"
    assert format_time(12_400, tenths=True) == "0:12.4"


# ============================================================================
# MainWindow wiring
# ============================================================================


def _window_with_song(tmp_path) -> MainWindow:
    win = MainWindow()
    _load_midi(win.music_panel, simple_song(tmp_path / "song.mid"))
    return win


def test_a_loaded_song_reaches_every_music_strand(qapp, tmp_path):
    win = _window_with_song(tmp_path)
    win.add_strand()
    for session in win.sessions:
        session.config.animation.kind = AnimationKind.MUSIC
    win._on_song_changed()

    assert all(s.strand.music_track is not None for s in win.sessions)


def test_two_strands_can_follow_different_bands_of_one_song(qapp, tmp_path):
    # The reason band is a per-strand choice: one strip pumping on the kick and
    # another sparkling on the hats is the whole point of having bands.
    win = _window_with_song(tmp_path)
    win.add_strand()
    win.sessions[0].config.animation.kind = AnimationKind.MUSIC
    win.sessions[0].config.animation.band = "mid"
    win.sessions[1].config.animation.kind = AnimationKind.MUSIC
    win.sessions[1].config.animation.band = "treble"
    win._on_song_changed()

    first, second = (s.strand.music_track for s in win.sessions)
    assert first is not None and second is not None
    assert first.samples != second.samples


def test_moving_the_transport_moves_every_strand(qapp, tmp_path):
    win = _window_with_song(tmp_path)
    win.sessions[0].config.animation.kind = AnimationKind.MUSIC
    win._on_song_changed()

    win.music_panel._on_seek(600)
    qapp.processEvents()
    assert win.sessions[0].strand.music_position_ms() == 600


def test_editing_a_strand_mid_song_does_not_rewind_it(qapp, tmp_path):
    # Rebuilding an engine strand restarts its playback at zero; without the
    # resync in _apply_group_edit, nudging a color would jump the whole preview
    # back to the top of the track.
    win = _window_with_song(tmp_path)
    win.sessions[0].config.animation.kind = AnimationKind.MUSIC
    win._on_song_changed()
    win.music_panel._on_seek(900)

    win._apply_group_edit(rebuild=True)
    assert win.sessions[0].strand.music_position_ms() == 900


def test_a_strand_added_mid_song_joins_at_the_current_position(qapp, tmp_path):
    win = _window_with_song(tmp_path)
    win.music_panel._on_seek(750)

    win.add_strand()
    assert win.sessions[-1].strand.music_position_ms() == 750


def test_a_paused_transport_does_not_let_strands_run_ahead(qapp, tmp_path):
    win = _window_with_song(tmp_path)
    win.sessions[0].config.animation.kind = AnimationKind.MUSIC
    win._on_song_changed()
    win.music_panel._on_seek(400)

    strand = win.sessions[0].strand
    for _ in range(10):
        strand.tick()
    assert strand.music_position_ms() == 400


def test_selecting_a_strand_points_the_scrubber_at_its_band(qapp, tmp_path):
    win = _window_with_song(tmp_path)
    win.sessions[0].config.animation.kind = AnimationKind.MUSIC
    win.sessions[0].config.animation.band = BAND_TREBLE
    win._sync_preview_band()
    assert win.music_panel._preview_band == BAND_TREBLE


def test_new_document_clears_the_song(qapp, tmp_path):
    win = _window_with_song(tmp_path)
    assert win.music.loaded

    win._file_new()
    assert not win.music.loaded
    assert win.music_panel.scrubber.isHidden()


def test_saving_and_reopening_keeps_the_song(qapp, tmp_path):
    from pattern_studio.serialization import load_document

    win = _window_with_song(tmp_path)
    win.sessions[0].config.animation.kind = AnimationKind.MUSIC
    analysed = dict(win.music.analysis.bands)
    baked = dict(win.music.bands)

    path = tmp_path / "show.hlprofile"
    win._write_to(path)
    reopened = load_document(path)

    # The analysis is what is saved; the tables are re-baked from it.
    assert reopened.music.analysis.bands == analysed
    assert reopened.music.name == win.music.name
    assert not reopened.music.bands

    fresh = MainWindow()
    fresh.music = reopened.music
    fresh.music_panel.load(fresh.music)
    assert fresh.music.bands == baked


def test_import_adopts_a_song_only_when_there_is_none(qapp, tmp_path):
    from pattern_studio.models import Document, StrandConfig
    from pattern_studio.serialization import load_document, save_document

    donor_path = tmp_path / "donor.hlprofile"
    save_document(donor_path, Document(
        strands=[StrandConfig(name="Imported")],
        music=MusicConfig(
            name="donated",
            analysis=TrackAnalysis(bands={BAND_BASS: [0, 255, 0] * 50}, frame_ms=10),
        ),
    ))
    donor = load_document(donor_path)
    # An imported document's tables are re-baked the same way an opened one's are.
    donor.music.bands = {BAND_BASS: [0, 255, 0] * 50}

    # Nothing loaded yet: the imported song is taken, and the strand that was
    # already open picks it up too.
    empty = MainWindow()
    empty.sessions[0].config.animation.kind = AnimationKind.MUSIC
    assert empty._adopt_music(donor) is True
    assert empty.music.name == "donated"
    assert empty.sessions[0].strand.music_track is not None

    # A song already loaded is never replaced by an import.
    occupied = _window_with_song(tmp_path)
    mine = occupied.music.name
    assert occupied._adopt_music(donor) is False
    assert occupied.music.name == mine
