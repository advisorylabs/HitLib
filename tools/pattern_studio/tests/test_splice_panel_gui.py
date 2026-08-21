"""End-to-end checks for the splice-mask editor, driven through the actual
widgets (not by mutating config directly).
"""

from pattern_studio.main_window import MainWindow
from pattern_studio.models import AnimationKind, OverlayAnimationKind, SpliceModeKind


def test_split_mode_animation_content_reveals_overlay_in_masked_bins(qapp):
    win = MainWindow()
    win.show()
    inspector = win.inspector
    splice = inspector.splice_panel

    inspector.strand_panel.length_spin.setValue(4)
    inspector.strand_panel.changed.emit()
    qapp.processEvents()

    idx = inspector.anim_panel.kind_combo.findData(AnimationKind.RAINBOW)
    inspector.anim_panel.kind_combo.setCurrentIndex(idx)
    qapp.processEvents()

    splice.setChecked(True)
    splice.sections_spin.setValue(1)  # two halves
    idx = splice.split_content_combo.findData(True)  # "Animation"
    splice.split_content_combo.setCurrentIndex(idx)
    qapp.processEvents()

    assert splice.overlay_panel.isVisible()
    idx = splice.overlay_panel.kind_combo.findData(OverlayAnimationKind.SOLID)
    splice.overlay_panel.kind_combo.setCurrentIndex(idx)
    splice.overlay_panel.color_btn.set_color(0x00FF00)
    splice.overlay_panel.color_btn.color_changed.emit(0x00FF00)
    qapp.processEvents()

    strand = win.sessions[0].strand
    strand.tick()
    # First half is masked out and should show the overlay solid color;
    # this only works now that the overlay display no longer requires
    # CENTER_SPREAD to be the active base animation.
    assert strand.pixels[0] == 0x00FF00
    assert strand.pixels[1] == 0x00FF00


def test_custom_mode_add_region_updates_config_and_list(qapp):
    win = MainWindow()
    win.show()  # isVisible() reflects the whole ancestor chain, not just setVisible() on the widget itself
    inspector = win.inspector
    splice = inspector.splice_panel

    splice.setChecked(True)
    idx = splice.mode_combo.findData(SpliceModeKind.CUSTOM)
    splice.mode_combo.setCurrentIndex(idx)
    qapp.processEvents()

    assert splice.custom_widget.isVisible()
    assert not splice.split_widget.isVisible()

    splice._add_region()
    qapp.processEvents()
    splice.region_start_spin.setValue(2)
    splice.region_width_spin.setValue(3)
    qapp.processEvents()

    cfg = win.sessions[0].config
    assert cfg.splice.mode == SpliceModeKind.CUSTOM
    assert len(cfg.splice.regions) == 1
    assert cfg.splice.regions[0].start == 2
    assert cfg.splice.regions[0].width == 3
    assert splice.region_list.count() == 1
    assert splice.region_list.item(0).text() == "2-4  Solid Color"


def test_custom_region_has_its_own_independent_animation_editor(qapp):
    win = MainWindow()
    win.show()
    inspector = win.inspector
    splice = inspector.splice_panel

    splice.setChecked(True)
    idx = splice.mode_combo.findData(SpliceModeKind.CUSTOM)
    splice.mode_combo.setCurrentIndex(idx)
    splice._add_region()
    qapp.processEvents()

    # Split's shared overlay panel is unrelated to Custom mode -- it stays hidden.
    assert not splice.overlay_panel.isVisible()
    assert splice.region_anim_panel.isVisible()

    idx = splice.region_anim_panel.kind_combo.findData(OverlayAnimationKind.RAINBOW)
    splice.region_anim_panel.kind_combo.setCurrentIndex(idx)
    splice.region_anim_panel.speed_spin.setValue(3)
    splice.region_anim_panel.speed_spin.valueChanged.emit(3)
    qapp.processEvents()

    region = win.sessions[0].config.splice.regions[0]
    assert region.animation.kind == OverlayAnimationKind.RAINBOW
    assert region.animation.speed == 3
    assert splice.region_list.item(0).text().endswith("Rainbow")


def test_two_custom_regions_keep_independent_animations(qapp):
    win = MainWindow()
    win.show()
    inspector = win.inspector
    splice = inspector.splice_panel

    splice.setChecked(True)
    idx = splice.mode_combo.findData(SpliceModeKind.CUSTOM)
    splice.mode_combo.setCurrentIndex(idx)
    splice._add_region()
    qapp.processEvents()
    idx = splice.region_anim_panel.kind_combo.findData(OverlayAnimationKind.RAINBOW)
    splice.region_anim_panel.kind_combo.setCurrentIndex(idx)
    qapp.processEvents()

    splice._add_region()
    qapp.processEvents()
    idx = splice.region_anim_panel.kind_combo.findData(OverlayAnimationKind.FLOW)
    splice.region_anim_panel.kind_combo.setCurrentIndex(idx)
    qapp.processEvents()

    regions = win.sessions[0].config.splice.regions
    assert regions[0].animation.kind == OverlayAnimationKind.RAINBOW
    assert regions[1].animation.kind == OverlayAnimationKind.FLOW


def test_remove_region_clears_editor_when_list_empties(qapp):
    win = MainWindow()
    inspector = win.inspector
    splice = inspector.splice_panel

    splice.setChecked(True)
    idx = splice.mode_combo.findData(SpliceModeKind.CUSTOM)
    splice.mode_combo.setCurrentIndex(idx)
    splice._add_region()
    qapp.processEvents()

    splice.region_list.setCurrentRow(0)
    splice._remove_region()
    qapp.processEvents()

    assert splice.region_list.count() == 0
    assert not splice.region_editor.isEnabled()
    assert win.sessions[0].config.splice.regions == []
