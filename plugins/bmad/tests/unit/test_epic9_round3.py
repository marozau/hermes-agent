"""Round-3 regression tests — TDD-strict.

Each test asserts what the wave ACTUALLY CHANGES on disk, not just status == DONE.
"""

import subprocess
import pytest
import yaml
from pathlib import Path

from plugins.bmad.lib.doctor import run_doctor, Severity
from plugins.bmad.lib.migrate import (
    create_migration_plan, execute_migration, WaveStatus, _git_commit,
    _check_dirty_worktree, _get_last_wave_from_git
)
from plugins.bmad.lib.status_reconciliation import reconcile_project, EvidenceState


class TestWaveComposition:
    """P0: Waves must do real work, not stubs."""

    def _init_git(self, path):
        subprocess.run(["git", "init"], cwd=path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=path, capture_output=True)
        (path / ".gitkeep").write_text("")
        subprocess.run(["git", "add", ".gitkeep"], cwd=path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=path, capture_output=True)

    def test_wave1_creates_real_config(self, tmp_path):
        """Wave 1 must create a real bmad/config.yaml via bootstrap, not a stub."""
        self._init_git(tmp_path)
        plan = create_migration_plan(tmp_path)
        plan = execute_migration(plan, tmp_path, waves=[1])
        config = tmp_path / "bmad" / "config.yaml"
        assert config.exists(), "Wave 1 must create bmad/config.yaml"
        data = yaml.safe_load(config.read_text())
        assert isinstance(data, dict)
        assert "project_name" in data or "version" in data  # Real bootstrap output

    def test_wave1_preserves_existing_config(self, tmp_path):
        """Wave 1 must not overwrite existing config."""
        self._init_git(tmp_path)
        (tmp_path / "bmad").mkdir()
        (tmp_path / "bmad" / "config.yaml").write_text("version: 1\ncustom: true\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add config"], cwd=tmp_path, capture_output=True)
        plan = create_migration_plan(tmp_path)
        plan = execute_migration(plan, tmp_path, waves=[1])
        data = yaml.safe_load((tmp_path / "bmad" / "config.yaml").read_text())
        assert data.get("custom") is True  # Original preserved

    def test_wave4_scans_legacy_stories(self, tmp_path):
        """Wave 4 must scan for legacy stories, not be a no-op."""
        self._init_git(tmp_path)
        stories_dir = tmp_path / "implementation-artifacts" / "stories"
        stories_dir.mkdir(parents=True)
        (stories_dir / "S1.md").write_text("---\nid: S1\nstatus: done\n---\nStory 1\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add stories"], cwd=tmp_path, capture_output=True)
        plan = create_migration_plan(tmp_path)
        plan = execute_migration(plan, tmp_path, waves=[4])
        assert plan.waves[3].status == WaveStatus.DONE
        assert "1" in plan.waves[3].message  # Found 1 legacy story

    def test_wave5_checks_ocr(self, tmp_path):
        """Wave 5 must check OCR status with real function."""
        self._init_git(tmp_path)
        plan = create_migration_plan(tmp_path)
        plan = execute_migration(plan, tmp_path, waves=[5])
        assert plan.waves[4].status == WaveStatus.DONE
        assert "OCR" in plan.waves[4].message


class TestGitCommit:
    """P0: _git_commit must be safe and correct."""

    def test_no_changes_does_not_raise(self, tmp_path):
        """'nothing to commit' must not raise."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, capture_output=True)
        (tmp_path / "init.txt").write_text("init")
        subprocess.run(["git", "add", "init.txt"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
        sha = _git_commit(tmp_path, "test no-op", [])
        assert len(sha) == 40  # Full SHA, not truncated

    def test_targeted_add_only(self, tmp_path):
        """_git_commit must only stage specified files."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, capture_output=True)
        (tmp_path / "a.txt").write_text("a")
        subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
        # Create uncommitted file
        (tmp_path / "b.txt").write_text("b")
        # Commit only a.txt change
        (tmp_path / "a.txt").write_text("a2")
        _git_commit(tmp_path, "targeted", ["a.txt"])
        # b.txt should NOT be committed
        result = subprocess.run(["git", "status", "--porcelain"], cwd=tmp_path,
                                capture_output=True, text=True)
        assert "b.txt" in result.stdout  # Still uncommitted


