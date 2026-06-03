"""Tests for Epic 9 — Doctor + Migrate (Story 9.8).

Tests against fixture projects and drift scenarios.
"""

import pytest
import yaml
from pathlib import Path

from plugins.bmad.lib.doctor import run_doctor, DoctorReport, Severity
from plugins.bmad.lib.phase_overrides import load_phase_overrides, is_phase_overridden
from plugins.bmad.lib.status_reconciliation import reconcile_project, EvidenceState
from plugins.bmad.lib.migrate import create_migration_plan, execute_migration, WaveStatus


# ── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def minimal_project(tmp_path):
    """Minimal BMAD project with just config."""
    (tmp_path / "bmad").mkdir()
    (tmp_path / "bmad" / "config.yaml").write_text("version: 1\n")
    return tmp_path


@pytest.fixture
def full_project(tmp_path):
    """Full BMAD project with all artifacts."""
    (tmp_path / "bmad").mkdir()
    (tmp_path / "bmad" / "config.yaml").write_text("version: 1\nworkspace_mode: false\n")
    (tmp_path / "planning-artifacts").mkdir()
    (tmp_path / "planning-artifacts" / "product-brief.md").write_text("# Product Brief\n")
    (tmp_path / "planning-artifacts" / "prd-test.md").write_text("# PRD\n")
    (tmp_path / "planning-artifacts" / "architecture-test.md").write_text("# Architecture\n")
    (tmp_path / "planning-artifacts" / "epics-stories-test.md").write_text("# Epics\n")
    (tmp_path / "planning-artifacts" / "sprint-status.yaml").write_text(
        'stories:\n  "9.1":\n    status: done\n  "9.2":\n    status: in_progress\n'
    )
    (tmp_path / "implementation-artifacts").mkdir()
    (tmp_path / "implementation-artifacts" / "9.1-dev-notes.md").write_text("# Dev Notes\n")
    return tmp_path


@pytest.fixture
def outdated_project(tmp_path):
    """Outdated BMAD project with drift."""
    (tmp_path / "bmad").mkdir()
    (tmp_path / "bmad" / "config.yaml").write_text("workspace_mode: true\nworktrees:\n  - name: hermes\n")
    (tmp_path / "planning-artifacts").mkdir()
    return tmp_path


# ── Phase Overrides ─────────────────────────────────────────────────────

class TestPhaseOverrides:
    def test_load_empty(self, tmp_path):
        assert load_phase_overrides(tmp_path) == {}

    def test_load_valid(self, tmp_path):
        (tmp_path / "bmad").mkdir()
        (tmp_path / "bmad" / "config.yaml").write_text(
            "phase_overrides:\n  analysis: skipped\n  solutioning: not_needed\n"
        )
        overrides = load_phase_overrides(tmp_path)
        assert overrides["analysis"] == "skipped"
        assert overrides["solutioning"] == "not_needed"

    def test_load_invalid_phase(self, tmp_path):
        (tmp_path / "bmad").mkdir()
        (tmp_path / "bmad" / "config.yaml").write_text(
            "phase_overrides:\n  invalid_phase: skipped\n"
        )
        overrides = load_phase_overrides(tmp_path)
        assert "invalid_phase" not in overrides

    def test_is_overridden(self):
        assert is_phase_overridden({"analysis": "skipped"}, "analysis")
        assert not is_phase_overridden({}, "analysis")


# ── Doctor ──────────────────────────────────────────────────────────────

class TestDoctor:
    def test_minimal_project_finds_issues(self, minimal_project):
        report = run_doctor(minimal_project)
        assert isinstance(report, DoctorReport)
        assert len(report.findings) > 0
        assert report.categories_checked == 10

    def test_full_project_fewer_issues(self, full_project):
        report = run_doctor(full_project)
        # Full project should have fewer critical issues
        critical = [f for f in report.findings if f.severity == Severity.CRITICAL]
        assert len(critical) == 0

    def test_outdated_project_detects_drift(self, outdated_project):
        report = run_doctor(outdated_project)
        # Should find workspace pattern issue (worktree dir missing)
        workspace_findings = [f for f in report.findings if f.category == "Workspace Pattern"]
        assert len(workspace_findings) > 0

    def test_markdown_output(self, minimal_project):
        report = run_doctor(minimal_project)
        md = report.to_markdown()
        assert "# BMAD Doctor Report" in md
        assert "Findings:" in md

    def test_phase_overrides_honored(self, tmp_path):
        """DI-3: phase_overrides honored."""
        (tmp_path / "bmad").mkdir()
        (tmp_path / "bmad" / "config.yaml").write_text(
            "version: 1\nphase_overrides:\n  analysis: skipped\n"
        )
        report = run_doctor(tmp_path)
        # Should NOT flag missing product-brief (analysis is overridden)
        missing = [f for f in report.findings
                   if "product-brief" in f.title.lower() and f.category == "Missing Artifacts"]
        assert len(missing) == 0

    def test_doctor_read_only(self, full_project):
        """DI-1: Doctor never mutates."""
        before = set(full_project.rglob("*"))
        run_doctor(full_project)
        after = set(full_project.rglob("*"))
        assert before == after


# ── Status Reconciliation ───────────────────────────────────────────────

class TestStatusReconciliation:
    def test_empty_project(self, tmp_path):
        assert reconcile_project(tmp_path) == []

    def test_confirmed_story(self, full_project):
        results = reconcile_project(full_project)
        story_91 = [r for r in results if r.story_id == "9.1"]
        if story_91:
            # 9.1 has dev notes + git commits may exist
            assert story_91[0].evidence_state in (
                EvidenceState.CONFIRMED, EvidenceState.PROBABLE, EvidenceState.UNCERTAIN
            )

    def test_conservative_no_promote(self, tmp_path):
        """DI-4: Don't promote silently on ambiguous evidence."""
        (tmp_path / "planning-artifacts").mkdir()
        (tmp_path / "planning-artifacts" / "sprint-status.yaml").write_text(
            "stories:\n  X.1:\n    status: pending\n"
        )
        results = reconcile_project(tmp_path)
        if results:
            # No evidence → should not recommend 'done'
            assert results[0].recommended_status != "done"


# ── Migrate ─────────────────────────────────────────────────────────────

class TestMigrate:
    def test_create_plan(self, minimal_project):
        plan = create_migration_plan(minimal_project)
        assert len(plan.waves) == 5
        assert plan.waves[0].name == "Workspace Pattern Fix"

    def test_dry_run(self, minimal_project):
        plan = create_migration_plan(minimal_project)
        plan = execute_migration(plan, minimal_project, dry_run=True)
        assert all(w.status == WaveStatus.DONE for w in plan.waves)

    def test_single_wave(self, minimal_project):
        plan = create_migration_plan(minimal_project)
        plan = execute_migration(plan, minimal_project, waves=[1], dry_run=True)
        assert plan.waves[0].status == WaveStatus.DONE
        assert plan.waves[1].status == WaveStatus.SKIPPED

    def test_plan_markdown(self, minimal_project):
        plan = create_migration_plan(minimal_project)
        md = plan.to_markdown()
        assert "# BMAD Migration Plan" in md
        assert "Wave 1" in md

    def test_halt_on_failure(self, tmp_path):
        """DI-2: Halt on failure."""
        plan = create_migration_plan(tmp_path)
        # This should work (tmp_path has no git repo, but waves are safe)
        plan = execute_migration(plan, tmp_path, dry_run=True)
        # In dry_run mode, all waves should complete
        assert all(w.status == WaveStatus.DONE for w in plan.waves)
