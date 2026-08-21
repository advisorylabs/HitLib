import json

from pattern_studio.models import (
    AnimationKind,
    ModeConfig,
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
    configs = [_elaborate_config(), StrandConfig(name="Plain")]
    restored = document_from_dict(document_to_dict(configs))
    assert restored == configs


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


def test_missing_fields_fall_back_to_defaults():
    minimal = {"name": "Bare"}
    restored = strand_from_dict(minimal)
    assert restored == StrandConfig(name="Bare")


def test_save_and_load_file_round_trip(tmp_path):
    configs = [_elaborate_config()]
    path = tmp_path / "profile.hlprofile"
    save_document(path, configs)

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["schema_version"] == 1

    restored = load_document(path)
    assert restored == configs