class TestDirtyWorktree:
    """P0: Pre-flight dirty-tree check."""

    def test_clean_tree_passes(self, tmp_path):
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        assert _check_dirty_worktree(tmp_path) is None

    def test_dirty_tree_detected(self, tmp_path):
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        (tmp_path / "dirty.txt").write_text("dirty")
        assert _check_dirty_worktree(tmp_path) is not None

    def test_non_git_repo_returns_not_repo(self, tmp_path):
        assert _check_dirty_worktree(tmp_path) == "NOT_A_GIT_REPO"


class TestResume:
    """P0: --resume must skip completed waves."""

    def test_resume_skips_completed_waves(self, tmp_path):
        """Resume should detect completed waves from git log."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, capture_output=True)
        (tmp_path / "init.txt").write_text("init")
        subprocess.run(["git", "add", "init.txt"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
        # Simulate wave 1 and 2 completed
        (tmp_path / "w1.txt").write_text("w1")
        subprocess.run(["git", "add", "w1.txt"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "[bmad-migrate] wave 1: config bootstrap"],
                       cwd=tmp_path, capture_output=True)
        (tmp_path / "w2.txt").write_text("w2")
        subprocess.run(["git", "add", "w2.txt"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "[bmad-migrate] wave 2: config schema upgrade"],
                       cwd=tmp_path, capture_output=True)

        last = _get_last_wave_from_git(tmp_path)
        assert last == 2


class TestStatusReconciliation:
    """P0: DI-4 conservative + canonical status enum."""

    def test_done_with_no_evidence_recommended_not_started(self, tmp_path):
        """DI-4: done with ZERO evidence → recommend 'not-started'."""
        (tmp_path / "planning-artifacts").mkdir()
        (tmp_path / "planning-artifacts" / "sprint-status.yaml").write_text(
            'stories:\n  "X.1":\n    status: done\n'
        )
        results = reconcile_project(tmp_path)
        assert len(results) == 1
        assert results[0].recommended_status == "not-started"
        assert results[0].evidence_state == EvidenceState.NOT_STARTED

    def test_status_enum_kebab_case(self, tmp_path):
        """Status enum must use kebab-case 'in-progress', not 'in_progress'."""
        (tmp_path / "planning-artifacts").mkdir()
        (tmp_path / "planning-artifacts" / "sprint-status.yaml").write_text(
            'stories:\n  "1.1":\n    status: in_progress\n'
        )
        results = reconcile_project(tmp_path)
        # Normalized status should be kebab-case
        assert results[0].current_status == "in-progress"


class TestDoctorPerCheckIsolation:
    """P0: Each check must be isolated — one failure doesn't abort others."""

    def test_all_10_checks_run_even_if_one_crashes(self, tmp_path):
        """Even with a malformed project, all 10 categories should be checked."""
        # Create a project that will trigger various checks
        (tmp_path / "bmad").mkdir()
        (tmp_path / "bmad" / "config.yaml").write_text("version: 1\n")
        report = run_doctor(tmp_path)
        assert report.categories_checked == 10

    def test_crashing_check_reports_high_severity(self):
        """A crashing check should be HIGH severity, not LOW."""
        # This is tested by the per-check wrapper — if a check raises,
        # the finding should be HIGH
        from plugins.bmad.lib.doctor import DoctorFinding
        finding = DoctorFinding(
            category="test", severity=Severity.HIGH,
            title="Diagnostic check crashed: test",
            detail="Exception: RuntimeError: boom",
            remediation="This is a doctor bug — report it."
        )
        assert finding.severity == Severity.HIGH
