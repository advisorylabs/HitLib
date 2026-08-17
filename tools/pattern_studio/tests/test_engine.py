from pattern_studio.engine import apply_strand_config, make_strand
from pattern_studio.models import AnimationKind, ModeConfig, PhaseConfig, StrandConfig


def test_single_animation_path_applies_directly():
    cfg = StrandConfig(length=4)
    cfg.animation.kind = AnimationKind.SOLID
    cfg.animation.color = 0x00FF00
    strand = make_strand(cfg)
    strand.tick()
    assert strand.pixels == [0x00FF00] * 4


def test_profile_mode_priority_switch():
    cfg = StrandConfig(length=1, use_profile=True)
    idle = ModeConfig(name="Idle", priority=10)
    idle.animation.kind = AnimationKind.SOLID
    idle.animation.color = 0x000011
    red = ModeConfig(name="Red", priority=50)
    red.animation.kind = AnimationKind.SOLID
    red.animation.color = 0xFF0000
    cfg.profile_modes = [idle, red]
    cfg.active_mode_indices = [0]

    strand = make_strand(cfg)
    strand.tick()
    assert strand.pixels == [0x000011]

    cfg.active_mode_indices = [0, 1]
    apply_strand_config(strand, cfg)  # re-attach + re-activate, as reapply_animation() would
    strand.tick()
    assert strand.pixels == [0xFF0000]  # higher priority wins


def test_sequenced_mode_cycles_phases():
    cfg = StrandConfig(length=1, refresh_ms=20, use_profile=True)
    seq_mode = ModeConfig(name="Endgame", priority=10)
    p1 = PhaseConfig(name="Warn", duration_ms=20)
    p1.animation.kind = AnimationKind.SOLID
    p1.animation.color = 0xFFFF00
    p2 = PhaseConfig(name="Cycle", duration_ms=40)
    p2.animation.kind = AnimationKind.SOLID
    p2.animation.color = 0xFF0000
    seq_mode.phases = [p1, p2]
    cfg.profile_modes = [seq_mode]
    cfg.active_mode_indices = [0]

    strand = make_strand(cfg)
    strand.tick()  # now_ms=20, phase0 active
    assert strand.pixels == [0xFFFF00]

    strand.tick()  # now_ms=40, phase0 elapses -> phase1 starts
    assert strand.pixels == [0xFF0000]


def test_switching_from_profile_back_to_single_detaches():
    cfg = StrandConfig(length=1, use_profile=True)
    mode = ModeConfig(name="Solo", priority=10)
    mode.animation.kind = AnimationKind.SOLID
    mode.animation.color = 0xFF0000
    cfg.profile_modes = [mode]
    cfg.active_mode_indices = [0]
    strand = make_strand(cfg)
    strand.tick()
    assert strand.pixels == [0xFF0000]

    cfg.use_profile = False
    cfg.animation.kind = AnimationKind.SOLID
    cfg.animation.color = 0x0000FF
    apply_strand_config(strand, cfg)
    strand.tick()
    assert strand.pixels == [0x0000FF]
    assert strand.active_profile is None
