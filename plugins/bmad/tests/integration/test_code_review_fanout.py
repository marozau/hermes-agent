"""Integration tests for /bmad:code-review 3-reviewer fan-out.

Mocks ``ctx.dispatch_tool("delegate_task", ...)`` to verify the handler:
  - issues exactly 3 delegate_task calls in full mode (with spec)
  - issues exactly 2 in no-spec mode (Acceptance Auditor skipped)
  - aggregates child summaries into the canonical Markdown report
  - falls back to legacy body return when --no-fanout is passed
  - respects guards (BMAD project, phase gate)
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest
import yaml

from plugins.bmad.commands.code_review import (
    handler,
    _parse_args,
    _build_goals,
    _capture_diff,
    _aggregate,
    _REVIEWERS,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _mock_ctx(project_dir: Path, captured_calls: list):
    class MockCtx:
        pass
    ctx = MockCtx()
    ctx.project_dir = str(project_dir)
    ctx.working_directory = str(project_dir)
    ctx.profile_config = {}

    def dispatch_tool(name, **kwargs):
        captured_calls.append((name, kwargs))
        # Determine which reviewer this is from the goal text
        goal = kwargs.get("goal", "")
        if "Blind Hunter" in goal:
            return {"task_id": "t-blind", "status": "success",
                    "summary": "- Finding 1\n- Finding 2\n- Finding 3"}
        if "Edge Case Hunter" in goal:
            return {"task_id": "t-edge", "status": "success",
                    "summary": '[{"location":"x.py:10","trigger_condition":"null input","guard_snippet":"if x is None: return","potential_consequence":"TypeError"}]'}
        if "Acceptance Auditor" in goal:
            return {"task_id": "t-audit", "status": "success",
                    "summary": "- AC-1 violated: missing validation"}
        return {"task_id": "t-?", "status": "success", "summary": "_(generic)_"}

    ctx.dispatch_tool = dispatch_tool
    return ctx


def _scaffold(tmp_path: Path) -> Path:
    """Build a BMAD project at level 2 with implementation phase open."""
    (tmp_path / "bmad").mkdir()
    yaml.safe_dump({
        "project_name": "fanout-test",
        "project_type": "api",
        "project_level": 2,
        "user_name": "tester",
    }, open(tmp_path / "bmad" / "config.yaml", "w"), sort_keys=False)
    (tmp_path / "planning-artifacts").mkdir()
    yaml.safe_dump({
        "project": "fanout-test",
        "level": 2,
        "created": "2026-05-21",
        "last_updated": "2026-05-21",
        "phases": {
            "analysis": {"product-brief": "planning-artifacts/brief.md"},
            "planning": {"prd": "planning-artifacts/prd.md"},
            "solutioning": {
                "architecture": "planning-artifacts/arch.md",
                "solutioning-gate-check": "planning-artifacts/sgc.md",
            },
            "implementation": {"sprint-planning": "planning-artifacts/sp.md"},
        },
    }, open(tmp_path / "planning-artifacts" / "workflow-status.yaml", "w"), sort_keys=False)
    return tmp_path


def _stub_diff(diff_text: str = "diff --git a/x.py b/x.py\n+def foo(): return 1\n"):
    """Return a context manager that stubs git diff."""
    def fake_run(cmd, *args, **kwargs):
        if cmd and cmd[0] == "git" and "diff" in cmd:
            class R:
                returncode = 0
                stdout = diff_text
                stderr = ""
            return R()
        raise FileNotFoundError(cmd)
    return mock.patch("plugins.bmad.commands.code_review.subprocess.run",
                      side_effect=fake_run)


# ── Fan-out core ────────────────────────────────────────────────────────────


class TestFanOutFullMode:
    """With --spec, all 3 reviewers spawn."""

    def test_full_mode_three_reviewers(self, tmp_path: Path):
        project = _scaffold(tmp_path)
        spec = project / "spec.md"
        spec.write_text("# Spec\n\nAC-1: x must validate input.")
        calls: list = []
        ctx = _mock_ctx(project, calls)

        with _stub_diff():
            out = handler(ctx, f"--spec {spec.name}")

        # 3 delegate_task calls
        assert len(calls) == 3, f"Expected 3 calls, got {len(calls)}: {[c[0] for c in calls]}"
        assert all(name == "delegate_task" for name, _ in calls)

        # Each reviewer addressed in the goal text
        goals = [kw.get("goal", "") for _, kw in calls]
        assert any("Blind Hunter" in g for g in goals)
        assert any("Edge Case Hunter" in g for g in goals)
        assert any("Acceptance Auditor" in g for g in goals)

        # Spec inlined in the auditor goal
        auditor_goal = next(g for g in goals if "Acceptance Auditor" in g)
        assert "AC-1" in auditor_goal

        # Aggregated output mentions all three role headings
        assert "Blind Hunter" in out
        assert "Edge Case Hunter" in out
        assert "Acceptance Auditor" in out
        # Findings from each child should appear
        assert "Finding 1" in out  # blind
        assert "null input" in out  # edge
        assert "AC-1 violated" in out  # auditor
        # Triage section present
        assert "MUST FIX" in out


class TestFanOutNoSpecMode:
    """Without --spec, Acceptance Auditor is skipped (2 reviewers)."""

    def test_no_spec_skips_auditor(self, tmp_path: Path):
        project = _scaffold(tmp_path)
        calls: list = []
        ctx = _mock_ctx(project, calls)

        with _stub_diff():
            out = handler(ctx, "")

        assert len(calls) == 2, f"Expected 2 calls (no spec), got {len(calls)}"
        goals = [kw.get("goal", "") for _, kw in calls]
        assert any("Blind Hunter" in g for g in goals)
        assert any("Edge Case Hunter" in g for g in goals)
        # Auditor NOT invoked
        assert not any("Acceptance Auditor" in g for g in goals)
        # Mode noted in output
        assert "no-spec" in out


# ── Guards ───────────────────────────────────────────────────────────────────


class TestGuards:
    def test_outside_bmad_project_refuses(self, tmp_path: Path):
        calls: list = []
        ctx = _mock_ctx(tmp_path, calls)  # no bmad/config.yaml
        out = handler(ctx, "")
        assert "Not a BMAD project" in out
        assert len(calls) == 0, "Should not have fanned out"

    def test_phase_gate_blocks(self, tmp_path: Path):
        # Level 2 but architecture missing → code-review (implementation) blocked
        (tmp_path / "bmad").mkdir()
        yaml.safe_dump({
            "project_name": "blocked-test",
            "project_type": "api",
            "project_level": 2,
            "user_name": "t",
        }, open(tmp_path / "bmad" / "config.yaml", "w"), sort_keys=False)
        (tmp_path / "planning-artifacts").mkdir()
        yaml.safe_dump({
            "project": "blocked-test", "level": 2,
            "created": "2026-05-21", "last_updated": "2026-05-21",
            "phases": {
                "analysis": {"product-brief": "not-started"},
                "planning": {}, "solutioning": {}, "implementation": {},
            },
        }, open(tmp_path / "planning-artifacts" / "workflow-status.yaml", "w"), sort_keys=False)

        calls: list = []
        ctx = _mock_ctx(tmp_path, calls)
        out = handler(ctx, "")
        assert "blocked" in out.lower()
        assert len(calls) == 0


class TestNoFanOutFlag:
    """--no-fanout returns the original prompt body, no delegation."""

    def test_legacy_mode_returns_body(self, tmp_path: Path):
        project = _scaffold(tmp_path)
        calls: list = []
        ctx = _mock_ctx(project, calls)
        out = handler(ctx, "--no-fanout")
        assert len(calls) == 0, "Legacy mode must not fan out"
        # Original body has these markers
        assert "Code Review" in out or "code-review" in out or "review" in out.lower()


class TestDiffMissing:
    """Empty diff is reported, not silently fanned out against nothing."""

    def test_empty_diff_returns_warning(self, tmp_path: Path):
        project = _scaffold(tmp_path)
        calls: list = []
        ctx = _mock_ctx(project, calls)
        with _stub_diff(diff_text=""):
            out = handler(ctx, "")
        assert "No diff" in out
        assert len(calls) == 0


# ── Args parsing ────────────────────────────────────────────────────────────


class TestArgsParser:
    def test_defaults(self):
        p = _parse_args("")
        assert p["no_fanout"] is False
        assert p["diff_rev"] == "HEAD~1..HEAD"
        assert p["spec_path"] is None

    def test_no_fanout_flag(self):
        assert _parse_args("--no-fanout")["no_fanout"] is True

    def test_diff_override(self):
        assert _parse_args("--diff main..HEAD")["diff_rev"] == "main..HEAD"

    def test_spec_path(self):
        assert _parse_args("--spec docs/spec.md")["spec_path"] == "docs/spec.md"

    def test_multiple_flags(self):
        p = _parse_args("--no-fanout --diff x..y --spec s.md")
        assert p["no_fanout"]
        assert p["diff_rev"] == "x..y"
        assert p["spec_path"] == "s.md"


# ── Goal building ───────────────────────────────────────────────────────────


class TestGoalBuilding:
    def test_blind_goal_has_no_project_context_hint(self):
        diff = "diff --git a/x b/x\n+pass"
        meta = {"files_changed": 1, "insertions": 1, "deletions": 0}
        goals = _build_goals(diff, meta, "", Path("/tmp/p"))
        blind = goals[0]
        assert "Blind Hunter" in blind
        assert "DO NOT read project files" in blind

    def test_edge_goal_grants_read_access(self):
        diff = "diff --git a/x b/x\n+pass"
        meta = {"files_changed": 1, "insertions": 1, "deletions": 0}
        goals = _build_goals(diff, meta, "", Path("/tmp/p"))
        edge = goals[1]
        assert "Edge Case Hunter" in edge
        assert "Read/Grep/Glob" in edge

    def test_audit_goal_includes_spec_when_present(self):
        diff = "diff --git a/x b/x\n+pass"
        meta = {"files_changed": 1, "insertions": 1, "deletions": 0}
        goals = _build_goals(diff, meta, "# Spec\nAC-1", Path("/tmp/p"))
        audit = goals[2]
        assert "Acceptance Auditor" in audit
        assert "AC-1" in audit

    def test_diff_truncation_above_12kb(self):
        big_diff = "+" + "x" * 20_000
        meta = {"files_changed": 1, "insertions": 1, "deletions": 0}
        goals = _build_goals(big_diff, meta, "", Path("/tmp/p"))
        for g in goals:
            assert "diff truncated" in g
            assert len(g) < 16_000  # bounded


# ── Aggregation ──────────────────────────────────────────────────────────────


class TestAggregate:
    def test_renders_per_reviewer_section(self):
        reviewers = _REVIEWERS[:2]
        results = [
            {"summary": "blind findings", "status": "success"},
            {"summary": "edge findings", "status": "success"},
        ]
        meta = {"rev": "HEAD~1..HEAD", "files_changed": 1, "insertions": 5, "deletions": 2}
        out = _aggregate(reviewers, results, meta, "no-spec")
        assert "Blind Hunter" in out
        assert "blind findings" in out
        assert "Edge Case Hunter" in out
        assert "edge findings" in out
        assert "MUST FIX" in out

    def test_marks_failed_child(self):
        reviewers = _REVIEWERS[:1]
        results = [{"summary": "boom", "status": "failure", "error": True}]
        meta = {"rev": "HEAD~1..HEAD", "files_changed": 0, "insertions": 0, "deletions": 0}
        out = _aggregate(reviewers, results, meta, "no-spec")
        assert "Sub-agent failed" in out


# ── Diff capture ─────────────────────────────────────────────────────────────


class TestCaptureDiff:
    def test_no_git_returns_empty(self, tmp_path: Path):
        with mock.patch(
            "plugins.bmad.commands.code_review.subprocess.run",
            side_effect=FileNotFoundError("git"),
        ):
            text, meta = _capture_diff(tmp_path, "HEAD~1..HEAD")
        assert text == ""
        assert "error" in meta

    def test_counts_files_and_lines(self, tmp_path: Path):
        sample_diff = (
            "diff --git a/x.py b/x.py\n"
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "+added line 1\n"
            "+added line 2\n"
            "-removed line\n"
            "diff --git a/y.py b/y.py\n"
            "+another addition\n"
        )
        with _stub_diff(diff_text=sample_diff):
            text, meta = _capture_diff(tmp_path, "HEAD~1..HEAD")
        assert text == sample_diff
        assert meta["files_changed"] == 2
        assert meta["insertions"] == 3  # +added 1, +added 2, +another (excludes +++ b/...)
        assert meta["deletions"] == 1
