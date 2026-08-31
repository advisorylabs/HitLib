"""Checks for the app's "emitted light" pass: bloom effects, the drifting
brand rule, and the canvas's glow/spill.

These are all painted rather than laid out, so nothing here asserts an exact
pixel. What the tests do pin is the part that breaks silently: that a halo is
actually installed (and removed again), that the animation clocks advance, and
that a lit strip really does put color *outside* its own track. A bloom that
gets clipped to the element it came from looks identical to no bloom at all
until someone eyeballs a screenshot.
"""

from PySide6.QtCore import QEvent, QPoint
from PySide6.QtGui import QColor, QEnterEvent, QImage, QPainter, QRegion
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from pattern_studio import theme
from pattern_studio.canvas import StripCanvas
from pattern_studio.models import AnimationKind, StrandConfig
from pattern_studio.session import StrandSession
from pattern_studio.strand_list import StrandListPanel
from pattern_studio.widgets import BrandRule, ColorButton


# sendEvent(), not widget.event(): event filters are run by the application's
# notify(), so calling event() directly would skip the very thing under test.
def _enter(widget):
    pos = QPoint(1, 1)
    QApplication.sendEvent(widget, QEnterEvent(pos, pos, widget.mapToGlobal(pos)))


def _leave(widget):
    QApplication.sendEvent(widget, QEvent(QEvent.Leave))


def _render_on_black(widget) -> QImage:
    """Paint `widget` over black.

    grab() composites a partly transparent widget onto an undefined (in
    practice, light) backing, which inverts the very lightness comparisons
    these tests make. The app always has a dark surface behind it.
    """
    image = QImage(widget.size(), QImage.Format_RGB32)
    image.fill(QColor("black"))
    painter = QPainter(image)
    # DrawChildren only: DrawWindowBackground would fill from the palette
    # first, and these tests don't install the app theme.
    widget.render(painter, QPoint(0, 0), QRegion(), QWidget.RenderFlag.DrawChildren)
    painter.end()
    return image


# ----------------------------------------------------------------------
# theme.bloom / theme.HoverBloom
# ----------------------------------------------------------------------


def test_bloom_is_a_glow_not_a_drop_shadow(qapp):
    button = QPushButton("x")
    effect = theme.bloom(button, theme.ACCENT)
    # A nonzero offset would make it a shadow cast *by* the widget rather than
    # light coming *off* it.
    assert effect.offset().x() == 0 and effect.offset().y() == 0
    assert effect.color().rgb() == QColor(theme.ACCENT).rgb()
    assert button.graphicsEffect() is effect


def test_hover_bloom_attaches_on_enter_and_lets_go_after_leave(qapp):
    """A resting widget must carry no effect: one forces the widget through an
    offscreen buffer, which costs it subpixel text antialiasing."""
    button = QPushButton("Add")
    hover = theme.HoverBloom(button, theme.ACCENT, radius=12)
    assert button.graphicsEffect() is None

    _enter(button)
    assert button.graphicsEffect() is not None

    _leave(button)
    hover._anim.setCurrentTime(hover._anim.duration())  # skip to the end of the ramp
    qapp.processEvents()
    assert button.graphicsEffect() is None


def test_hover_bloom_with_a_resting_glow_stays_attached(qapp):
    button = QPushButton()
    theme.HoverBloom(button, theme.FOCUS, radius=14, resting=6)
    assert button.graphicsEffect() is not None
    _enter(button)
    _leave(button)
    qapp.processEvents()
    assert button.graphicsEffect() is not None


def test_disabling_a_hot_control_drops_its_halo(qapp):
    button = QPushButton("Play")
    hover = theme.HoverBloom(button, theme.FOCUS, radius=12)
    _enter(button)
    button.setEnabled(False)
    hover._anim.setCurrentTime(hover._anim.duration())
    qapp.processEvents()
    assert button.graphicsEffect() is None


def test_color_button_halo_follows_its_swatch(qapp):
    swatch = ColorButton(0xFF0000)
    assert swatch.graphicsEffect().color().rgb() == QColor(0xFF, 0, 0).rgb()
    swatch.set_color(0x00FF00)
    assert swatch.graphicsEffect().color().rgb() == QColor(0, 0xFF, 0).rgb()


# ----------------------------------------------------------------------
# widgets.BrandRule
# ----------------------------------------------------------------------


def test_brand_rule_offset_wraps_within_a_tile(qapp):
    rule = BrandRule()
    rule._offset = rule.TILE_PX - 1.0
    rule._advance()
    assert 0.0 <= rule._offset < rule.TILE_PX


