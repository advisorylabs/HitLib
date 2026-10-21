"""Undo and redo for the whole document.

Pattern Studio's panels edit configs *in place* and then emit `changed` -
`ModesPanel.save()` is literally a `pass` with a comment saying the mutation
has already happened. That is a reasonable shape for a form, and rewriting
every panel into command objects would touch 1,592 lines of inspector.py and
415 of modes_panel.py to change nothing a user can see.

So this takes snapshots instead. Before an edit the window holds a copy of
the document's editable state; after one it takes another and pushes a
command that swaps between them. The panels stay exactly as they are.

Two things make that affordable:

  * **What is snapshotted is small.** Strands and compositions are plain
    dataclasses - a few hundred bytes each. The song is deliberately left
    out: its baked analysis is the one genuinely large thing in a document,
    loading a file is not an edit anyone expects Ctrl+Z to reverse, and
    re-baking it would take longer than the keystroke that triggered it.
  * **Bursts collapse.** Dragging a spin box fires a change per step. Each
    command carries an epoch, and Qt merges consecutive commands that share
    one, so a drag is a single undo step. The window bumps the epoch when the
    user pauses or moves to something else - see MainWindow._bump_undo_epoch.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from PySide6.QtGui import QUndoCommand

from .models import CompositionConfig, StrandConfig


@dataclass
class Snapshot:
    """The part of a document that an edit can change, copied.

    `current` and `selected` ride along so undo puts the user back where they
    were: reversing an edit to strand 3 and landing on strand 1 makes it look
    as though nothing happened.
    """

    strands: list[StrandConfig] = field(default_factory=list)
    compositions: list[CompositionConfig] = field(default_factory=list)
    current: int = -1
    selected: list[int] = field(default_factory=list)

    @staticmethod
    def of(strands: list[StrandConfig], compositions: list[CompositionConfig],
           current: int, selected: list[int]) -> "Snapshot":
        return Snapshot(
            strands=deepcopy(strands),
            compositions=deepcopy(compositions),
            current=current,
            selected=list(selected),
        )

    def same_content_as(self, other: "Snapshot") -> bool:
        """Whether the *document* matches, ignoring where the cursor was.

        Selection is carried for restore but must not make an edit out of
        nothing: clicking between strands would otherwise fill the undo stack
        with steps that change no pixels.
        """
        return self.strands == other.strands and self.compositions == other.compositions


class DocumentEdit(QUndoCommand):
    """One undoable change, as a before and an after snapshot."""

    def __init__(self, window, text: str, before: Snapshot, after: Snapshot, epoch: int):
        super().__init__(text)
        self._window = window
        self._before = before
        self._after = after
        self._epoch = epoch
        # QUndoStack.push() calls redo() immediately, but the edit this
        # command describes has already been made by the widget that made it.
        # Re-applying it here would be harmless but would rebuild every engine
        # strand for nothing, visibly restarting the animations mid-edit.
        self._pushed = False

    def undo(self) -> None:
        self._window.restore_snapshot(self._before)

    def redo(self) -> None:
        if not self._pushed:
            self._pushed = True
            return
        self._window.restore_snapshot(self._after)

    def id(self) -> int:  # noqa: A003 (Qt's name)
        return self._epoch

    def mergeWith(self, other) -> bool:  # noqa: N802 (Qt override)
        """Fold a later edit from the same burst into this one.

        Qt only offers commands whose id() matches, so this is reached exactly
        when both belong to the same epoch. The merged command keeps the
        earliest `before` and the latest `after`, which is what makes one drag
        one undo step.

        A burst can end where it began - a drag cancelled with Esc puts the
        original back. That is no step at all, and marking it obsolete has Qt
        drop it rather than leave a Ctrl+Z that changes nothing.
        """
        if not isinstance(other, DocumentEdit) or other._epoch != self._epoch:
            return False
        self._after = other._after
        self.setObsolete(self._before.same_content_as(self._after))
        return True
