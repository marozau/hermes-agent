"""Persistence layer for the reflection bank.

Two-tier storage:
- Global:  ~/.hermes/reflection-bank/global.yaml  (cross-project)
- Per-project:  .hermes/reflection-bank.yaml       (project-local)

Atomic writes via tempfile + os.replace.  Automatic flush on exit via atexit.
"""

from __future__ import annotations

import atexit
import os
import tempfile
from pathlib import Path
from typing import Optional

from plugins.bmad.judge.reflection_bank.schema import ReflectionEntry, load_entries, dump_entries


class BankStore:
    """Reads/writes a single YAML reflection bank file.

    Parameters
    ----------
    path : Path
        Absolute path to the bank YAML file.
    auto_flush : bool
        If True, register an ``atexit`` handler that flushes dirty state.
    """

    def __init__(self, path: Path | str, auto_flush: bool = True) -> None:
        self.path = Path(path)
        self._entries: list[ReflectionEntry] = []
        self._dirty = False
        if auto_flush:
            atexit.register(self._flush_if_dirty)
        self._load()

    # ------------------------------------------------------------------ public API

    @property
    def entries(self) -> list[ReflectionEntry]:
        return self._entries

    def add(self, entry: ReflectionEntry) -> None:
        """Append an entry and mark the store dirty."""
        self._entries.append(entry)
        self._dirty = True

    def update(self, entry: ReflectionEntry) -> None:
        """Replace an existing entry by id.  No-op if id not found."""
        for i, e in enumerate(self._entries):
            if e.id == entry.id:
                self._entries[i] = entry
                self._dirty = True
                return

    def remove(self, entry_id: str) -> bool:
        """Remove an entry by id.  Returns True if found and removed."""
        for i, e in enumerate(self._entries):
            if e.id == entry_id:
                del self._entries[i]
                self._dirty = True
                return True
        return False

    def flush(self) -> None:
        """Write current entries to disk (atomic)."""
        self._write(self._entries)
        self._dirty = False

    def reload(self) -> None:
        """Re-read from disk, discarding in-memory state."""
        self._load()

    # ---------------------------------------------------------------- internals

    def _load(self) -> None:
        if self.path.exists():
            raw = self.path.read_text(encoding="utf-8")
            self._entries = load_entries(raw) if raw.strip() else []
        else:
            self._entries = []

    def _write(self, entries: list[ReflectionEntry]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = dump_entries(entries)
        fd, tmp_path = tempfile.mkstemp(
            dir=self.path.parent, prefix=".reflection-bank-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, str(self.path))
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def _flush_if_dirty(self) -> None:
        if self._dirty:
            self.flush()


# ------------------------------------------------------------------ convenience factories


def get_global_store() -> BankStore:
    """Return the global reflection bank store."""
    home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    return BankStore(home / "reflection-bank" / "global.yaml")


def get_project_store(project_root: Optional[Path] = None) -> BankStore:
    """Return the project-local reflection bank store.

    Falls back to cwd if ``project_root`` is None.
    """
    root = project_root or Path.cwd()
    return BankStore(root / ".hermes" / "reflection-bank.yaml")
