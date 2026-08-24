"""Field-level diff/apply behind editing several strands as one group.

The inspector only ever displays one strand (the *anchor*) so writing its
whole config into every selected strand would clobber the properties they were
never meant to share (a rainbow strand would suddenly become a pulse strand
just because the anchor was one). Instead MainWindow keeps a baseline copy of
the anchor taken *before* the edit, diffs it afterwards, and replays only the
leaf fields that actually changed onto the other selected strands.

Diffing rather than instrumenting every widget is deliberate: the panels
mutate nested lists (profile modes, phases, splice regions) in place as the
user edits, not just in save(), so "which field changed?" isn't something the
signal wiring can answer on its own.
"""

from __future__ import annotations

import copy
from dataclasses import fields, is_dataclass
from typing import Any

#: A dotted path from a StrandConfig down to one leaf field, e.g.
#: ("animation", "color") or ("splice", "overlay", "speed").
FieldPath = tuple[str, ...]
FieldChange = tuple[FieldPath, Any]

#: Top-level StrandConfig fields that are per-strand identity and must never
#: be copied across a group edit: two strands can't share an ADI port, and one
#: shared name would make the sidebar unreadable. Nested `name` fields (a
#: mode's, a phase's) are shared content and do propagate.
NON_SHARED_FIELDS = frozenset({"name", "adi_port"})


def diff_config(before: Any, after: Any) -> list[FieldChange]:
    """Return the leaf fields that differ between two configs of the same type.

    Nested dataclasses are walked recursively so a color tweak reports just
    ("animation", "color") rather than the whole AnimationConfig. Lists
    (palette, profile_modes, splice regions...) are compared (and carried)
    whole, since there's no stable identity to match their elements up by.
    """
    return _diff(before, after, (), top=True)


def _diff(before: Any, after: Any, path: FieldPath, top: bool) -> list[FieldChange]:
    changes: list[FieldChange] = []
    for f in fields(after):
        if top and f.name in NON_SHARED_FIELDS:
            continue
        old = getattr(before, f.name)
        new = getattr(after, f.name)
        sub_path = path + (f.name,)
        if is_dataclass(old) and is_dataclass(new) and type(old) is type(new):
            changes.extend(_diff(old, new, sub_path, top=False))
        elif old != new:
            changes.append((sub_path, copy.deepcopy(new)))
    return changes


def apply_changes(config: Any, changes: list[FieldChange]) -> None:
    """Write `changes` (from diff_config) into `config`, leaving every other
    field alone. Values are deep-copied so grouped strands never end up
    sharing a mutable list or nested config object.
    """
    for path, value in changes:
        target = config
        for attr in path[:-1]:
            target = getattr(target, attr)
        setattr(target, path[-1], copy.deepcopy(value))
