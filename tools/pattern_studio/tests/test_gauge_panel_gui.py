"""The Gauge region's editor and the Divide action, driven through the actual
widgets rather than by mutating config directly.

The design these are about - a strip under a drivebase split into one gauge per
motor - is only worth having in the GUI if it can be built there without
arithmetic, so what these check is mostly that: pick a source, press Divide,
change six ports.
"""

import pytest

from pattern_studio import fill_sources
from pattern_studio.inspector import MASK_SPLICE
from pattern_studio.main_window import MainWindow
from pattern_studio.models import (
    Document,
    GaugeStyleKind,
    OverlayAnimationKind,
    SpliceModeKind,
)
from pattern_studio.serialization import document_from_dict, document_to_dict


@pytest.fixture
def win(qapp):
    """One window per test, taken back down afterwards.

    Every StrandSession owns a running QTimer and the canvas owns another, so a
    window left alive keeps ticking and repainting a strand belonging to a test
    that finished long ago. A dozen of those in one file is enough to bring the
    whole run down inside the painter.
    """
    window = MainWindow()
    window.show()
    yield window
    window.close()
    window.deleteLater()
    qapp.processEvents()


def _custom_splice(win: MainWindow):
    """The splice panel, switched to Custom mode with one region to edit."""
    masks = win.inspector.masks_panel
    masks.mask_kind_combo.setCurrentIndex(masks.mask_kind_combo.findData(MASK_SPLICE))
    panel = masks.splice_panel
    panel.mode_combo.setCurrentIndex(panel.mode_combo.findData(SpliceModeKind.CUSTOM))
    panel._add_region()
    panel.region_list.setCurrentRow(0)
    return panel


def _make_gauge(panel):
    anim = panel.region_anim_panel
    anim.kind_combo.setCurrentIndex(anim.kind_combo.findData(OverlayAnimationKind.GAUGE))
    return anim


def test_a_region_can_be_a_gauge_but_a_shared_overlay_cannot(win, qapp):
    """Split mode's overlay is one buffer for every masked bin, so a gauge
    there would be a single meter smeared across all of them."""
    panel = _custom_splice(win)

    assert panel.region_anim_panel.kind_combo.findData(OverlayAnimationKind.GAUGE) >= 0
    assert panel.overlay_panel.kind_combo.findData(OverlayAnimationKind.GAUGE) < 0


def test_picking_motor_temperature_brings_the_whole_scale_with_it(win, qapp):
    """The point of the preset: six stops at the temperatures the motor itself
    changes behaviour at, without typing any of them."""
    anim = _make_gauge(_custom_splice(win))

    anim.source_combo.setCurrentIndex(anim.source_combo.findData("motor_temp"))
    qapp.processEvents()

    stops = anim.stops_editor.stops()
    assert [stop.at for stop in stops] == [20, 45, 55, 60, 65, 70]
    assert stops[0].color == 0x00FF00   # cold
    assert stops[-1].color == 0xFF00FF  # shut down
    assert anim.source_empty_spin.value() == 20
    assert anim.source_full_spin.value() == 70
    # And the rows say what the numbers mean.
    assert "C" in anim.source_full_spin.suffix()


def test_a_source_with_no_scale_of_its_own_leaves_the_stops_empty(win, qapp):
    """Most readings do not have meaningful thresholds, and a made-up scale
    would be worse than the two-color fallback."""
    anim = _make_gauge(_custom_splice(win))

    anim.source_combo.setCurrentIndex(anim.source_combo.findData("battery"))
    qapp.processEvents()

    assert anim.stops_editor.stops() == []
    # With no scale, Color / Color 2 are the scale, so they are visible.
    assert anim.color_btn.isVisible()


def test_a_scale_hides_the_two_fallback_colors(win, qapp):
    anim = _make_gauge(_custom_splice(win))

    anim.source_combo.setCurrentIndex(anim.source_combo.findData("motor_temp"))
    qapp.processEvents()

    assert not anim.color_btn.isVisible()
    assert not anim.color2_btn.isVisible()


def test_whole_segment_style_hides_the_bar_only_fields(win, qapp):
    anim = _make_gauge(_custom_splice(win))
    qapp.processEvents()

    # A whole-segment gauge covers every pixel it owns: no unlit part to color,
    # no direction to reverse.
    assert not anim.invert_check.isVisible()
    assert not anim.bg_btn.isVisible()

    anim.style_combo.setCurrentIndex(anim.style_combo.findData(GaugeStyleKind.BAR))
    qapp.processEvents()
    assert anim.invert_check.isVisible()
    assert anim.bg_btn.isVisible()