def test_brand_rule_actually_moves(qapp):
    """The sweep must visibly travel: pinned to the window width the gradient
    is shallow enough to read as static."""
    rule = BrandRule()
    rule.resize(400, rule.height())
    before = _render_on_black(rule)
    for _ in range(25):  # one second at FRAME_MS
        rule._advance()
    after = _render_on_black(rule)
    row = range(0, before.width(), 8)
    moved = sum(
        1 for x in row if before.pixelColor(x, 0).rgb() != after.pixelColor(x, 0).rgb()
    )
    assert moved > len(list(row)) // 2, "the gradient barely shifts in a second"


def test_brand_rule_paints_the_wordmark_gradient(qapp):
    rule = BrandRule()
    rule.resize(400, rule.height())
    image = _render_on_black(rule)
    row = [image.pixelColor(x, 0).rgb() for x in range(0, image.width(), 8)]
    assert len(set(row)) > 5, "brand rule is not painting a gradient"
    # The falloff under the crisp line has to be dimmer than the line itself,
    # otherwise it reads as a second rule rather than as spill.
    line = image.pixelColor(image.width() // 2, 0)
    spill = image.pixelColor(image.width() // 2, rule.LINE_H + 1)
    assert spill.lightness() < line.lightness()


def test_brand_rule_only_animates_while_visible(qapp):
    rule = BrandRule()
    assert not rule._timer.isActive()
    rule.show()
    assert rule._timer.isActive()
    rule.hide()
    assert not rule._timer.isActive()


# ----------------------------------------------------------------------
# canvas bloom
# ----------------------------------------------------------------------


def _canvas_with(kind: AnimationKind, color: int = 0x00FF00, length: int = 24):
    canvas = StripCanvas()
    canvas.resize(600, 300)
    cfg = StrandConfig(name="S", length=length)
    cfg.animation.kind = kind
    cfg.animation.color = color
    session = StrandSession(cfg)
    # Pixels stay black until something drives the engine, and an unlit strip
    # is exactly the case where there's no bloom to measure.
    session.strand.tick()
    canvas.set_sessions([session])
    return canvas


def test_spill_bands_ignores_a_dark_row(qapp):
    canvas = _canvas_with(AnimationKind.OFF)
    assert canvas._spill_bands([0x000000] * 20) is None
    assert canvas._spill_bands([]) is None


def test_spill_bands_take_the_brightest_pixel_under_them(qapp):
    """One lit pixel in a dark stretch still throws light; averaging the band
    would wash it out to nothing."""
    canvas = _canvas_with(AnimationKind.OFF)
    pixels = [0x000000] * 24
    pixels[0] = 0xFF0000
    bands = canvas._spill_bands(pixels)
    assert bands is not None
    assert bands[0][1:] == (0xFF, 0, 0)
    assert bands[-1][1:] == (0, 0, 0)


def test_spill_stop_sits_under_the_pixel_casting_it(qapp):
    """The wash has to line up with its source. Anchoring a band's stop at the
    band's midpoint instead put the glow visibly to one side of a lone lit
    LED on long strands."""
    canvas = _canvas_with(AnimationKind.OFF)
    n = 200  # long enough that several pixels share a band
    pixels = [0x000000] * n
    pixels[41] = 0x0000FF
    bands = canvas._spill_bands(pixels)
    lit = [pos for pos, r, g, b in bands if max(r, g, b) > 0]
    assert lit, "the lit pixel cast nothing"
    # Within half a pixel of the LED's own center.
    assert abs(lit[0] - (41 + 0.5) / n) < 0.5 / n


def test_lit_strip_throws_light_past_its_own_track(qapp):
    """The spill pass puts color outside the bezel."""
    canvas = _canvas_with(AnimationKind.SOLID, color=0x00FF00)
    image = _render_on_black(canvas)
    unlit = _render_on_black(_canvas_with(AnimationKind.OFF))

    # Sample the column down the middle of the canvas and take the greenest
    # pixel that is *not* inside a track, i.e. purely spill.
    x = image.width() // 2
    lit_green = max(image.pixelColor(x, y).green() for y in range(image.height()))
    assert lit_green > 0
    spill = [
        image.pixelColor(x, y).green() - unlit.pixelColor(x, y).green()
        for y in range(image.height())
        if image.pixelColor(x, y).green() < lit_green // 3
    ]
    assert max(spill) > 4, "no measurable glow outside the LED bodies"


def test_group_selection_drives_the_pulse_timer(qapp):
    canvas = _canvas_with(AnimationKind.SOLID)
    assert not canvas._pulse_timer.isActive()
    canvas.set_selected([0, 1])
    assert canvas._pulse_timer.isActive()
    # A single selection draws no outline, so nothing needs repainting.
    canvas.set_selected([0])
    assert not canvas._pulse_timer.isActive()


def test_group_outline_glows_rather_than_just_drawing_a_line(qapp):
    """A 1px cyan rectangle is a border; what marks a group is that the
    border throws light. Measured against the same canvas unselected, so the
    only difference is the outline and its halo."""
    canvas = _canvas_with(AnimationKind.OFF)
    canvas.set_selected(())
    plain = _render_on_black(canvas)
    # Two indices so the canvas treats it as a group even with one strand.
    canvas.set_selected([0, 1])
    lit = _render_on_black(canvas)

    x = lit.width() // 2
    gains = [
        lit.pixelColor(x, y).blue() - plain.pixelColor(x, y).blue()
        for y in range(lit.height())
    ]
    assert max(gains) > 60, "the selection outline barely registers"
    # The line itself is under 2px; anything past that is halo.
    assert sum(1 for g in gains if g > 8) >= 8, "the outline has no falloff"


class _FakeClock:
    """Stand-in for the canvas's QElapsedTimer. There's no way to set a real
    one, and sleeping through a 2.6s cycle in a unit test is not an option."""

    def __init__(self):
        self.ms = 0

    def start(self):
        pass

    def elapsed(self):
        return self.ms


def test_pulse_breathes_between_its_endpoints(qapp):
    canvas = _canvas_with(AnimationKind.SOLID)
    clock = _FakeClock()
    canvas._clock = clock

    assert canvas._pulse() < 0.01
    clock.ms = canvas.PULSE_MS // 2
    assert canvas._pulse() > 0.99
    # Several cycles in it's still breathing, not stuck at an endpoint.
    clock.ms = canvas.PULSE_MS * 3
    assert canvas._pulse() < 0.01


def test_canvas_still_renders_with_no_strands(qapp):
    canvas = StripCanvas()
    canvas.resize(400, 200)
    assert not canvas.grab().isNull()


# ----------------------------------------------------------------------
# selected strand rows
# ----------------------------------------------------------------------


def _list_panel(selected: int) -> tuple[StrandListPanel, QImage]:
    panel = StrandListPanel()
    # Fixed, not resize(): the "N selected" label appears with a group and
    # would otherwise widen the panel, so the two renders wouldn't line up.
    panel.setFixedSize(200, 160)
    panel.set_names(["Strand 1", "Strand 2", "Strand 3"])
    if selected > 1:
        panel.list_widget.selectAll()
    else:
        panel.select(0)
    panel.set_group_size(selected)
    panel.show()
    QApplication.processEvents()
    return panel, _render_on_black(panel)


def _row_scanline(panel: StrandListPanel, image: QImage, row: int) -> list[QColor]:
    rect = panel.list_widget.visualItemRect(panel.list_widget.item(row))
    origin = panel.list_widget.mapTo(panel, rect.topLeft())
    y = origin.y() + rect.height() // 2
    # Past the accent bar and the label, out to wherever the row is cut off by
    # the panel's own width.
    x0 = origin.x() + 80
    x1 = min(origin.x() + rect.width(), image.width()) - 4
    return [image.pixelColor(x, y) for x in range(x0, x1)]


def _widest_flat_run(values) -> int:
    runs, current = [], 1
    for a, b in zip(values, values[1:]):
        if a == b:
            current += 1
        else:
            runs.append(current)
            current = 1
    runs.append(current)
    return max(runs)


def test_selected_row_wash_has_no_visible_stepping(themed):
    """The complaint this delegate exists to fix: a QSS gradient is rounded to
    8 bits with no dithering, and the channel that travels least across the row
    lands as a few very wide flat bands with hard edges between them."""
    panel, image = _list_panel(selected=1)
    row = _row_scanline(panel, image, 0)
    assert len(row) > 40
    for channel, name in ((0, "red"), (1, "green"), (2, "blue")):
        widest = _widest_flat_run([c.getRgb()[channel] for c in row])
        assert widest <= 8, f"{name} steps in {widest}px-wide bands"


def test_group_selection_rings_its_rows(themed):
    """The group bloom has to land *around* a row, not on it: filled halos
    left unclipped would just double the wash's strength.

    Compared against the same three-row selection with the bloom switched
    off, so the only difference in the two renders is the halo itself.
    """
    lit_panel, lit = _list_panel(selected=3)
    plain_panel, _ = _list_panel(selected=3)
    plain_panel._row_delegate.set_group(False)
    plain = _render_on_black(plain_panel)

    rect = lit_panel.list_widget.visualItemRect(lit_panel.list_widget.item(0))
    origin = lit_panel.list_widget.mapTo(lit_panel, rect.topLeft())
    x = origin.x() + rect.width() // 3
    column = range(origin.y(), min(origin.y() + 3 * rect.height(), lit.height()))

    gained = [
        lit.pixelColor(x, y).red() - plain.pixelColor(x, y).red() for y in column
    ]
    # Light in the gaps between rows...
    assert max(gained) > 4, "the group bloom adds nothing around the rows"
    # ...and none of it spread across the row bodies.
    assert sum(1 for g in gained if g > 2) < len(gained) // 2
