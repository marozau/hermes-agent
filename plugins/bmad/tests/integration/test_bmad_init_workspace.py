"""Tests for Story 6.3 — bmad-init --workspace scaffolder.

ACs:
- AC-6.3.1: Layout creation
- AC-6.3.2: Multi-worktree
- AC-6.3.3: Idempotency
- AC-6.3.4: Git failure handling
- AC-6.3.5: --envrc flag
- AC-6.3.6: Branch already checked out
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from plugins.bmad.scripts.bmad_init import bootstrap_workspace


@pytest.fixture
def upstream_repo(tmp_path):
    """Create a minimal git repo to act as upstream.

    The feature branch exists but is NOT checked out (we switch back to main)
    so that ``git worktree add`` can check it out into a new worktree.
    """
    repo = tmp_path / "upstream"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(repo), capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(repo), capture_output=True,
    )
    (repo / "README.md").write_text("upstream")
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(repo), capture_output=True,
    )
    # Create a feature branch then switch back to main
    subprocess.run(
        ["git", "checkout", "-b", "feat/test"],
        cwd=str(repo), capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "main"],
        cwd=str(repo), capture_output=True,
    )
    return repo


@pytest.fixture
def upstream_repo_b(tmp_path):
    """Create a second upstream repo (branch not checked out)."""
    repo = tmp_path / "upstream-b"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(repo), capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(repo), capture_output=True,
    )
    (repo / "README.md").write_text("upstream-b")
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(repo), capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "-b", "feat/b"],
        cwd=str(repo), capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "main"],
        cwd=str(repo), capture_output=True,
    )
    return repo


class TestLayoutCreation:
    """AC-6.3.1: Layout creation."""

    def test_creates_workspace_structure(self, tmp_path, upstream_repo):
        workspace = tmp_path / "my-workspace"
        workspace.mkdir()

        config = bootstrap_workspace(
            workspace,
            project_name="test-project",
            worktrees=[{
                "name": "repo-a",
                "upstream": str(upstream_repo),
                "branch": "feat/test",
            }],
        )

        assert config["workspace_mode"] is True
        assert (workspace / "bmad" / "config.yaml").exists()
        assert (workspace / "planning-artifacts").is_dir()
        assert (workspace / "worktree" / "repo-a").is_dir()
        assert (workspace / "AGENTS.md").exists()
        assert (workspace / "CLAUDE.md").exists()
        assert (workspace / "WORKTREES.md").exists()

    def test_symlink_on_unix(self, tmp_path, upstream_repo):
        workspace = tmp_path / "ws"
        workspace.mkdir()

        bootstrap_workspace(
            workspace,
            project_name="test",
            worktrees=[{
                "name": "a",
                "upstream": str(upstream_repo),
                "branch": "feat/test",
            }],
        )

        claude = workspace / "CLAUDE.md"
        if claude.is_symlink():
            assert claude.resolve() == (workspace / "AGENTS.md").resolve()


class TestMultiWorktree:
    """AC-6.3.2: Multi-worktree."""

    def test_two_worktrees(self, tmp_path, upstream_repo, upstream_repo_b):
        workspace = tmp_path / "ws"
        workspace.mkdir()

        config = bootstrap_workspace(
            workspace,
            project_name="test",
            worktrees=[
                {"name": "a", "upstream": str(upstream_repo), "branch": "feat/test"},
                {"name": "b", "upstream": str(upstream_repo_b), "branch": "feat/b"},
            ],
        )

        assert len(config["worktrees"]) == 2
        assert (workspace / "worktree" / "a").is_dir()
        assert (workspace / "worktree" / "b").is_dir()


class TestIdempotency:
    """AC-6.3.3: Idempotency."""

    def test_refuses_double_init(self, tmp_path, upstream_repo):
        workspace = tmp_path / "ws"
        workspace.mkdir()

        bootstrap_workspace(
            workspace,
            project_name="test",
            worktrees=[{
                "name": "a",
                "upstream": str(upstream_repo),
                "branch": "feat/test",
            }],
        )

        with pytest.raises(RuntimeError, match="already initialized"):
            bootstrap_workspace(
                workspace,
                project_name="test",
                worktrees=[{
                    "name": "a",
                    "upstream": str(upstream_repo),
                    "branch": "feat/test",
                }],
            )


class TestGitFailureHandling:
    """AC-6.3.4: Git failure handling."""

    def test_invalid_upstream_raises(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()

        with pytest.raises(ValueError, match="does not exist"):
            bootstrap_workspace(
                workspace,
                project_name="test",
                worktrees=[{
                    "name": "a",
                    "upstream": "/nonexistent/path",
                    "branch": "feat/test",
                }],
            )


class TestEnvrcFlag:
    """AC-6.3.5: --envrc flag."""

    def test_envrc_written(self, tmp_path, upstream_repo):
        workspace = tmp_path / "ws"
        workspace.mkdir()

        bootstrap_workspace(
            workspace,
            project_name="test",
            worktrees=[{
                "name": "a",
                "upstream": str(upstream_repo),
                "branch": "feat/test",
            }],
            envrc=True,
        )

        envrc = workspace / ".envrc"
        assert envrc.exists()
        assert "BMAD_WORKSPACE_ROOT" in envrc.read_text()
