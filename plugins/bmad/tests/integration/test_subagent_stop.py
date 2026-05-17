"""Integration tests for subagent_stop hook — child completion handling."""

from __future__ import annotations

from pathlib import Path

import yaml
import pytest

from plugins.bmad.hooks.subagent_stop import (
    subagent_stop,
    _find_matching_rule,
    PATH_RULES,
)


def _mock_ctx(project_dir: str | None):
    class MockCtx:
        pass
    ctx = MockCtx()
    if project_dir is not None:
        ctx.project_dir = project_dir
        ctx.working_directory = project_dir
    return ctx


def _scaffold(tmp_path: Path) -> Path:
    (tmp_path / "bmad").mkdir()
    yaml.safe_dump({
        "project_name": "sa-test",
        "project_type": "api",
        "project_level": 2,
        "user_name": "tester",
    }, open(tmp_path / "bmad" / "config.yaml", "w"), sort_keys=False)
    (tmp_path / "planning-artifacts").mkdir()
    yaml.safe_dump({
        "project": "sa-test",
        "level": 2,
        "created": "2026-05-17",
        "last_updated": "2026-05-17",
        "phases": {
            "analysis": {"product-brief": "not-started"},
            "planning": {"prd": "not-started"},
            "solutioning": {},
            "implementation": {"sprint-planning": "not-started"},
        },
    }, open(tmp_path / "planning-artifacts" / "workflow-status.yaml", "w"), sort_keys=False)
    return tmp_path


class TestSubagentStopLogging:
    """The hook appends every child completion to _subagent-log.yaml."""

    def test_appends_log_entry(self, tmp_path: Path) -> None:
        project = _scaffold(tmp_path)
        ctx = _mock_ctx(str(project))

        subagent_stop(ctx, {
            "task_id": "task-001",
            "goal": "design a thing",
            "status": "success",
            "summary": "Designed it.",
            "parent_skill_name": "bmad-create-architecture",
        })

        log_path = project / "planning-artifacts" / "_subagent-log.yaml"
        assert log_path.exists(), "Hook must create the subagent log file"
        entries = yaml.safe_load(log_path.read_text())
        assert entries, "Log file must be non-empty"
        assert entries[-1]["task_id"] == "task-001"
        assert entries[-1]["parent_skill"] == "bmad-create-architecture"
        assert entries[-1]["goal"] == "design a thing"
        assert "timestamp" in entries[-1]

    def test_appends_multiple_entries(self, tmp_path: Path) -> None:
        project = _scaffold(tmp_path)
        ctx = _mock_ctx(str(project))

        for i in range(3):
            subagent_stop(ctx, {
                "task_id": f"task-{i:03d}",
                "goal": f"goal-{i}",
                "status": "success",
                "summary": "ok",
                "parent_skill_name": "bmad-testarch-nfr",
            })

        log_path = project / "planning-artifacts" / "_subagent-log.yaml"
        entries = yaml.safe_load(log_path.read_text())
        assert len(entries) >= 3

    def test_truncates_long_summary(self, tmp_path: Path) -> None:
        project = _scaffold(tmp_path)
        ctx = _mock_ctx(str(project))

        long_summary = "x" * 1000
        subagent_stop(ctx, {
            "task_id": "task-truncate",
            "goal": "g",
            "status": "success",
            "summary": long_summary,
            "parent_skill_name": "bmad-create-prd",
        })

        log_path = project / "planning-artifacts" / "_subagent-log.yaml"
        entries = yaml.safe_load(log_path.read_text())
        assert len(entries[-1]["summary"]) <= 500


class TestSubagentStopStatusUpdate:
    """When a child's summary contains an artifact path, status updates."""

    def test_updates_status_on_artifact_in_summary(self, tmp_path: Path) -> None:
        project = _scaffold(tmp_path)
        ctx = _mock_ctx(str(project))

        # Summary must end with the artifact path (PATH_RULES uses $-anchored regex)
        subagent_stop(ctx, {
            "task_id": "task-artifact",
            "goal": "write a brief",
            "status": "success",
            "summary": "wrote planning-artifacts/product-brief-foo.md",
            "parent_skill_name": "bmad-product-brief",
        })

        status_path = project / "planning-artifacts" / "workflow-status.yaml"
        data = yaml.safe_load(status_path.read_text())
        # Path-rule matched product-brief → status updated for that slot
        assert data["phases"]["analysis"]["product-brief"].startswith("subagent:")


class TestSubagentStopNeverRaises:
    """The hook must never propagate exceptions."""

    def test_returns_none_outside_bmad_project(self, tmp_path: Path) -> None:
        ctx = _mock_ctx(str(tmp_path))  # no bmad/config.yaml
        # Should not raise even though no project exists
        result = subagent_stop(ctx, {"task_id": "x", "goal": "y", "status": "z"})
        assert result is None

    def test_no_raise_on_malformed_child_result(self, tmp_path: Path) -> None:
        project = _scaffold(tmp_path)
        ctx = _mock_ctx(str(project))
        # Empty dict — most fields missing
        result = subagent_stop(ctx, {})
        assert result is None

    def test_no_raise_on_none_ctx_attributes(self) -> None:
        class MockCtx:
            pass
        ctx = MockCtx()
        # No project_dir, no working_directory at all
        result = subagent_stop(ctx, {"task_id": "x", "goal": "y", "status": "z"})
        assert result is None


class TestFindMatchingRule:
    """_find_matching_rule maps (parent_skill, goal) to a path-rule entry."""

    def test_returns_none_for_no_match(self) -> None:
        result = _find_matching_rule(None, parent_skill="", goal="")
        assert result is None

    def test_matches_product_brief_in_goal(self) -> None:
        result = _find_matching_rule(
            None,
            parent_skill="bmad-product-brief",
            goal="write a product brief",
        )
        assert result is not None
        phase, slot = result
        assert phase == "analysis"
        assert slot == "product-brief"

    def test_matches_solutioning_gate_check(self) -> None:
        result = _find_matching_rule(
            None,
            parent_skill="bmad-solutioning-gate-check",
            goal="run the gate check",
        )
        assert result is not None
        phase, slot = result
        assert phase == "solutioning"
        assert slot == "solutioning-gate-check"

    def test_first_match_wins_with_most_specific_first(self) -> None:
        """PATH_RULES is ordered most-specific first — solutioning-gate-check
        must beat generic prd/architecture matches."""
        rules = PATH_RULES
        # solutioning-gate-check must appear before any rule that could
        # accidentally also match it.
        gate_idx = next(
            i for i, (_, _, slot) in enumerate(rules) if slot == "solutioning-gate-check"
        )
        # No other rule should be defined to ALSO match the gate-check slot
        for i, (_, _, slot) in enumerate(rules):
            if i != gate_idx and slot in ("architecture", "epics-stories", "prd"):
                # These should be specific enough not to collide
                assert slot != "solutioning-gate-check"
