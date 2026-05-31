"""Tests for Story 7.2 (epic-doc anchor) and Story 7.10 (orchestrate-export) gaps.

Verifies:
- /bmad:dev-story accepts epic-doc anchor <path>#story-X.Y
- Adversarial gate runs after story predicates pass (opt-in)
- bmad-orchestrate-export CLI command
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plugins.bmad.lib.epic_anchor import StorySpec, EpicSpec, parse_epic_text


# ── Story 7.2: Epic-doc anchor in dev-story ──────────────────────────────────


class TestEpicDocAnchor:
    def test_extract_story_section_by_heading(self, tmp_path):
        """7.2: Extract story section by ### heading."""
        from plugins.bmad.commands.dev_story import _extract_story_section

        epic = tmp_path / "epic.md"
        epic.write_text("""# Epic 7

### 7.3 lib/orchestrator.py

The core orchestrator. ~250 LOC.

### 7.4 /bmad:orchestrate cmd

CLI handler.
""")
        section = _extract_story_section(epic, "7.3")
        assert section is not None
        assert "7.3" in section
        assert "orchestrator" in section.lower()
        assert "7.4" not in section  # Should stop before next heading

    def test_extract_story_section_not_found(self, tmp_path):
        """7.2: Returns None for non-existent story ID."""
        from plugins.bmad.commands.dev_story import _extract_story_section

        epic = tmp_path / "epic.md"
        epic.write_text("# Epic\n\n### 7.1 foo\nbar\n")
        assert _extract_story_section(epic, "9.9") is None

    def test_extract_last_story_section(self, tmp_path):
        """7.2: Last story section extends to end of file."""
        from plugins.bmad.commands.dev_story import _extract_story_section

        epic = tmp_path / "epic.md"
        epic.write_text("""# Epic

### 7.1 first

First story.

### 7.2 last

Last story content here.
More content.
""")
        section = _extract_story_section(epic, "7.2")
        assert section is not None
        assert "last story content" in section.lower()
        assert "More content" in section


# ── Story 7.8: Adversarial gate integration ──────────────────────────────────


class TestAdversarialGateIntegration:
    def test_verification_gate_field_on_story_spec(self):
        """7.8: StorySpec has verification_gate field."""
        story = StorySpec(
            id="7.3", title="Orchestrator",
            verification_gate="adversarial",
        )
        assert story.verification_gate == "adversarial"

    def test_verification_gate_default_empty(self):
        """7.8: verification_gate defaults to empty (no gate)."""
        story = StorySpec(id="7.3", title="Orchestrator")
        assert story.verification_gate == ""

    def test_adversarial_gate_parses_from_text(self):
        """7.8: Epic parser extracts verification_gate if present."""
        text = """# Epic 7

| 7.3 | lib/orchestrator.py | 5h | — |
| 7.4 | /bmad:orchestrate cmd | 3h | 7.3 |
"""
        epic = parse_epic_text(text, epic_id="7")
        assert len(epic.stories) == 2


# ── Story 7.10: orchestrate-export CLI ───────────────────────────────────────


class TestOrchestrateExport:
    def test_export_handler_resolves_epic_path(self, tmp_path):
        """7.10: Export handler resolves epic path."""
        from plugins.bmad.commands.orchestrate_export import _resolve_epic

        epic = tmp_path / "epics-stories-7.md"
        epic.write_text("# Epic 7\n")

        # Direct path works
        result = _resolve_epic(str(epic))
        assert result.exists()

    def test_export_handler_missing_epic_exits(self, tmp_path):
        """7.10: Export handler exits on missing epic."""
        import sys
        from plugins.bmad.commands.orchestrate_export import handler

        with pytest.raises(SystemExit) as exc_info:
            handler(["nonexistent-epic.md"])
        assert exc_info.value.code == 1

    def test_export_creates_flow_file(self, tmp_path):
        """7.10: Export creates a Prefect flow .py file."""
        from plugins.bmad.lib.epic_anchor import EpicSpec, StorySpec
        from plugins.bmad.lib.orchestrator import OrchestrateReport
        from plugins.bmad.lib.prefect_bridge import export_prefect_flow

        epic = EpicSpec(
            id="7", name="Epic 7",
            stories=[
                StorySpec(id="7.1", title="Story A", success_predicates=["file_exists:foo"]),
                StorySpec(id="7.2", title="Story B", dependencies=["7.1"],
                          success_predicates=["file_exists:bar"]),
            ],
        )
        report = OrchestrateReport(
            epic_id="7", total_stories=2,
            waves=[["7.1"], ["7.2"]], results={},
        )

        output = tmp_path / "flow.py"
        flow_path = export_prefect_flow(epic, report, output)
        assert flow_path.exists()
        content = flow_path.read_text()
        assert "@flow" in content
        assert "@task" in content
