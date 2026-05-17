"""Workflow status ledger — YAML persistence with mtime-based caching and atomic writes.

Exports:
    _cache             dict[Path, tuple[dict, float]] — mtime-based cache
    load               Read workflow-status.yaml (cached by mtime)
    mark_complete      Atomic-write a slot as complete with artifact path
    mark_in_progress   Atomic-write a slot as in-progress
    get_next_required  Delegate to phases.next_required_slot
    _atomic_write      Low-level POSIX-atomic YAML writer (tmp → fsync → rename)

Architecture §A-7.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml

from ._datetime import _today_iso
from . import phases

# ── Module-level cache ──────────────────────────────────────────────────────
# {path: (data_dict, mtime_float)}
_cache: dict[Path, tuple[dict, float]] = {}


# ── Public API ──────────────────────────────────────────────────────────────

def load(project_dir: Path) -> dict:
    """Load workflow-status.yaml with mtime-based cache invalidation.

    Returns the parsed YAML dict.  If the file's mtime hasn't changed since
    the last read the cached copy is returned without I/O.
    """
    path = project_dir / "planning-artifacts" / "workflow-status.yaml"
    mtime = path.stat().st_mtime
    if path in _cache and _cache[path][1] == mtime:
        return _cache[path][0]
    data: dict = yaml.safe_load(path.read_text())
    _cache[path] = (data, mtime)
    return data


def mark_complete(
    project_dir: Path, phase: str, slot: str, artifact_path: str,
) -> None:
    """Atomic write: set *slot* under *phase* to *artifact_path*.

    Also bumps ``last_updated`` and invalidates the module-level cache.
    """
    data = load(project_dir)
    data.setdefault("phases", {}).setdefault(phase, {})[slot] = artifact_path
    data["last_updated"] = _today_iso()
    _write_and_invalidate(project_dir, data)


def mark_in_progress(project_dir: Path, phase: str, slot: str) -> None:
    """Atomic write: set *slot* under *phase* to ``'in-progress'``.

    Also bumps ``last_updated`` and invalidates the module-level cache.
    """
    data = load(project_dir)
    data.setdefault("phases", {}).setdefault(phase, {})[slot] = "in-progress"
    data["last_updated"] = _today_iso()
    _write_and_invalidate(project_dir, data)


def get_next_required(state: dict, level: int) -> dict | None:
    """Return the next required slot ``{phase, slot, command}`` or ``None``.

    Pure delegation to ``phases.next_required_slot`` — see that function for
    the complete logic.
    """
    return phases.next_required_slot(state, level)


# ── Internals ───────────────────────────────────────────────────────────────

def _atomic_write(path: Path, data: dict) -> None:
    """Write *data* as YAML to *path* using a POSIX-atomic rename.

    Creates a temporary file in the same directory (with a ``.`` prefix and
    ``.tmp`` suffix), calls ``fsync`` on its file descriptor, then performs
    an atomic ``os.replace``.

    This prevents partial writes from being observed by other readers (PRD
    R-5 mitigation). If the write fails before the rename, the temp file
    is cleaned up so we don't leak dotfiles into ``planning-artifacts/``.
    """
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, prefix=".", suffix=".tmp", delete=False,
        ) as f:
            tmp_name = f.name
            yaml.safe_dump(data, f, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)  # POSIX atomic rename
        tmp_name = None  # rename consumed it; no cleanup needed
    finally:
        if tmp_name is not None and os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def _write_and_invalidate(project_dir: Path, data: dict) -> None:
    """Write *data* to the workflow-status file and invalidate cache."""
    path = project_dir / "planning-artifacts" / "workflow-status.yaml"
    _atomic_write(path, data)
    _cache.pop(path, None)
