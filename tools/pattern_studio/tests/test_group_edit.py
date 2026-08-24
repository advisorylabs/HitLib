"""Unit tests for the field-level diff/apply behind group editing."""

from copy import deepcopy

from pattern_studio.group_edit import apply_changes, diff_config
from pattern_studio.models import (
    AnimationKind,
    ModeConfig,
    SpliceRegionConfig,
    StrandConfig,
)


def test_identical_configs_have_no_changes():
    cfg = StrandConfig()
    assert diff_config(cfg, deepcopy(cfg)) == []


def test_nested_edit_reports_only_the_leaf_field():
    before = StrandConfig()
    after = deepcopy(before)
    after.animation.color = 0x00FF00

    assert diff_config(before, after) == [(("animation", "color"), 0x00FF00)]


def test_identity_fields_never_propagate():
    before = StrandConfig(name="Front", adi_port=1, smart_port=0)
    after = deepcopy(before)
    after.name = "Back"
    after.adi_port = 4
    after.smart_port = 9

    # smart_port is the expander the strand hangs off, which strands legitimately
    # share, unlike the ADI port and the name.
    assert diff_config(before, after) == [(("smart_port",), 9)]


def test_apply_leaves_unrelated_fields_alone():
    target = StrandConfig(brightness=40)
    target.animation.kind = AnimationKind.PULSE
    target.animation.speed = 7

    apply_changes(target, [(("animation", "color"), 0x123456), (("brightness",), 90)])

    assert target.animation.color == 0x123456
    assert target.brightness == 90
    assert target.animation.kind == AnimationKind.PULSE  # untouched
    assert target.animation.speed == 7  # untouched


def test_lists_are_carried_whole_and_deep_copied():
    before = StrandConfig()
    after = deepcopy(before)
    after.profile_modes = [ModeConfig(name="Alert", priority=200)]
    after.splice.regions = [SpliceRegionConfig(start=2, width=3)]

    changes = diff_config(before, after)
    target = StrandConfig()
    apply_changes(target, changes)

    assert [m.name for m in target.profile_modes] == ["Alert"]
    assert [(r.start, r.width) for r in target.splice.regions] == [(2, 3)]
    # Deep-copied, so grouped strands never end up sharing a mutable object.
    assert target.profile_modes[0] is not after.profile_modes[0]
    assert target.splice.regions[0] is not after.splice.regions[0]


def test_enum_change_round_trips():
    before = StrandConfig()
    after = deepcopy(before)
    after.animation.kind = AnimationKind.TWINKLE

    target = StrandConfig()
    apply_changes(target, diff_config(before, after))

    assert target.animation.kind == AnimationKind.TWINKLE


def test_default_refresh_is_25ms():
    assert StrandConfig().refresh_ms == 25
