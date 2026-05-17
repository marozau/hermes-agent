"""
test_e2e_solutioning_loop.py — E2E test of Solutioning → Implementation loop.

Verifies that the 3.5 commands fire correctly, phase gates advance,
and status updates propagate.

Run::

    pytest tests/e2e/test_e2e_solutioning_loop.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def project_with_completed_analysis_planning() -> Path:
    """Create a fixture project where Analysis + Planning phases are complete.

    Level 2 project with product-brief and prd done, ready for Solutioning.
    """
    with tempfile.TemporaryDirectory() as td:
        path = Path(td)

        # bmad/config.yaml
        (path / "bmad").mkdir()
        yaml.safe_dump(
            {
                "project_name": "e2e-test-proj",
                "project_type": "api",
                "project_level": 2,
                "user_name": "tester",
                "planning_artifacts": "planning-artifacts",
                "implementation_artifacts": "implementation-artifacts",
                "created": "2026-05-17",
            },
            open(path / "bmad" / "config.yaml", "w"),
            sort_keys=False,
        )

        # Directory structure
        (path / "planning-artifacts").mkdir()
        (path / "planning-artifacts" / "research").mkdir()
        (path / "implementation-artifacts").mkdir()
        (path / "implementation-artifacts" / "stories").mkdir()

        # Dummy product brief & PRD artifacts (for path-rule matching)
        (path / "planning-artifacts" / "product-brief-test.md").write_text(
            "# Product Brief\n\nTest product brief."
        )
        (path / "planning-artifacts" / "prd-test.md").write_text(
            "# PRD\n\nTest PRD."
        )

        # workflow-status.yaml — analysis + planning complete
        yaml.safe_dump(
            {
                "project": "e2e-test-proj",
                "level": 2,
                "created": "2026-05-17",
                "last_updated": "2026-05-17",
                "phases": {
                    "analysis": {"product-brief": "complete"},
                    "planning": {"prd": "complete"},
                    "solutioning": {},
                    "implementation": {},
                },
            },
            open(path / "planning-artifacts" / "workflow-status.yaml", "w"),
            sort_keys=False,
        )

        yield path


# ── Helpers ───────────────────────────────────────────────────────────────


def run_cmd(ctx, command: str) -> str:
    """Simulate invoking a BMAD slash command handler.

    This reads the handler function, calls it with ctx + args,
    and returns the result string.
    """
    # Map command name to module path
    cmd_map = {
        "create-architecture": "create_architecture",
        "epics-stories": "epics_stories",
        "solutioning-gate-check": "solutioning_gate_check",
        "sprint-planning": "sprint_planning",
        "create-story": "create_story",
        "dev-story": "dev_story",
        "code-review": "code_review",
    }

    mod_name = cmd_map.get(command)
    if not mod_name:
        raise ValueError(f"Unknown command: {command}")

    # Import the handler module
    import importlib

    mod = importlib.import_module(
        f"plugins.bmad.commands.{mod_name}"
    )
    return mod.handler(ctx, "")


# ── Mock context ──────────────────────────────────────────────────────────


@pytest.fixture
def mock_ctx(project_with_completed_analysis_planning):
    """Mock Hermes context pointing at the fixture project."""

    class MockCtx:
        working_directory = str(project_with_completed_analysis_planning)
        project_dir = str(project_with_completed_analysis_planning)
        profile_config = {}

    return MockCtx()


def _blocked(reason: str) -> bool:
    """True if the result string indicates a phase gate block."""
    return reason.startswith("🚫")


# ── Tests ─────────────────────────────────────────────────────────────────


class TestE2ESolutioningLoop:
    """Test the full Solutioning → Implementation sequence."""

    def test_phase_gates_advance_sequentially(
        self,
        project_with_completed_analysis_planning: Path,
        mock_ctx,
    ):
        """Each command's phase gate properly allows execution when prerequisites are met."""
        from plugins.bmad.lib import phases
        from plugins.bmad.lib.status import load as load_status

        status_path = (
            project_with_completed_analysis_planning
            / "planning-artifacts"
            / "workflow-status.yaml"
        )

        # ── Step 1: create-architecture (analysis → solutioning) ──────
        result = run_cmd(mock_ctx, "create-architecture")
        assert not _blocked(result), (
            f"create-architecture blocked unexpectedly: {result}"
        )

        # Simulate the handler producing output
        # Mark architecture slot complete (as post_tool_call hook would)
        state = load_status(project_with_completed_analysis_planning)
        ok, _ = phases.can_run("create-architecture", state, 2)
        assert ok, "can_run should allow create-architecture with analysis complete"

        # Manually mark complete (would normally happen via post_tool_call hook)
        from plugins.bmad.lib.status import mark_complete
        mark_complete(
            project_with_completed_analysis_planning,
            "solutioning",
            "architecture",
            "planning-artifacts/architecture-test.md",
        )

        # Verify: status now shows solutioning.architecture = complete
        state = load_status(project_with_completed_analysis_planning)
        assert state["phases"]["solutioning"]["architecture"].endswith(".md"), (
            "architecture slot should be complete after running create-architecture"
        )

        # ── Step 2: epics-stories ────────────────────────────────────
        result = run_cmd(mock_ctx, "epics-stories")
        assert not _blocked(result), (
            f"epics-stories blocked unexpectedly: {result}"
        )

        mark_complete(
            project_with_completed_analysis_planning,
            "solutioning",
            "epics-stories",
            "planning-artifacts/epics-stories-test.md",
        )

        state = load_status(project_with_completed_analysis_planning)
        assert state["phases"]["solutioning"]["epics-stories"].endswith(".md"), (
            "epics-stories slot should be complete"
        )

        # ── Step 3: solutioning-gate-check ───────────────────────────
        result = run_cmd(mock_ctx, "solutioning-gate-check")
        assert not _blocked(result), (
            f"solutioning-gate-check blocked: {result}"
        )

        mark_complete(
            project_with_completed_analysis_planning,
            "solutioning",
            "solutioning-gate-check",
            "planning-artifacts/solutioning-gate-test.md",
        )

        state = load_status(project_with_completed_analysis_planning)
        assert state["phases"]["solutioning"]["solutioning-gate-check"].endswith(".md")

        # ── Step 4: sprint-planning (solutioning → implementation) ───
        result = run_cmd(mock_ctx, "sprint-planning")
        assert not _blocked(result), (
            f"sprint-planning blocked: {result}"
        )

        mark_complete(
            project_with_completed_analysis_planning,
            "implementation",
            "sprint-planning",
            "planning-artifacts/sprint-planning-test.md",
        )

        state = load_status(project_with_completed_analysis_planning)
        assert state["phases"]["implementation"]["sprint-planning"].endswith(".md")

        # ── Step 5: create-story ─────────────────────────────────────
        result = run_cmd(mock_ctx, "create-story")
        assert not _blocked(result), (
            f"create-story blocked: {result}"
        )

        mark_complete(
            project_with_completed_analysis_planning,
            "implementation",
            "story",
            "implementation-artifacts/stories/story-001-test.md",
        )

        state = load_status(project_with_completed_analysis_planning)
        assert state["phases"]["implementation"]["story"].endswith(".md")

        # ── Step 6: dev-story ────────────────────────────────────────
        result = run_cmd(mock_ctx, "dev-story")
        assert not _blocked(result), (
            f"dev-story blocked: {result}"
        )

        mark_complete(
            project_with_completed_analysis_planning,
            "implementation",
            "dev",
            "implementation-artifacts/stories/dev-test-output.md",
        )

        state = load_status(project_with_completed_analysis_planning)
        assert state["phases"]["implementation"]["dev"].endswith(".md")

        # ── Step 7: code-review ──────────────────────────────────────
        result = run_cmd(mock_ctx, "code-review")
        assert not _blocked(result), (
            f"code-review blocked: {result}"
        )

        mark_complete(
            project_with_completed_analysis_planning,
            "implementation",
            "code-review",
            "implementation-artifacts/stories/code-review-test.md",
        )

        state = load_status(project_with_completed_analysis_planning)
        assert state["phases"]["implementation"]["code-review"].endswith(".md")

        # ── Final verification: all slots complete ───────────────────
        from plugins.bmad.lib.phases import next_required_slot

        nxt = next_required_slot(state, 2)
        assert nxt is None, (
            f"Expected no next required slot, got: {nxt}"
        )

    def test_solutioning_gate_blocked_when_prereqs_missing(self, mock_ctx):
        """solutioning-gate-check is blocked when architecture or epics-stories incomplete."""
        from plugins.bmad.lib import phases

        # Level 2 with analysis/planning done, solutioning empty
        state = {
            "level": 2,
            "phases": {
                "analysis": {"product-brief": "complete"},
                "planning": {"prd": "complete"},
                "solutioning": {},
                "implementation": {},
            },
        }

        ok, reason = phases.can_run("solutioning-gate-check", state, 2)
        assert not ok, "Should be blocked when solutioning prereqs missing"
        assert "architecture" in reason, "Reason should mention missing architecture slot"

    def test_implementation_blocked_when_solutioning_incomplete(self, mock_ctx):
        """Implementation commands are blocked when solutioning gate not done."""
        from plugins.bmad.lib import phases

        # Level 2 with analysis/planning done, solutioning incomplete
        state = {
            "level": 2,
            "phases": {
                "analysis": {"product-brief": "complete"},
                "planning": {"prd": "complete"},
                "solutioning": {"architecture": "complete"},
                # gate-check not done, epics-stories not done
                "implementation": {},
            },
        }

        ok, reason = phases.can_run("sprint-planning", state, 2)
        assert not ok, "Should be blocked when solutioning gate not passed"
        assert "solutioning" in reason, (
            "Reason should mention solutioning phase incompleteness"
        )

    def test_handler_body_returned(self, project_with_completed_analysis_planning, mock_ctx):
        """Each command handler returns the .md body content, not a block message.

        Walks the sequence marking each slot complete BEFORE invoking the
        next command so the handler's defense-in-depth phase gate allows the
        body through.
        """
        from plugins.bmad.lib.status import mark_complete

        sequence: list[tuple[str, str, str, str]] = [
            ("create-architecture", "solutioning", "architecture",
             "planning-artifacts/architecture-test.md"),
            ("epics-stories", "solutioning", "epics-stories",
             "planning-artifacts/epics-stories-test.md"),
            ("solutioning-gate-check", "solutioning", "solutioning-gate-check",
             "planning-artifacts/solutioning-gate-test.md"),
            ("sprint-planning", "implementation", "sprint-planning",
             "planning-artifacts/sprint-planning-test.md"),
            ("create-story", "implementation", "story",
             "implementation-artifacts/stories/story-001-test.md"),
            ("dev-story", "implementation", "dev",
             "implementation-artifacts/stories/dev-test-output.md"),
            ("code-review", "implementation", "code-review",
             "implementation-artifacts/stories/code-review-test.md"),
        ]
        for cmd, phase, slot, artifact in sequence:
            result = run_cmd(mock_ctx, cmd)
            assert not _blocked(result), f"{cmd} blocked: {result}"
            assert len(result) > 50, (
                f"{cmd} returned suspiciously short body ({len(result)} chars)"
            )
            mark_complete(
                project_with_completed_analysis_planning,
                phase, slot, artifact,
            )

    def test_subagent_log_persists(self, project_with_completed_analysis_planning):
        """The subagent log file can be written and read."""
        from plugins.bmad.lib import subagent_log

        entry = {
            "timestamp": "2026-05-17T12:00:00",
            "parent_skill": "bmad-create-architecture",
            "goal": "Design the component model",
            "task_id": "task-001",
            "status": "success",
            "artifacts": ["planning-artifacts/architecture-test.md"],
        }

        subagent_log.append(project_with_completed_analysis_planning, entry)
        recent = subagent_log.read_recent(project_with_completed_analysis_planning, limit=5)

        assert len(recent) >= 1
        assert recent[-1]["task_id"] == "task-001"
        assert recent[-1]["parent_skill"] == "bmad-create-architecture"

    def test_dashboard_renders_all_sections(self, project_with_completed_analysis_planning):
        """Dashboard handler renders all 3 sections without errors."""
        # Build a fully complete status
        from plugins.bmad.lib import status
        from plugins.bmad.lib.status import mark_complete

        artifacts_phases = [
            ("planning-artifacts/product-brief-test.md", "analysis", "product-brief"),
            ("planning-artifacts/prd-test.md", "planning", "prd"),
            ("planning-artifacts/architecture-test.md", "solutioning", "architecture"),
            ("planning-artifacts/epics-stories-test.md", "solutioning", "epics-stories"),
            ("planning-artifacts/solutioning-gate-test.md", "solutioning", "solutioning-gate-check"),
            ("planning-artifacts/sprint-planning-test.md", "implementation", "sprint-planning"),
            ("implementation-artifacts/stories/story-001.md", "implementation", "story"),
            ("implementation-artifacts/stories/dev-test.md", "implementation", "dev"),
            ("implementation-artifacts/stories/code-review-test.md", "implementation", "code-review"),
        ]
        for artifact, phase, slot in artifacts_phases:
            mark_complete(project_with_completed_analysis_planning, phase, slot, artifact)

        # Add a subagent log entry
        from plugins.bmad.lib import subagent_log
        subagent_log.append(project_with_completed_analysis_planning, {
            "timestamp": "2026-05-17T12:00:00",
            "parent_skill": "e2e-test",
            "goal": "Run E2E test",
            "task_id": "e2e-001",
            "status": "success",
            "artifacts": [],
        })

        # Call the dashboard handler
        from plugins.bmad.commands.dashboard import handler

        class MockCtx:
            working_directory = str(project_with_completed_analysis_planning)
            project_dir = str(project_with_completed_analysis_planning)
            profile_config = {}

        output = handler(MockCtx(), "")
        assert "Sub-Agent" in output or "sub-agent" in output.lower()
        assert "Phase" in output
        assert "✅" in output
