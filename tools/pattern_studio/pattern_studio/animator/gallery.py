"""What the Animator opens with when the library is empty.

An empty layer stack is the worst possible first screen: it asks the user to
know what a layer is before they can see one. So the first thing they get is
a grid of cards, each with a live thumbnail actually ticking the real
composition - the animation is the label.

Three kinds of card, in this order on purpose:

  * **From a preset** - every animation they already know, now as layers.
    The point being made is that a preset *is* a custom animation, so there
    is nothing new to learn before adding a second layer to one.
  * **Something new** - the things the preset vocabulary cannot express.
    Without these the tab reads as the old dropdown in a new shape.
  * The Solid Color preset doubles as the blank start - it is one solid
    layer, which is exactly what a blank animation would be.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import presets, theme
from ..canvas import StripCanvas
from ..models import (
    AnimationConfig,
    AnimationKind,
    BindingConfig,
    BitScrollSegmentConfig,
    CompositionConfig,
    DriverConfig,
    DriverKind,
    LayerBlend,
    LayerConfig,
    LayerKind,
    LayerParam,
    LfoShape,
    StrandConfig,
)
from ..session import StrandSession

#: The thumbnails run at this length - short enough to read as a swatch.
THUMB_LENGTH = 16


def preset_kinds() -> list[AnimationKind]:
    """The presets offered as starting points, in dropdown order.

    Shared with the Animator's Preset menu so the two offer the same list.
    Off is left out - there is nothing to see or build on - and so is Custom,
    which is already a composition.
    """
    return [kind for kind in AnimationKind
            if kind not in (AnimationKind.CUSTOM, AnimationKind.OFF)]


def starters() -> list[tuple[str, str, CompositionConfig]]:
    """(title, what it shows off, composition) for the "something new" row.

    Each one exists to demonstrate a mechanism a preset has no way to reach,
    so that the gallery makes the case for the tab rather than just filling
    it. Built fresh on every call, so a caller can take the layers without
    sharing them with anyone else. The Preset menu offers the same list.
    """
    out: list[tuple[str, str, CompositionConfig]] = []

    out.append((
        "Alliance Chase",
        "Two colours in one scrolling pattern - a preset bitscroll only has one",
        CompositionConfig(name="Alliance Chase", layers=[
            LayerConfig(name="chase", kind=LayerKind.BITSCROLL, speed=1, spacing=3,
                        segments=[BitScrollSegmentConfig(0xFF0000, 3),
                                  BitScrollSegmentConfig(0x0000FF, 3)]),
        ]),
    ))

    out.append((
        "Breathing Wash",
        "An oscillator driving a layer's opacity",
        CompositionConfig(name="Breathing Wash",
            drivers=[DriverConfig(name="Breathe", kind=DriverKind.LFO,
                                  shape=LfoShape.SINE, period_ms=2600)],
            layers=[
                LayerConfig(name="base", kind=LayerKind.SOLID, color=0x050010),
                LayerConfig(name="wash", kind=LayerKind.FLOW, color=0x8000FF,
                            color2=0x00A0FF, speed=1, blend=LayerBlend.KEY,
                            bindings=[BindingConfig(param=LayerParam.OPACITY,
                                                    driver=0, at_zero=30, at_full=255)]),
            ]),
    ))

    out.append((
        "Sparkle Over Wash",
        "Two layers at once - the sparkle keys over the wash instead of hiding it",
        CompositionConfig(name="Sparkle Over Wash", layers=[
            LayerConfig(name="wash", kind=LayerKind.FLOW, color=0x001040,
                        color2=0x004080, speed=1),
            LayerConfig(name="sparks", kind=LayerKind.TWINKLE, blend=LayerBlend.KEY,
                        palette=[0xFFFFFF, 0x88CCFF], density_pct=25, fade_step=20),
        ]),
    ))

    out.append((
        "Endgame Alert",
        "A timeline: two layers taking turns, with a crossfade between them",
        CompositionConfig(name="Endgame Alert", loop_ms=2000, layers=[
            LayerConfig(name="calm", kind=LayerKind.SOLID, color=0x00FF40,
                        start_ms=0, end_ms=1000, fade_out_ms=250),
            LayerConfig(name="alert", kind=LayerKind.FLASH, color=0xFF2000,
                        on_ms=120, off_ms=120, start_ms=1000, end_ms=0,
                        fade_in_ms=250),
        ]),
    ))

    out.append((
        "Split Ends",
        "One layer repeated along the strip, instead of four separate regions",
        CompositionConfig(name="Split Ends", layers=[
            LayerConfig(name="base", kind=LayerKind.SOLID, color=0x101010),
            LayerConfig(name="marks", kind=LayerKind.SOLID, color=0xFFAA00,
                        start=0, width=2, stride=5, blend=LayerBlend.KEY),
        ]),
    ))

    out.append((
        "Hand Drawn",
        "A drawing, frame by frame - across is pixel, down is time",
        CompositionConfig(name="Hand Drawn", layers=[
            LayerConfig(name="base", kind=LayerKind.SOLID, color=0x000008),
            LayerConfig(name="drawn", kind=LayerKind.FRAMES, blend=LayerBlend.KEY,
                        frame_width=8, frame_ms=120,
                        frame_palette=[0x000000, 0xFF0080, 0x00E0FF],
                        frames=[[0, 1, 1, 0, 0, 2, 2, 0],
                                [1, 1, 0, 0, 2, 2, 0, 0],
                                [1, 0, 0, 2, 2, 0, 0, 1],
                                [0, 0, 2, 2, 0, 0, 1, 1]]),
        ]),
    ))
    return out


class _Card(QWidget):
    """One template, with its animation actually running on it."""

    picked = Signal(object)

    def __init__(self, title: str, subtitle: str, comp: CompositionConfig, parent=None):
        super().__init__(parent)
        self._comp = comp
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("role", "card")

        name = QLabel(title)
        name.setProperty("role", "sectionHeader")
        note = QLabel(subtitle)
        note.setProperty("role", "hint")
        note.setWordWrap(True)

        self.canvas = StripCanvas()
        self.canvas.setMinimumHeight(40)
        self.canvas.setMaximumHeight(52)

        cfg = StrandConfig(name=title, length=THUMB_LENGTH,
                           animation=AnimationConfig(kind=AnimationKind.CUSTOM,
                                                     composition_id=comp.id))
        self.session = StrandSession(cfg, parent=self, compositions={comp.id: comp})
        self.session.ticked.connect(self.canvas.update)
        self.canvas.set_sessions([self.session])
        self.session.start()

        box = QVBoxLayout(self)
        box.setContentsMargins(8, 8, 8, 8)
        box.setSpacing(4)
        box.addWidget(name)
        box.addWidget(self.canvas)
        box.addWidget(note)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.LeftButton:
            self.picked.emit(self._comp)

    def stop(self) -> None:
        self.session.stop()


class TemplateGalleryDialog(QDialog):
    """The first screen of an empty Animator."""

    def __init__(self, next_id, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Start an Animation")
        self.setModal(True)
        self._chosen: CompositionConfig | None = None
        self._cards: list[_Card] = []

        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setSpacing(8)

        entries: list[tuple[str, str, CompositionConfig]] = []
        for kind in preset_kinds():
            comp = presets.composition_for_animation(AnimationConfig(kind=kind))
            entries.append((comp.name, "The preset, as a layer you can add to", comp))
        entries.extend(starters())
        # No separate "Blank" card: the Solid Color preset above already *is*
        # one solid layer, and two cards that make the same thing is a choice
        # nobody can make correctly.

        for i, (title, note, comp) in enumerate(entries):
            comp.id = next_id()
            card = _Card(title, note, comp)
            card.picked.connect(self._choose)
            self._cards.append(card)
            grid.addWidget(card, i // 3, i % 3)

        scroll = QScrollArea()
        scroll.setWidget(grid_host)
        scroll.setWidgetResizable(True)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll, 1)
        layout.addWidget(buttons)
        self.resize(760, 540)

    def _choose(self, comp: CompositionConfig) -> None:
        self._chosen = comp
        self.accept()

    def chosen(self) -> CompositionConfig | None:
        return self._chosen

    def done(self, result: int) -> None:  # noqa: D102 (Qt override)
        # Every card carries a running QTimer. Left alone they would keep
        # ticking after the dialog closed, which is the same leak
        # conftest._close_windows exists to clean up after.
        for card in self._cards:
            card.stop()
        super().done(result)
