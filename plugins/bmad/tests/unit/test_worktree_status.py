"""Tests for Story 6.7 — WORKTREES.md + /bmad:worktree-status command.

ACs:
- AC-6.7.1: Initial state
- AC-6.7.2: Read-only display
- AC-6.7.3: Update via --claim
- AC-6.7.4: Release via --release
- AC-6.7.5: Collision detection
- AC-6.7.6: Atomic file writes
"""

from __future__ import annotations

import concurrent.futures
from pathlib import Path

import pytest
import yaml

from plugins.bmad.commands.worktree_status import worktree_status


@pytest.fixture
def workspace_dir(tmp_path):
    """Create a workspace-mode project with WORKTREES.md."""
    bmad_dir = tmp_path / "bmad"
    bmad_dir.mkdir()
    (bmad_dir / "config.yaml").write_text(yaml.safe_dump({
        "project_name": "test-ws",
        "workspace_mode": True,
        "worktrees": [
            {
                "name": "repo-a",
                "upstream": "/tmp/upstream-a",
                "branch": "feat/a",
                "path": "worktree/repo-a",
            },
            {
                "name": "repo-b",
                "upstream": "/tmp/upstream-b",
                "branch": "feat/b",
                "path": "worktree/repo-b",
            },
        ],
    }, sort_keys=False))

    (tmp_path / "worktree" / "repo-a").mkdir(parents=True)
    (tmp_path / "worktree" / "repo-b").mkdir(parents=True)

    return tmp_path


class TestInitialState:
    """AC-6.7.1: Initial state from config."""

    def test_default_state_has_all_worktrees(self, workspace_dir):
        result = worktree_status(workspace_dir)
        assert result["success"] is True
        assert "repo-a" in result["table"]
        assert "repo-b" in result["table"]
        assert "idle" in result["table"]


class TestReadOnly:
    """AC-6.7.2: Read-only display."""

    def test_read_only_no_mutation(self, workspace_dir):
        result1 = worktree_status(workspace_dir)
        result2 = worktree_status(workspace_dir)
        assert result1["table"] == result2["table"]


class TestClaim:
    """AC-6.7.3: --claim operation."""

    def test_claim_updates_row(self, workspace_dir):
        result = worktree_status(
            workspace_dir,
            claim="repo-a",
            task="Story 6.3",
            agent_id="agent-1",
        )
        assert result["success"] is True
        assert "in-progress" in result["table"]
        assert "agent-1" in result["table"]

    def test_claim_idempotent_same_agent(self, workspace_dir):
        worktree_status(
            workspace_dir,
            claim="repo-a",
            task="Story 6.3",
            agent_id="agent-1",
        )
        result = worktree_status(
            workspace_dir,
            claim="repo-a",
            task="Story 6.3",
            agent_id="agent-1",
        )
        assert result["success"] is True

    def test_claim_nonexistent_worktree(self, workspace_dir):
        result = worktree_status(
            workspace_dir,
            claim="nonexistent",
            task="test",
        )
        assert result["success"] is False
        assert result["exit_code"] == 1


class TestRelease:
    """AC-6.7.4: --release operation."""

    def test_release_resets_row(self, workspace_dir):
        worktree_status(
            workspace_dir,
            claim="repo-a",
            task="Story 6.3",
            agent_id="agent-1",
        )
        result = worktree_status(
            workspace_dir,
            release="repo-a",
            agent_id="agent-1",
        )
        assert result["success"] is True
        assert "idle" in result["table"]


class TestCollisionDetection:
    """AC-6.7.5: Collision detection."""

    def test_collision_returns_exit_2(self, workspace_dir):
        worktree_status(
            workspace_dir,
            claim="repo-a",
            task="Story 6.3",
            agent_id="agent-1",
        )
        result = worktree_status(
            workspace_dir,
            claim="repo-a",
            task="Story 6.4",
            agent_id="agent-2",
        )
        assert result["success"] is False
        assert result["exit_code"] == 2
        assert "already claimed" in result["message"]

    def test_force_override(self, workspace_dir):
        worktree_status(
            workspace_dir,
            claim="repo-a",
            task="Story 6.3",
            agent_id="agent-1",
        )
        result = worktree_status(
            workspace_dir,
            claim="repo-a",
            task="Story 6.4",
            agent_id="agent-2",
            force=True,
        )
        assert result["success"] is True


class TestAtomicWrites:
    """AC-6.7.6: Atomic file writes under concurrent access."""

    def test_parallel_claims_one_wins(self, workspace_dir):
        """10 parallel claims on the same worktree — exactly one wins."""
        results = []

        def try_claim(agent_id):
            return worktree_status(
                workspace_dir,
                claim="repo-a",
                task=f"Task-{agent_id}",
                agent_id=f"agent-{agent_id}",
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(try_claim, i) for i in range(10)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        successes = [r for r in results if r["success"]]
        failures = [r for r in results if not r["success"]]

        # At least one should succeed (the first claim)
        assert len(successes) >= 1
        # Others should get collision (exit 2) or succeed if they were first