def test_a_hand_driven_gauge_keeps_its_range_but_drops_the_reader_fields(win, qapp):
    """Unlike a Fill meter's, a gauge's Empty At / Full At still matter with no
    reader: they are what place the color stops."""
    anim = _make_gauge(_custom_splice(win))

    anim.source_combo.setCurrentIndex(anim.source_combo.findData(fill_sources.MANUAL))
    qapp.processEvents()

    assert anim.source_empty_spin.isVisible()
    assert anim.source_full_spin.isVisible()
    assert not anim.smoothing_spin.isVisible()
    assert not anim.source_wrap_check.isVisible()


def test_divide_lays_out_one_segment_per_motor(win, qapp):
    """The arithmetic nobody should have to do: 60 pixels, six motors."""
    win.inspector.strand_panel.length_spin.setValue(60)
    qapp.processEvents()
    panel = _custom_splice(win)
    panel.set_strip_length(60)

    panel.divide_count_spin.setValue(6)
    panel.divide_gap_check.setChecked(True)
    panel.divide_btn.click()
    qapp.processEvents()

    regions = panel._splice.regions
    assert [r.start for r in regions] == [0, 10, 20, 30, 40, 50]
    # A dark pixel between each pair, so two neighbouring segments at similar
    # levels don't read as one long one.
    assert [r.width for r in regions] == [9] * 6


def test_divide_without_a_gap_covers_every_pixel(win, qapp):
    panel = _custom_splice(win)
    panel.set_strip_length(30)

    panel.divide_count_spin.setValue(4)
    panel.divide_gap_check.setChecked(False)
    panel.divide_btn.click()
    qapp.processEvents()

    regions = panel._splice.regions
    # 30 does not divide by 4, so the remainder goes to the first segments and
    # nothing is stranded at the end.
    assert [r.width for r in regions] == [8, 8, 7, 7]
    assert regions[-1].start + regions[-1].width == 30


def test_divide_copies_the_first_segment_onto_the_new_ones(win, qapp):
    """Set one segment up the way you want it, then divide - which is the flow
    that makes six gauges a two-minute job instead of a twelve-field one."""
    panel = _custom_splice(win)
    panel.set_strip_length(60)
    anim = _make_gauge(panel)
    anim.source_combo.setCurrentIndex(anim.source_combo.findData("motor_temp"))
    qapp.processEvents()

    panel.divide_count_spin.setValue(6)
    panel.divide_btn.click()
    qapp.processEvents()

    regions = panel._splice.regions
    assert len(regions) == 6
    for region in regions:
        assert region.animation.kind == OverlayAnimationKind.GAUGE
        assert region.animation.source == "motor_temp"
        assert len(region.animation.stops) == 6
    # Copies, not shares - changing one segment's port must not change five.
    regions[0].animation.source_port = 11
    assert regions[1].animation.source_port != 11


def test_divide_keeps_the_ports_already_set_on_each_segment(win, qapp):
    """Re-dividing after a length change re-spaces the segments without losing
    six configured motor ports."""
    panel = _custom_splice(win)
    panel.set_strip_length(60)
    _make_gauge(panel)
    panel.divide_count_spin.setValue(3)
    panel.divide_btn.click()
    for i, port in enumerate((1, 2, 3)):
        panel._splice.regions[i].animation.source_port = port

    panel.set_strip_length(30)
    panel.divide_btn.click()
    qapp.processEvents()

    regions = panel._splice.regions
    assert [r.animation.source_port for r in regions] == [1, 2, 3]
    assert [r.start for r in regions] == [0, 10, 20]


def test_a_designed_gauge_survives_save_and_load(win, qapp):
    panel = _custom_splice(win)
    anim = _make_gauge(panel)
    anim.source_combo.setCurrentIndex(anim.source_combo.findData("motor_temp"))
    anim.source_port_spin.setValue(11)
    anim.style_combo.setCurrentIndex(anim.style_combo.findData(GaugeStyleKind.BAR))
    qapp.processEvents()

    # Region edits mutate the loaded config in place, which is the session's,
    # so the document is already current - same as File > Save builds it.
    document = Document(strands=[session.config for session in win.sessions])
    restored = document_from_dict(document_to_dict(document))
    region = restored.strands[0].splice.regions[0]

    assert region.animation.kind == OverlayAnimationKind.GAUGE
    assert region.animation.source == "motor_temp"
    assert region.animation.source_port == 11
    assert region.animation.style == GaugeStyleKind.BAR
    assert [stop.at for stop in region.animation.stops] == [20, 45, 55, 60, 65, 70]
