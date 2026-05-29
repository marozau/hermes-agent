"""Query layer for the reflection bank.

7 filter dimensions:
  - mistake_pattern (case-insensitive)
  - phase
  - profile
  - severity
  - recurrence_count (exact, min, max range)
  - fixed_in_phase (presence/absence)
  - requires_adjustment

Plus recurrence increment helper and archive toggle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from plugins.bmad.judge.reflection_bank.schema import ReflectionEntry, Severity


@dataclass
class Query:
    """Filter specification for searching reflection entries.

    Every field is optional — unset means "no filter on this dimension".
    """

    mistake_pattern: Optional[str] = None
    phase: Optional[str] = None
    profile: Optional[str] = None
    severity: Optional[Severity] = None
    recurrence_min: Optional[int] = None
    recurrence_max: Optional[int] = None
    recurrence_exact: Optional[int] = None
    fixed_in_phase: Optional[bool] = None  # True = fixed, False = unfixed
    requires_adjustment: Optional[bool] = None

    # Not exposed as a user-visible filter; used internally by the generator.
    _extra_filters: list = field(default_factory=list, repr=False)


def search(entries: list[ReflectionEntry], query: Query) -> list[ReflectionEntry]:
    """Return entries matching *all* set filter dimensions.

    Unset query fields (``None``) are skipped.
    """
    results: list[ReflectionEntry] = []
    for e in entries:
        if not _matches(e, query):
            continue
        results.append(e)
    return results


def find_by_pattern(
    entries: list[ReflectionEntry], pattern: str, case_insensitive: bool = True
) -> Optional[ReflectionEntry]:
    """Find the first entry whose ``mistake_pattern`` matches (case-insensitive by default).

    Returns the *earliest* matching entry (lowest recurrence_count tie-break).
    """
    normalized = pattern.strip().lower() if case_insensitive else pattern.strip()
    best: Optional[ReflectionEntry] = None
    for e in entries:
        candidate = (
            e.mistake_pattern.strip().lower()
            if case_insensitive
            else e.mistake_pattern.strip()
        )
        if candidate != normalized:
            continue
        if best is None or e.recurrence_count < best.recurrence_count:
            best = e
    return best


def increment_recurrence(
    entries: list[ReflectionEntry], pattern: str
) -> Optional[ReflectionEntry]:
    """Increment ``recurrence_count`` on the first entry matching ``pattern``.

    Returns the updated entry, or None if no match.
    """
    entry = find_by_pattern(entries, pattern)
    if entry is None:
        return None
    entry.recurrence_count += 1
    return entry


def toggle_fixed(entry: ReflectionEntry, phase: Optional[str] = None) -> ReflectionEntry:
    """Mark an entry as fixed (or unfixed).  Sets ``fixed_in_phase`` when fixing.

    Archive-friendly: entries are never deleted, just toggled.
    """
    entry.fixed_in_phase = phase
    return entry


# ------------------------------------------------------------------ internals


def _matches(entry: ReflectionEntry, q: Query) -> bool:
    # mistake_pattern — case-insensitive substring match
    if q.mistake_pattern is not None:
        if q.mistake_pattern.strip().lower() not in entry.mistake_pattern.strip().lower():
            return False

    if q.phase is not None:
        if q.phase.strip() != entry.phase.strip():
            return False

    if q.profile is not None:
        if q.profile.strip() != entry.profile.strip():
            return False

    if q.severity is not None:
        if entry.severity != q.severity:
            return False

    # recurrence_count — exact takes precedence, then range
    if q.recurrence_exact is not None:
        if entry.recurrence_count != q.recurrence_exact:
            return False
    else:
        if q.recurrence_min is not None and entry.recurrence_count < q.recurrence_min:
            return False
        if q.recurrence_max is not None and entry.recurrence_count > q.recurrence_max:
            return False

    if q.fixed_in_phase is not None:
        is_fixed = entry.fixed_in_phase is not None
        if is_fixed != q.fixed_in_phase:
            return False

    if q.requires_adjustment is not None:
        if entry.requires_adjustment != q.requires_adjustment:
            return False

    return True
