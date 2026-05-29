"""Reflection entry schema — dataclass with 17 fields, YAML serialization, forward-compatible _extra for schema evolution."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import yaml


class Severity(str, Enum):
    """Severity levels for reflection entries.

    Coerces unknown values with a ValueError-tolerant constructor so that
    entries written by a future version with a new severity level survive
    a round-trip through an older version without blowing up.
    """

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @classmethod
    def _missing_(cls, value: object) -> "Severity":
        """ValueError-tolerant coercion — unknown values become INFO."""
        return cls.INFO


@dataclass
class ReflectionEntry:
    """One post-phase reflection observation.

    Fields
    ------
    id : str
        UUID4 string, generated on creation.
    phase : str
        Name of the BMAD phase that produced this reflection (e.g. "PRD", "Architecture").
    profile : str
        Hermes profile that produced this entry.
    timestamp : str
        ISO-8601 UTC timestamp of creation.
    summary : str
        One-paragraph human-readable description of the issue.
    mistake_pattern : str
        Normalized slug identifying the mistake class (e.g. "missing-edge-case").
        Used as the join key for recurrence tracking.
    severity : Severity
        Severity of the issue.
    phase_of_discovery : str
        Which phase caught the issue (may differ from ``phase`` if a later phase
        discovered the consequence of an earlier mistake).
    root_cause : str
        Root cause analysis text.
    first_principles_vs_heuristic : str
        Human-readable note on whether root cause stems from first-principles
        reasoning vs heuristic/shortcut.
    confidence : float
        Confidence that this is a real issue (0.0 – 1.0).
    recommendation : str
        Concrete action to prevent recurrence.
    adjusted_instruction : str
        Modified agent instruction text to bake the fix into future runs.
    affected_skill : Optional[str]
        Skill name(s) affected, if any.
    recurrence_count : int
        How many times this mistake_pattern has recurred (updated by generator).
    fixed_in_phase : Optional[str]
        If the issue was resolved, which phase fixed it.
    requires_adjustment : bool
        Whether an agent instruction / skill patch is needed.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    phase: str = ""
    profile: str = "default"
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    summary: str = ""
    mistake_pattern: str = ""
    severity: Severity = Severity.MEDIUM
    phase_of_discovery: str = ""
    root_cause: str = ""
    first_principles_vs_heuristic: str = ""
    confidence: float = 0.8
    recommendation: str = ""
    adjusted_instruction: str = ""
    affected_skill: Optional[str] = None
    recurrence_count: int = 0
    fixed_in_phase: Optional[str] = None
    requires_adjustment: bool = False

    # Schema evolution: hold unknown optional fields so round-trips preserve them.
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)


def create_entry(**kwargs: Any) -> ReflectionEntry:
    """Factory: build an entry with clean defaults.

    Unknown kwargs beyond the 17 known fields land in ``_extra`` for forward
    compatibility.  Coerces ``severity`` to a ``Severity`` enum when received
    as a raw string (YAML round-trip).
    """
    known = {f.name for f in ReflectionEntry.__dataclass_fields__.values()}  # type: ignore[arg-type]
    known_fields = {k: v for k, v in kwargs.items() if k in known}
    extra_fields = {k: v for k, v in kwargs.items() if k not in known}

    # Coerce severity from string to enum
    if "severity" in known_fields and isinstance(known_fields["severity"], str):
        known_fields["severity"] = Severity(known_fields["severity"])

    entry = ReflectionEntry(**known_fields)
    entry._extra = extra_fields
    return entry


def load_entries(raw: str) -> list[ReflectionEntry]:
    """Parse a YAML document into a list of ReflectionEntry objects.

    Unknown fields are preserved in ``_extra``.
    """
    data = yaml.safe_load(raw)
    if data is None:
        return []
    if not isinstance(data, list):
        raise ValueError(f"Expected a YAML list of entries, got {type(data).__name__}")
    entries: list[ReflectionEntry] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        entries.append(create_entry(**item))
    return entries


def dump_entries(entries: list[ReflectionEntry]) -> str:
    """Serialize a list of ReflectionEntry objects to a YAML string.

    Writes a YAML document that is human-readable and round-trippable.
    """
    raw: list[dict[str, Any]] = []
    for e in entries:
        d: dict[str, Any] = {}
        for f_name, f_val in asdict(e).items():
            if f_name == "_extra":
                continue
            # Serialize enums to their string value
            if isinstance(f_val, Enum):
                d[f_name] = f_val.value
            elif f_val is not None:
                d[f_name] = f_val
        # Merge preserved extra fields
        if e._extra:
            d.update(e._extra)
        raw.append(d)
    return yaml.dump(raw, default_flow_style=False, allow_unicode=True, sort_keys=False)
