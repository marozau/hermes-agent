"""Golden test: verify epic_anchor parser extracts consolidated fields.

B-1 verification: ensures the consolidated per-story section shape
(SKILL.md update) is parseable by epic_anchor.py, including:
- success_predicates (kind:payload format)
- verification_gate (adversarial or empty)
- dependencies
"""

from __future__ import annotations

import textwrap

import pytest

from plugins.bmad.lib.epic_anchor import parse_epic_text, CyclicDependencyError


# ── Golden test: consolidated shape ──────────────────────────────────────────

CONSOLIDATED_EPIC = textwrap.dedent("""\
    # Epic 7: Orchestrated Execution

    ## Story 7.1: Extend epics-stories skill

    **Description:** Update the epics-stories skill to emit consolidated per-story sections.

    **Dependencies:** none

    **Acceptance Criteria:**

    - **Given** the epics-stories skill
    - **When** generating a story
    - **Then** the output includes success_predicates, verification_gate, failure_action

    **success_predicates:**
    - file_exists:plugins/bmad/skills/bmm/epics-stories/SKILL.md
    - tests_pass:plugins/bmad/tests/unit/test_epic_anchor.py
    - grep:verification_gate:plugins/bmad/lib/epic_anchor.py

    **verification_gate:** adversarial

    **failure_action:**
      max_attempts: 2
      action: retry_then_escalate

    **Effort:** 1.5h
    **Touches:** skills/bmm/epics-stories/SKILL.md

    ## Story 7.3: Core orchestrator

    **Description:** Implement lib/orchestrator.py with wave dispatch, predicates, retry.

    **Dependencies:** 7.1, 7.2

    **Acceptance Criteria:**

    - **Given** an epic document
    - **When** /bmad:orchestrate runs
    - **Then** stories are dispatched in topological waves

    **success_predicates:**
    - file_exists:plugins/bmad/lib/orchestrator.py
    - tests_pass:plugins/bmad/tests/unit/test_orchestrator.py
    - shell:true

    **verification_gate:** none

    **failure_action:**
      max_attempts: 3
      action: retry_then_escalate

    **Effort:** 5h
    **Touches:** lib/orchestrator.py
""")


class TestGoldenConsolidatedShape:
    def test_parses_epic_id(self):
        """Parser extracts epic ID from heading."""
        epic = parse_epic_text(CONSOLIDATED_EPIC)
        assert epic.id == "7"

    def test_parses_epic_name(self):
        """Parser extracts epic name from heading."""
        epic = parse_epic_text(CONSOLIDATED_EPIC)
        assert "Orchestrated Execution" in epic.name

    def test_parses_two_stories(self):
        """Parser extracts both stories."""
        epic = parse_epic_text(CONSOLIDATED_EPIC)
        assert len(epic.stories) == 2

    def test_parses_story_ids(self):
        """Parser extracts story IDs correctly."""
        epic = parse_epic_text(CONSOLIDATED_EPIC)
        ids = {s.id for s in epic.stories}
        assert ids == {"7.1", "7.3"}

    def test_parses_success_predicates(self):
        """Parser extracts success_predicates from consolidated shape."""
        epic = parse_epic_text(CONSOLIDATED_EPIC)
        story_71 = next(s for s in epic.stories if s.id == "7.1")
        assert len(story_71.success_predicates) == 3
        assert "file_exists:plugins/bmad/skills/bmm/epics-stories/SKILL.md" in story_71.success_predicates
        assert "tests_pass:plugins/bmad/tests/unit/test_epic_anchor.py" in story_71.success_predicates
        assert "grep:verification_gate:plugins/bmad/lib/epic_anchor.py" in story_71.success_predicates

    def test_parses_verification_gate_adversarial(self):
        """B-2: Parser extracts verification_gate: adversarial."""
        epic = parse_epic_text(CONSOLIDATED_EPIC)
        story_71 = next(s for s in epic.stories if s.id == "7.1")
        assert story_71.verification_gate == "adversarial"

    def test_parses_verification_gate_none(self):
        """B-2: Parser extracts verification_gate: none."""
        epic = parse_epic_text(CONSOLIDATED_EPIC)
        story_73 = next(s for s in epic.stories if s.id == "7.3")
        assert story_73.verification_gate == "none"

    def test_parses_dependencies(self):
        """Parser extracts dependencies from consolidated shape."""
        epic = parse_epic_text(CONSOLIDATED_EPIC)
        story_71 = next(s for s in epic.stories if s.id == "7.1")
        story_73 = next(s for s in epic.stories if s.id == "7.3")
        assert story_71.dependencies == []
        assert "7.1" in story_73.dependencies
        assert "7.2" in story_73.dependencies

    def test_parses_shell_predicate(self):
        """Parser extracts shell: predicate (kind:payload format)."""
        epic = parse_epic_text(CONSOLIDATED_EPIC)
        story_73 = next(s for s in epic.stories if s.id == "7.3")
        assert "shell:true" in story_73.success_predicates

    def test_topological_waves(self):
        """Parser builds correct wave DAG from dependencies."""
        epic = parse_epic_text(CONSOLIDATED_EPIC)
        waves = epic.topological_waves()
        # 7.1 has no deps → wave 0; 7.3 depends on 7.1,7.2 → wave 1
        assert len(waves) >= 2
        assert "7.1" in waves[0]
        assert "7.3" in waves[1]


# ── Cycle detection (M-9) ───────────────────────────────────────────────────

CYCLIC_EPIC = textwrap.dedent("""\
    # Epic 8: Cyclic

    ## Story 8.1: A
    **Description:** A
    **Dependencies:** 8.2
    **success_predicates:**
    - file_exists:x

    ## Story 8.2: B
    **Description:** B
    **Dependencies:** 8.1
    **success_predicates:**
    - file_exists:y
""")


class TestCycleDetection:
    def test_cyclic_raises(self):
        """M-9: Cyclic dependencies raise CyclicDependencyError."""
        epic = parse_epic_text(CYCLIC_EPIC)
        with pytest.raises(CyclicDependencyError):
            epic.topological_waves()
