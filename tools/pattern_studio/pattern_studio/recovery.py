"""Autosave, and bringing an autosave back after Pattern Studio did not close
properly.

Each open window writes its unsaved design into a slot of its own under
recovery_dir(): `<id>.hlprofile`, beside `<id>.json` saying which file the
design came from and when it was written. A `<id>.lock` held for as long as
the window is open is what tells a later launch that a slot was abandoned,
rather than another window's live autosave: QLockFile can tell a lock whose
process has gone from one still running.

A window that closes normally removes its slot - it either saved, or the user
said to discard the changes. So anything left behind is work that a crash, a
power cut or a killed process took away before anyone decided about it.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QLockFile, QStandardPaths

from .models import Document
from .serialization import save_document

_ORG = "AdvisoryLabs"
_APP = "HitLib Pattern Studio"
_DESIGN_SUFFIX = ".hlprofile"
_META_SUFFIX = ".json"
_LOCK_SUFFIX = ".lock"


def recovery_dir() -> Path:
    """Where autosaves live. Machine-local, beside the app's other state:
    %LOCALAPPDATA% on Windows, Application Support on macOS."""
    base = QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation)
    return Path(base) / _ORG / _APP / "Recovery"


def _lock(path: Path) -> QLockFile | None:
    """`path` locked for this process, or None while another live one holds it.

    Stale time 0: only a lock whose process has gone counts as abandoned.
    QLockFile's default also calls a lock stale once it is 30 s old, which
    every window open longer than that would be.
    """
    lock = QLockFile(str(path))
    lock.setStaleLockTime(0)
    return lock if lock.tryLock(0) else None


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


class AutosaveSlot:
    """One window's autosave. Nothing touches the disk until the first write."""

    def __init__(self) -> None:
        self._id = uuid.uuid4().hex
        self._dir: Path | None = None
        self._lock: QLockFile | None = None

    def _path(self, suffix: str) -> Path:
        return self._dir / f"{self._id}{suffix}"

    @property
    def has_autosave(self) -> bool:
        return self._dir is not None and self._path(_META_SUFFIX).is_file()

    def write(self, document: Document, origin: Path | None) -> None:
        """Autosave `document`, which came from `origin` (None if it was
        never saved). Raises OSError if it could not be written."""
        if self._dir is None:
            directory = recovery_dir()
            directory.mkdir(parents=True, exist_ok=True)
            self._dir = directory
        if self._lock is None:
            self._lock = _lock(self._path(_LOCK_SUFFIX))
            if self._lock is None:
                raise OSError(f"couldn't lock {self._path(_LOCK_SUFFIX)}")
        # The design first: the meta is what a later launch looks for, so a
        # crash between the two leaves nothing half-written on offer.
        save_document(self._path(_DESIGN_SUFFIX), document)
        meta = {"origin": str(origin) if origin else "", "saved_at": time.time()}
        _write_atomic(self._path(_META_SUFFIX), json.dumps(meta))

    def discard(self) -> None:
        """Drop the autosave, keeping the slot: the window is still open."""
        if self._dir is None:
            return
        for suffix in (_META_SUFFIX, _DESIGN_SUFFIX):
            with contextlib.suppress(OSError):
                self._path(suffix).unlink(missing_ok=True)

    def close(self) -> None:
        """Drop the autosave and give the slot up, as the window closes."""
        self.discard()
        if self._lock is not None:
            self._lock.unlock()
            self._lock = None


@dataclass
class Orphan:
    """An autosave its window left behind, locked by this process while the
    user decides about it, so a second window cannot offer it too."""

    design: Path
    origin: Path | None
    saved_at: float
    _meta: Path
    _lock: QLockFile = field(repr=False)

    @property
    def label(self) -> str:
        return self.origin.name if self.origin else "an untitled design"

    def discard(self) -> None:
        for path in (self._meta, self.design):
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
        self.release()

    def release(self) -> None:
        """Leave it for a later launch to offer again."""
        self._lock.unlock()


def find_orphans() -> list[Orphan]:
    """Every autosave whose window is gone, newest first."""
    directory = recovery_dir()
    if not directory.is_dir():
        return []
    found: list[Orphan] = []
    for meta_path in directory.glob(f"*{_META_SUFFIX}"):
        design = meta_path.with_suffix(_DESIGN_SUFFIX)
        if not design.is_file():
            continue
        lock = _lock(meta_path.with_suffix(_LOCK_SUFFIX))
        if lock is None:
            continue  # another window is open and still autosaving into it
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            origin_text = str(meta.get("origin") or "")
            saved_at = float(meta.get("saved_at") or design.stat().st_mtime)
        except (OSError, ValueError, TypeError, AttributeError):
            origin_text, saved_at = "", design.stat().st_mtime
        found.append(Orphan(design, Path(origin_text) if origin_text else None,
                            saved_at, meta_path, lock))
    found.sort(key=lambda o: o.saved_at, reverse=True)
    return found
