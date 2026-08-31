import json

from pattern_studio.envelope import BAND_BASS, EnvelopeMode, EnvelopeSettings, TrackAnalysis
from pattern_studio.models import (
    AnimationKind,
    Document,
    GaugeBlendKind,
    GaugeStopConfig,
    GaugeStyleKind,
    ModeConfig,
    MusicConfig,
    OverlayAnimationKind,
    PhaseConfig,
    SpliceModeKind,
    SpliceRegionConfig,
    StrandConfig,
)
from pattern_studio.serialization import (
    document_from_dict,
    document_to_dict,
    load_document,
    save_document,
    strand_from_dict,
    strand_to_dict,
)


def _elaborate_config() -> StrandConfig:
    cfg = StrandConfig(name="Front", adi_port=3, smart_port=5, length=40, refresh_ms=25, brightness=80)
    cfg.animation.kind = AnimationKind.FLOW
    cfg.animation.color = 0x112233
    cfg.animation.color2 = 0x445566
    cfg.splice.enabled = True
    cfg.splice.sections = 3
    cfg.use_profile = True

    mode = ModeConfig(name="Endgame", priority=99)
    p1 = PhaseConfig(name="Warn", duration_ms=1500)
    p1.animation.kind = AnimationKind.FLASH
    p1.animation.color = 0xFFFF00
    p2 = PhaseConfig(name="Go", duration_ms=8000)
    p2.animation.kind = AnimationKind.RAINBOW
    mode.phases = [p1, p2]
    cfg.profile_modes = [mode]
    cfg.active_mode_indices = [0]
    return cfg


def test_strand_round_trip_preserves_every_field():
    cfg = _elaborate_config()
    restored = strand_from_dict(strand_to_dict(cfg))
    assert restored == cfg


def test_document_round_trip():
    doc = Document(strands=[_elaborate_config(), StrandConfig(name="Plain")])
    restored = document_from_dict(document_to_dict(doc))
    assert restored == doc


def _music() -> MusicConfig:
    return MusicConfig(
        source_path="C:/songs/anthem.mp3",
        name="anthem",
        source_kind="audio",
        loop=True,
        tracks=[1, 3],
        settings=EnvelopeSettings(
            mode=EnvelopeMode.LEVEL, attack_ms=5, release_ms=400,
            contrast=140, auto_gain=25, frame_ms=20,
        ),
        analysis=TrackAnalysis(
            bands={BAND_BASS: [0, 1, 127, 255, 40] * 20, "full": [9] * 100},
            frame_ms=10,
            name="anthem",
            source_path="C:/songs/anthem.mp3",
        ),
    )


def test_music_settings_and_analysis_round_trip():
    doc = Document(strands=[StrandConfig(name="Meter")], music=_music())
    encoded = document_to_dict(doc)
    # The analysis rides as compressed base64 rather than a hundred thousand
    # JSON integers.
    assert isinstance(encoded["music"]["analysis"]["bands"][BAND_BASS], str)
    assert document_from_dict(encoded) == doc


def test_baked_tables_are_not_saved():
    # They are derived from the analysis, and two copies in one file could
    # disagree. The panel re-bakes them on load.
    doc = Document(music=_music())
    doc.music.bands = {BAND_BASS: [1, 2, 3]}
    assert "bands" not in document_to_dict(doc)["music"]
    assert document_from_dict(document_to_dict(doc)).music.bands == {}


def test_a_long_analysis_stays_a_reasonable_size():
    # Four minutes of four bands is close to a hundred thousand values; without
    # compression the design file would be dwarfed by it.
    frames = [(i * 7) % 256 for i in range(24_000)]
    doc = Document(music=MusicConfig(
        analysis=TrackAnalysis(bands={b: frames for b in ("bass", "mid", "treble", "full")})
    ))
    assert len(json.dumps(document_to_dict(doc))) < 60_000


def test_a_schema_2_song_is_dropped_rather_than_misread():
    """Schema 2 stored one baked table and no analysis, so there is nothing to
    re-shape from. The strands still come through untouched."""
    restored = document_from_dict({
        "schema_version": 2,
        "strands": [{"name": "Kept"}],
        "music": {"name": "old", "samples": "AAECAw==", "frame_ms": 25},
    })
    assert restored.strands[0].name == "Kept"
    assert not restored.music.loaded


def test_document_without_music_block_loads_empty_song():
    """Schema 1 files predate the Song bar entirely."""
    restored = document_from_dict({"schema_version": 1, "strands": [{"name": "Old"}]})
    assert restored.strands[0].name == "Old"
    assert restored.music == MusicConfig()
    assert not restored.music.loaded


