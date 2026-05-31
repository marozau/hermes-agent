"""Epic anchor — extracts story specs from epic documents (Story 7.2).

Parses epic markdown to extract story IDs, descriptions, dependencies,
and success predicates. Used by the orchestrator to build DAGs from epics.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class StorySpec:
    """A single story extracted from an epic document."""
    id: str
    title: str
    description: str = ""
    dependencies: list[str] = field(default_factory=list)
    success_predicates: list[str] = field(default_factory=list)
    effort_hours: float = 0.0
    sprint: str = ""
    worktree: Optional[str] = None
    touches: list[str] = field(default_factory=list)
    verification_gate: str = ""  # "adversarial" for opt-in Opus review (Story 7.8)


@dataclass
class EpicSpec:
    """An epic parsed from its planning document."""
    id: str
    name: str
    description: str = ""
    stories: list[StorySpec] = field(default_factory=list)
    source_path: str = ""

    def story_by_id(self, story_id: str) -> Optional[StorySpec]:
        for s in self.stories:
            if s.id == story_id:
                return s
        return None

    def dependency_graph(self) -> dict[str, list[str]]:
        """Build adjacency list: story_id → list of dependency IDs."""
        return {s.id: list(s.dependencies) for s in self.stories}

    def topological_waves(self) -> list[list[str]]:
        """Group stories into execution waves via topological sort.

        Wave 0 has no unmet dependencies, wave 1 depends only on wave 0, etc.
        Returns list of waves, each a list of story IDs.

        M-9: Raises CyclicDependencyError if a cycle is detected.
        """
        graph = self.dependency_graph()
        story_ids = set(graph.keys())
        in_progress: dict[str, int] = {sid: 0 for sid in story_ids}
        for sid, deps in graph.items():
            for d in deps:
                if d in story_ids:
                    in_progress[sid] += 1

        waves: list[list[str]] = []
        remaining = set(story_ids)
        while remaining:
            wave = [sid for sid in remaining if in_progress.get(sid, 0) == 0]
            if not wave:
                # M-9: Raise on cycle instead of silently executing
                raise CyclicDependencyError(
                    f"Cycle detected in dependency graph among stories: "
                    f"{', '.join(sorted(remaining))}"
                )
            waves.append(sorted(wave))
            for sid in wave:
                remaining.discard(sid)
                for dependent in story_ids:
                    if sid in graph.get(dependent, []):
                        in_progress[dependent] -= 1
        return waves


class CyclicDependencyError(ValueError):
    """Raised when epic story dependencies contain a cycle."""


# ── Parsing ──────────────────────────────────────────────────────────────────


def parse_epic_file(path: Path) -> EpicSpec:
    """Parse an epic document into an EpicSpec.

    Supports the BMAD epics-stories format with story tables and
    dependency/success-predicate sections.
    """
    text = path.read_text(encoding="utf-8")
    return parse_epic_text(text, source_path=str(path))


def parse_epic_text(text: str, source_path: str = "", epic_id: str = "") -> EpicSpec:
    """Parse epic text into an EpicSpec."""
    stories: list[StorySpec] = []

    # Pattern 1: Table rows like "| 7.1 | description | 1.5h | — |"
    table_pattern = re.compile(
        r"\|\s*(\d+\.\d+)\s*\|(.*?)\|(.*?)\|(.*?)\|",
        re.MULTILINE,
    )
    for m in table_pattern.finditer(text):
        story_id = m.group(1).strip()
        title = m.group(2).strip()
        effort_str = m.group(3).strip()
        deps_str = m.group(4).strip()

        effort = 0.0
        effort_match = re.search(r"(\d+(?:\.\d+)?)\s*h", effort_str)
        if effort_match:
            effort = float(effort_match.group(1))

        deps: list[str] = []
        if deps_str and deps_str != "—":
            deps = [d.strip() for d in re.split(r"[,;]", deps_str) if d.strip()]

        stories.append(StorySpec(
            id=story_id,
            title=title,
            effort_hours=effort,
            dependencies=deps,
        ))

    # Pattern 2: Story headings like "### 7.3 lib/orchestrator.py"
    heading_pattern = re.compile(r"###\s+(\d+\.\d+)\s+(.*)")
    for m in heading_pattern.finditer(text):
        sid = m.group(1).strip()
        title = m.group(2).strip()
        existing = next((s for s in stories if s.id == sid), None)
        if existing:
            existing.title = title
        else:
            stories.append(StorySpec(id=sid, title=title))

    # Extract success predicates from text blocks after "success_predicates" or "AC:"
    ac_pattern = re.compile(
        r"(?:success_predicates?|AC|acceptance criteria)[:\s]*\n((?:[-*]\s+.*\n)+)",
        re.IGNORECASE,
    )
    for m in ac_pattern.finditer(text):
        predicates = [
            line.lstrip("-* ").strip()
            for line in m.group(1).strip().split("\n")
            if line.strip()
        ]
        # Try to associate with the nearest story heading above this block
        prefix = text[:m.start()]
        last_heading = heading_pattern.findall(prefix)
        if last_heading:
            sid = last_heading[-1][0]
            story = next((s for s in stories if s.id == sid), None)
            if story:
                story.success_predicates = predicates

    # B-2: Extract verification_gate from per-story sections
    vg_pattern = re.compile(
        r"verification_gate[:\s]+(\S+)",
        re.IGNORECASE,
    )
    for m in vg_pattern.finditer(text):
        gate_value = m.group(1).strip().lower()
        prefix = text[:m.start()]
        last_heading = heading_pattern.findall(prefix)
        if last_heading:
            sid = last_heading[-1][0]
            story = next((s for s in stories if s.id == sid), None)
            if story:
                story.verification_gate = gate_value
                logger.debug("[epic_anchor] Story %s: verification_gate=%s", sid, gate_value)

    if not epic_id:
        # Infer from filename or first heading
        heading = re.search(r"#\s+.*?Epic\s+(\d+)", text, re.IGNORECASE)
        epic_id = heading.group(1) if heading else "unknown"

    return EpicSpec(
        id=epic_id,
        name=f"Epic {epic_id}",
        stories=stories,
        source_path=source_path,
    )


def deprecation_message() -> str:
    """Return the deprecation notice for /bmad:create-story."""
    return (
        "⚠️  /bmad:create-story is deprecated in favor of /bmad:epics-stories.\n"
        "The new skill generates all stories for an epic in one pass with\n"
        "dependency wiring and success predicates. Use:\n"
        "  /bmad:epics-stories <epic-number>\n"
        "To suppress this warning: --no-deprecation-notice"
    )
