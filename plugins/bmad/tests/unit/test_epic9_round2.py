"""Round-2 regression tests — TDD (test first, fix after).

8 P0 + 7 P1 findings from Epic 9 round-1 code review.
"""

import subprocess
import pytest
import yaml
from pathlib import Path

from plugins.bmad.lib.doctor import run_doctor, Severity
from plugins.bmad.lib.migrate import create_migration_plan, execute_migration, WaveStatus, _git_commit
from plugins.bmad.lib.status_reconciliation import reconcile_project, EvidenceState


# ── P0-1/2/3: Waves must compose existing machinery ─────────────────────

class TestWaveComposition:
    """P0: Waves 1/4/5 must actually do work, not stub."""

    def _init_git(self, path):
        """Initialize a real git repo for testing."""
        import subprocess
        subprocess.run(["git", "init"], cwd=path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                       cwd=path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"],
                       cwd=path, capture_output=True)
        # Initial commit so HEAD exists
        (path / ".gitkeep").write_text("")
        subprocess.run(["git", "add", ".gitkeep"], cwd=path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=path, capture_output=True)

    def test_wave1_creates_workspace_config_if_missing(self, tmp_path):
        """Wave 1 should ensure bmad/config.yaml exists with workspace_mode."""
        self._init_git(tmp_path)
        plan = create_migration_plan(tmp_path)
        plan = execute_migration(plan, tmp_path, waves=[1])
        config = tmp_path / "bmad" / "config.yaml"
        assert config.exists(), "Wave 1 must create bmad/config.yaml"
        data = yaml.safe_load(config.read_text())
        assert isinstance(data, dict)

    def test_wave4_runs_story_consolidation(self, tmp_path):
        """Wave 4 must attempt story consolidation (not be a stub)."""
        self._init_git(tmp_path)
        (tmp_path / "bmad").mkdir()
        (tmp_path / "bmad" / "config.yaml").write_text("version: 1\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add bmad"], cwd=tmp_path, capture_output=True)
        plan = create_migration_plan(tmp_path)
        plan = execute_migration(plan, tmp_path, waves=[4])
        assert plan.waves[3].status == WaveStatus.DONE

    def test_wave5_checks_ocr(self, tmp_path):
        """Wave 5 must check OCR status (not be a stub)."""
        self._init_git(tmp_path)
        plan = create_migration_plan(tmp_path)
        plan = execute_migration(plan, tmp_path, waves=[5])
        assert plan.waves[4].status == WaveStatus.DONE


# ── P0-4/5: git commit behavior ─────────────────────────────────────────

class TestGitCommit:
    """P0: _git_commit must handle no-op commits and not stage user work."""

    def test_git_commit_no_changes_is_not_error(self, tmp_path):
        """P0-4: 'nothing to commit' should not raise."""
        import subprocess
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                       cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"],
                       cwd=tmp_path, capture_output=True)
        # Create initial commit so HEAD exists
        (tmp_path / "init.txt").write_text("init")
        subprocess.run(["git", "add", "init.txt"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)

        # Now try to commit with no changes — should not raise
        sha = _git_commit(tmp_path, "test no-op")
        assert sha  # returns HEAD sha
