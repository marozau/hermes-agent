"""Round-5 regression tests — TDD-strict.

Fixes: 3 false-greens + regex + warning + partial-resume SHA.
"""

import subprocess
import pytest
import yaml
from pathlib import Path

from plugins.bmad.lib.migrate import (
    create_migration_plan, execute_migration, WaveStatus, _git_commit,
    _check_dirty_worktree, _get_last_wave_from_git, _get_wave_shas_from_git
)
from plugins.bmad.lib.status_reconciliation import reconcile_project, _check_git_commits
from plugins.bmad.lib.doctor import run_doctor, Severity


class TestFalseGreenFixes:
    """Fix 3 false-green tests from round-4."""

    def test_resume_preserves_shas_in_partial_resume(self, tmp_path):
        """ER3-13: partial-resume must show SHAs for completed waves."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, capture_output=True)
        (tmp_path / "init.txt").write_text("init")
        subprocess.run(["git", "add", "init.txt"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
        # Simulate waves 1-2 completed
        (tmp_path / "bmad").mkdir()
        (tmp_path / "bmad" / "config.yaml").write_text("version: 1\n")
        subprocess.run(["git", "add", "bmad/config.yaml"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "[bmad-migrate] wave 1: config bootstrap"],
                       cwd=tmp_path, capture_output=True)
        (tmp_path / "planning-artifacts").mkdir()
        (tmp_path / "planning-artifacts" / ".gitkeep").write_text("")
        subprocess.run(["git", "add", "planning-artifacts"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "[bmad-migrate] wave 2: config schema upgrade"],
                       cwd=tmp_path, capture_output=True)
        # Get expected SHA for wave 1
        result = subprocess.run(["git", "log", "--format=%H", "--grep", "\\[bmad-migrate\\] wave 1", "-1"],
                                cwd=tmp_path, capture_output=True, text=True)
        expected_sha = result.stdout.strip()
        # Resume — waves 1-2 should have SHAs, wave 3+ should run
        plan = create_migration_plan(tmp_path)
        plan = execute_migration(plan, tmp_path, resume=True)
        assert plan.waves[0].status == WaveStatus.SKIPPED
        assert plan.waves[0].commit_sha == expected_sha, \
            f"Wave 1 SHA should be {expected_sha}, got '{plan.waves[0].commit_sha}'"
        assert plan.waves[1].status == WaveStatus.SKIPPED
        assert plan.waves[1].commit_sha != "", "Wave 2 SHA should be preserved"



    def test_resume_preserves_shas_when_all_done(self, tmp_path):
        """All-done resume (early-exit branch) must preserve SHAs."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, capture_output=True)
        (tmp_path / "init.txt").write_text("init")
        subprocess.run(["git", "add", "init.txt"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
        # Simulate all 5 waves completed
        for w in range(1, 6):
            (tmp_path / f"w{w}.txt").write_text(f"w{w}")
            subprocess.run(["git", "add", f"w{w}.txt"], cwd=tmp_path, capture_output=True)
            subprocess.run(["git", "commit", "-m", f"[bmad-migrate] wave {w}: test wave"],
                           cwd=tmp_path, capture_output=True)
        # Get expected SHA for wave 1
        r = subprocess.run(["git", "log", "--format=%H", "--grep", "\[bmad-migrate\] wave 1", "-1"],
                           cwd=tmp_path, capture_output=True, text=True)
        expected_sha = r.stdout.strip()
        plan = create_migration_plan(tmp_path)
        plan = execute_migration(plan, tmp_path, resume=True)
        assert plan.waves[0].status == WaveStatus.DONE
        assert plan.waves[0].commit_sha == expected_sha

    def test_promotion_check_asserts_recommended(self, tmp_path):
        """ER3-5: not-started with 2/3 evidence should recommend in-progress."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, capture_output=True)
        (tmp_path / "planning-artifacts").mkdir()
        (tmp_path / "planning-artifacts" / "sprint-status.yaml").write_text(
            'stories:\n  "1.1":\n    status: not-started\n'
        )
        (tmp_path / "implementation-artifacts").mkdir()
        (tmp_path / "implementation-artifacts" / "1.1-dev-notes.md").write_text("notes")
        # Commit mentioning story 1.1 → 2/3 evidence (file + commit)
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "feat(1.1): implement login"],
                       cwd=tmp_path, capture_output=True)
        results = reconcile_project(tmp_path)
        assert len(results) == 1
        # 2/3 evidence (PARTIAL) → should recommend in-progress
        assert results[0].recommended_status == "in-progress", \
            f"Expected 'in-progress', got '{results[0].recommended_status}'"

    def test_wave4_details_use_real_keys(self, tmp_path):
        """ER3-7: wave 4 details must use actual dict keys (id/title), not 'file'."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, capture_output=True)
        stories_dir = tmp_path / "implementation-artifacts" / "stories"
        stories_dir.mkdir(parents=True)
        # Use a filename pattern that _scan_legacy_stories recognizes
        (stories_dir / "1.1-implement-login.md").write_text(
            "---\nid: 1.1\ntitle: Implement Login\nstatus: done\n---\nStory body\n"
        )
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add stories"], cwd=tmp_path, capture_output=True)
        plan = create_migration_plan(tmp_path)
        plan = execute_migration(plan, tmp_path, waves=[4])
        assert plan.waves[3].status == WaveStatus.DONE
        # Details must not contain 'unknown' or '?'
        details = plan.waves[3].details or ""
        message = plan.waves[3].message or ""
        assert "unknown" not in details.lower(), f"Found 'unknown' in details: {details}"
        assert "?" not in details, f"Dict key mismatch — got '?' in: {details}"


class TestRegexFix:
    """ER3-1: post-filter must catch bare versions, not just v-prefixed."""

    def test_rejects_bare_version_string(self, tmp_path):
        """Must NOT match 'bump to 9.1.0' when searching for story 9.1."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, capture_output=True)
        (tmp_path / "f.txt").write_text("x")
        subprocess.run(["git", "add", "f.txt"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "chore: bump to 9.1.0"],
                       cwd=tmp_path, capture_output=True)
        assert _check_git_commits(tmp_path, "9.1") is False

    def test_rejects_rc_version(self, tmp_path):
        """Must NOT match '9.1.0-rc1' when searching for story 9.1."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, capture_output=True)
        (tmp_path / "f.txt").write_text("x")
        subprocess.run(["git", "add", "f.txt"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "release 9.1.0-rc1"],
                       cwd=tmp_path, capture_output=True)
        assert _check_git_commits(tmp_path, "9.1") is False


class TestWarningFix:
    """NIT-2: --resume --wave must actually execute wave N."""

    def test_resume_wave_executes_specified_wave(self, tmp_path):
        """--resume --wave 3 should execute wave 3 (drop resume)."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, capture_output=True)
        (tmp_path / "init.txt").write_text("init")
        subprocess.run(["git", "add", "init.txt"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
        plan = create_migration_plan(tmp_path)
        # --resume --wave 3 should execute wave 3, not warn
        plan = execute_migration(plan, tmp_path, waves=[3], resume=True)
        # Wave 3 should have executed (not SKIPPED or warned)
        assert plan.waves[2].status == WaveStatus.DONE
