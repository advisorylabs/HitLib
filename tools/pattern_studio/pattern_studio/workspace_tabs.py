"""The Design / Animate switch, in the title bar.

Not a QTabWidget and not a bare QTabBar. theme.py styles no QTabBar at all,
and window_chrome draws a hand-rolled frameless caption, so a stock tab strip
would read as a foreign control sitting under the brand rule - and a
QTabWidget would draw a second border immediately beneath one that is already
there.

What it is instead: two checkable buttons in a QButtonGroup, placed in the
caption row right of the menus, where there is already empty space. Flat
fills only, per the house rule - the selected tab is marked with a cyan
underline rather than a graded fill. No glow and no press movement: these
sit beside the menu bar, which has neither.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QWidget


#: Page indices into MainWindow's QStackedWidget. Named so nothing has to
#: remember which of the two came first.
DESIGN = 0
ANIMATE = 1


class WorkspaceTabs(QWidget):
    """Switches the window between the strand designer and the Animator."""

    changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("workspaceTabs")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 0, 0)
        layout.setSpacing(2)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: list[QPushButton] = []

        for index, (label, tip) in enumerate((
            ("Design", "Set up strands and pick their animations"),
            ("Animate", "Build custom animations out of layers"),
        )):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setProperty("role", "segment")
            button.setToolTip(tip)
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            # No bloom and no press movement. These sit in the caption row
            # next to the menus, which do neither, and a tab that glows and
            # sinks reads as a toy beside them. The selected one is marked
            # by a cyan underline in the stylesheet instead.
            self.group.addButton(button, index)
            self.buttons.append(button)
            layout.addWidget(button)

        self.buttons[DESIGN].setChecked(True)
        self.group.idClicked.connect(self.changed)

    def current(self) -> int:
        return self.group.checkedId()

    def set_current(self, index: int) -> None:
        """Switch tabs without emitting `changed` - for code that is already
        doing whatever the signal would have triggered."""
        if 0 <= index < len(self.buttons):
            self.buttons[index].setChecked(True)

    def go_to(self, index: int) -> None:
        """Switch tabs and tell the window, as a click would."""
        if 0 <= index < len(self.buttons) and index != self.current():
            self.buttons[index].setChecked(True)
            self.changed.emit(index)
