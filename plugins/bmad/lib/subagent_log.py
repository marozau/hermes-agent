"""Sub-agent activity log — append, rotate, read_recent.

Writes to planning-artifacts/_subagent-log.yaml using lib/status._atomic_write.
Story 3.3.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .status import _atomic_write

LOG_FILENAME = "_subagent-log.yaml"
MAX_ENTRIES = 100


def log_path(project_dir: Path) -> Path:
    return project_dir / "planning-artifacts" / LOG_FILENAME


def archive_dir(project_dir: Path) -> Path:
    return project_dir / "planning-artifacts" / ".archive"


def append(project_dir: Path, entry: dict) -> None:
    """Append *entry* to the subagent log.

    Reads existing entries, appends the new one, and writes back
    atomically via ``_atomic_write``.
    """
    path = log_path(project_dir)
    entries = _read_all(path)
    entries.append(entry)
    _atomic_write(path, entries)  # type: ignore[arg-type]


def read_recent(project_dir: Path, limit: int = 10) -> list[dict]:
    """Return the *limit* most recent entries from the subagent log.

    Returns an empty list if the log does not exist yet.
    """
    entries = _read_all(log_path(project_dir))
    return entries[-limit:]


def rotate(project_dir: Path, max_entries: int = MAX_ENTRIES) -> None:
    """Archive older entries when *max_entries* is exceeded.

    Older entries are moved to ``planning-artifacts/.archive/_subagent-log-YYYY-MM-DD.yaml``.
    The *max_entries* most recent remain in the live log.
    """
    path = log_path(project_dir)
    entries = _read_all(path)
    if len(entries) <= max_entries:
        return
    archive_entries = entries[:-max_entries]
    keep = entries[-max_entries:]
    date_str = datetime.now().strftime("%Y-%m-%d")
    archive_dir_path = archive_dir(project_dir)
    archive_dir_path.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir_path / f"_subagent-log-{date_str}.yaml"
    _atomic_write(archive_path, archive_entries)  # type: ignore[arg-type]
    _atomic_write(path, keep)  # type: ignore[arg-type]


def _read_all(path: Path) -> list[dict]:
    """Read all entries from the subagent log YAML file.

    Handles missing files, empty files, and single-dict files
    (which ``yaml.safe_load`` may return as a dict rather than a list).
    """
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return []
    if isinstance(data, dict):
        return [data]
    # Must be a list
    return data  # type: ignore[return-value]
