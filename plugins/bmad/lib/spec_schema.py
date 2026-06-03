"""Spec schema — typed dataclasses for command structured output (Story 12.1).

Every command body can optionally include a YAML frontmatter block
(`spec:`) that declares persona, phase, verification predicates, and
rendering hints.  Commands without a spec: block are treated as legacy
(imperative-only) and pass through unchanged.

Pure dataclasses — no Pydantic dependency (CI-8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class VerificationItem:
    """A single verification checklist item.

    Attributes:
        description: Human-readable check text.
        predicate: Optional dotted path to a predicate function
            (e.g. "plugins.bmad.predicates.dev_story.tests_pass").
            None means the check is manual / informational.
    """
    description: str
    predicate: str | None = None


@dataclass(frozen=True)
class CommandSpec:
    """Structured spec block from a command .md frontmatter.

    Required fields (validation enforced by spec_parser.parse_spec):
        persona: The role the LLM should adopt (e.g. "Dev", "QA", "SM").
        phase: BMAD phase name (e.g. "implementation", "planning").
        verification: Non-empty list of verification items.

    Optional fields:
        imperative_preamble: If True (default), renderer prepends an
            imperative-voice preamble.  Set False for informational
            commands (help, status, dashboard, etc.) that should NOT
            assert "EXECUTE NOW".
        predicate_module: Dotted path to the predicate module for this
            command (e.g. "plugins.bmad.predicates.dev_story").
            Individual VerificationItems can override per-item.
        output_artifacts: List of expected output artifacts (for the
            renderer's stop-condition template).  Empty = no artifact
            tracking.
        metadata: Freeform key-value pairs for extensions.
    """
    persona: str
    phase: str
    verification: tuple[VerificationItem, ...]
    imperative_preamble: bool = True
    predicate_module: str | None = None
    output_artifacts: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