def test_custom_splice_regions_and_overlay_round_trip():
    cfg = StrandConfig(name="Custom Splice")
    cfg.splice.enabled = True
    cfg.splice.mode = SpliceModeKind.CUSTOM

    region1 = SpliceRegionConfig(start=0, width=5)
    region1.animation.kind = OverlayAnimationKind.SOLID
    region1.animation.color = 0xFF0000
    region2 = SpliceRegionConfig(start=10, width=3)
    region2.animation.kind = OverlayAnimationKind.PULSE
    region2.animation.color = 0x00FF00
    region2.animation.run_length = 4
    cfg.splice.regions = [region1, region2]

    # Split mode's own shared overlay config should round-trip independently
    # of the per-region ones above.
    cfg.splice.overlay.kind = OverlayAnimationKind.FLOW
    cfg.splice.overlay.color2 = 0x00FF00

    restored = strand_from_dict(strand_to_dict(cfg))
    assert restored == cfg


def test_a_fill_source_round_trips():
    cfg = StrandConfig(name="Gauge")
    cfg.animation.kind = AnimationKind.FILL
    cfg.animation.source = "motor_temp"
    cfg.animation.source_port = 11
    cfg.animation.source_empty = 20
    cfg.animation.source_full = 70
    cfg.animation.source_wrap = True
    cfg.animation.smoothing = 40
    cfg.animation.preview_sweep = False
    cfg.animation.preview_level = 75

    assert strand_from_dict(strand_to_dict(cfg)) == cfg


def test_a_file_without_fill_fields_loads_as_a_hand_driven_meter():
    # Files written before Fill existed carry none of its fields, and must not
    # come back pointing at a device that was never chosen.
    restored = strand_from_dict({"name": "Old", "animation": {"kind": "solid"}})
    assert restored.animation.source == "manual"
    assert restored.animation.source_port == 1


def test_missing_fields_fall_back_to_defaults():
    minimal = {"name": "Bare"}
    restored = strand_from_dict(minimal)
    assert restored == StrandConfig(name="Bare")


def test_save_and_load_file_round_trip(tmp_path):
    doc = Document(strands=[_elaborate_config()])
    path = tmp_path / "profile.hlprofile"
    save_document(path, doc)

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["schema_version"] == 6

    restored = load_document(path)
    assert restored == doc


def test_a_gauge_region_round_trips_with_its_whole_scale():
    cfg = StrandConfig(name="Drive Heat", length=60)
    cfg.splice.enabled = True
    cfg.splice.mode = SpliceModeKind.CUSTOM
    region = SpliceRegionConfig(start=10, width=9)
    region.animation.kind = OverlayAnimationKind.GAUGE
    region.animation.source = "motor_temp"
    region.animation.source_port = 11
    region.animation.source_empty = 20
    region.animation.source_full = 70
    region.animation.smoothing = 80
    region.animation.style = GaugeStyleKind.BAR
    region.animation.blend = GaugeBlendKind.STEP
    region.animation.invert = True
    region.animation.stops = [
        GaugeStopConfig(at=20.0, color=0x00FF00),
        GaugeStopConfig(at=55.0, color=0xFF7000),
        GaugeStopConfig(at=70.0, color=0xFF00FF),
    ]
    cfg.splice.regions = [region]

    restored = strand_from_dict(strand_to_dict(cfg)).splice.regions[0].animation

    assert restored.kind == OverlayAnimationKind.GAUGE
    assert restored.source == "motor_temp"
    assert restored.source_port == 11
    assert (restored.source_empty, restored.source_full) == (20, 70)
    assert restored.smoothing == 80
    assert restored.style == GaugeStyleKind.BAR
    assert restored.blend == GaugeBlendKind.STEP
    assert restored.invert is True
    assert [(stop.at, stop.color) for stop in restored.stops] == [
        (20.0, 0x00FF00), (55.0, 0xFF7000), (70.0, 0xFF00FF)
    ]


def test_a_schema_4_region_loads_without_gauge_fields():
    """Files written before gauges existed have regions with no scale and no
    source. They come back as the plain animated regions they always were."""
    payload = {
        "start": 4,
        "width": 6,
        "animation": {"kind": "rainbow", "speed": 3},
    }
    cfg = strand_from_dict({
        "name": "Old",
        "splice": {"enabled": True, "mode": "custom", "regions": [payload]},
    })
    animation = cfg.splice.regions[0].animation

    assert animation.kind == OverlayAnimationKind.RAINBOW
    assert animation.speed == 3
    assert animation.stops == []
    assert animation.style == GaugeStyleKind.HEAT
