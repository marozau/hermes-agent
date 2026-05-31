"""End-to-end integration test for Epic 6 — full workspace lifecycle.

Covers the E2E scenario: create workspace → define DAG → run nodes →
verify write boundary blocks external writes → verify gate semantics.

This is the cross-cutting DoD test for Epic 6.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from plugins.bmad.scripts.bmad_init import bootstrap_workspace
from plugins.bmad.lib.config import load_workspace_config, WorkspaceConfig
from plugins.bmad.lib.workspace import is_write_allowed
from plugins.bmad.lib.dag_runner import validate_dag, resolve_node_cwd, run_node
from plugins.bmad.lib.capability_check import (
    check_dag_worktree_capabilities,
    generate_capability_report,
)
from plugins.bmad.commands.worktree_status import worktree_status


@pytest.fixture
def full_workspace(tmp_path):
    """Create a complete workspace with two upstream repos and full config."""
    # Create two upstream repos
    repos = {}
    for name, branch in [("repo-a", "feat/a"), ("repo-b", "feat/b")]:
        repo = tmp_path / f"upstream-{name}"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), capture_output=True)
        (repo / "README.md").write_text(f"upstream {name}")
        subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True)
        subprocess.run(["git", "checkout", "-b", branch], cwd=str(repo), capture_output=True)
        subprocess.run(["git", "checkout", "main"], cwd=str(repo), capture_output=True)
        repos[name] = repo

    # Bootstrap workspace
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    config = bootstrap_workspace(
        workspace,
        project_name="e2e-test",
        worktrees=[
            {"name": "repo-a", "upstream": str(repos["repo-a"]), "branch": "feat/a"},
            {"name": "repo-b", "upstream": str(repos["repo-b"]), "branch": "feat/b"},
        ],
    )

    return workspace, repos


class TestE2EWorkspaceLifecycle:
    """Full workspace lifecycle: create → config → DAG → boundary → worktree-status."""

    def test_full_lifecycle(self, full_workspace):
        workspace, repos = full_workspace

        # 1. Verify workspace was created with correct config
        ws_config = load_workspace_config(workspace)
        assert ws_config.workspace_mode is True
        assert len(ws_config.worktrees) == 2
        assert ws_config.worktrees[0].name == "repo-a"
        assert ws_config.worktrees[1].name == "repo-b"

        # 2. Verify all layout files exist
        assert (workspace / "AGENTS.md").exists()
        assert (workspace / "CLAUDE.md").exists()
        assert (workspace / "WORKTREES.md").exists()
        assert (workspace / "bmad" / "config.yaml").exists()
        assert (workspace / "planning-artifacts").is_dir()
        assert (workspace / "worktree" / "repo-a").is_dir()
        assert (workspace / "worktree" / "repo-b").is_dir()

        # 3. Verify CLAUDE.md is a symlink to AGENTS.md
        claude = workspace / "CLAUDE.md"
        if claude.is_symlink():
            assert claude.resolve() == (workspace / "AGENTS.md").resolve()

        # 4. Verify AGENTS.md contains both worktrees
        agents_content = (workspace / "AGENTS.md").read_text()
        assert "repo-a" in agents_content
        assert "repo-b" in agents_content
        assert "feat/a" in agents_content
        assert "feat/b" in agents_content

        # 5. Verify WORKTREES.md has correct initial state
        worktrees_content = (workspace / "WORKTREES.md").read_text()
        assert "repo-a" in worktrees_content
        assert "repo-b" in worktrees_content
        assert "idle" in worktrees_content

        # 6. Verify write boundary blocks external writes
        assert is_write_allowed(
            str(workspace / "planning-artifacts" / "prd.md"),
            workspace, ws_config,
        ) is True
        assert is_write_allowed(
            str(workspace / "worktree" / "repo-a" / "lib" / "foo.py"),
            workspace, ws_config,
        ) is True
        assert is_write_allowed(
            "/tmp/some-external-repo/lib/foo.py",
            workspace, ws_config,
        ) is False

        # 7. Verify DAG validation
        dag = {
            "nodes": [
                {"id": "task-1", "worktree": "repo-a", "required_capabilities": ["git"]},
                {"id": "task-2", "worktree": "repo-b", "required_capabilities": ["git"]},
                {"id": "task-3"},  # workspace root
            ]
        }
        errors = validate_dag(dag, ws_config)
        assert errors == []

        # 8. Verify DAG execution dispatches to correct cwd
        result = run_node(
            dag["nodes"][0], workspace, ws_config,
            ["pwd"],
        )
        assert result.returncode == 0
        assert str(workspace / "worktree" / "repo-a") in result.stdout.strip()

        result = run_node(
            dag["nodes"][2], workspace, ws_config,
            ["pwd"],
        )
        assert result.returncode == 0
        assert str(workspace) in result.stdout.strip()

        # 9. Verify capability check
        cap_report = generate_capability_report(workspace, ws_config)
        assert "repo-a" in cap_report["worktrees"]
        assert "repo-b" in cap_report["worktrees"]
        assert "git" in cap_report["worktrees"]["repo-a"]["capabilities"]

        # 10. Verify worktree-status claim/release cycle
        result = worktree_status(workspace, claim="repo-a", task="E2E test", agent_id="test-agent")
        assert result["success"] is True
        assert "in-progress" in result["table"]

        # Verify collision detection
        result = worktree_status(workspace, claim="repo-a", task="Other", agent_id="other-agent")
        assert result["success"] is False
        assert result["exit_code"] == 2
        assert "already claimed" in result["message"]

        # Verify release
        result = worktree_status(workspace, release="repo-a", agent_id="test-agent")
        assert result["success"] is True
        assert "idle" in result["table"]

        # 11. Verify worktree has actual git repo
        wt_a = workspace / "worktree" / "repo-a"
        git_result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(wt_a), capture_output=True, text=True,
        )
        assert git_result.returncode == 0
        assert "feat/a" in git_result.stdout.strip()

        # 12. Verify config round-trip
        from plugins.bmad.lib.config import serialize_workspace_config
        serialized = serialize_workspace_config(ws_config)
        assert serialized["workspace_mode"] is True
        assert len(serialized["worktrees"]) == 2


class TestE2EGateScenarios:
    """Gate-related scenarios: write boundary blocks, DAG validation rejects."""

    def test_write_boundary_blocks_upstream_edit(self, full_workspace):
        """The #1 failure mode: agent writes to upstream repo instead of worktree."""
        workspace, _ = full_workspace
        ws_config = load_workspace_config(workspace)

        # This is the exact failure pattern from research §7 row 2
        upstream_path = str(workspace / ".." / "upstream-repo-a" / "lib" / "foo.py")
        assert is_write_allowed(upstream_path, workspace, ws_config) is False

    def test_dag_rejects_unknown_worktree(self, full_workspace):
        """DAG node referencing nonexistent worktree is rejected."""
        workspace, _ = full_workspace
        ws_config = load_workspace_config(workspace)

        dag = {"nodes": [{"id": "bad", "worktree": "nonexistent"}]}
        errors = validate_dag(dag, ws_config)
        assert len(errors) == 1
        assert "unknown worktree" in errors[0]

    def test_capability_mismatch_detected(self, full_workspace):
        """DAG node requiring unavailable tool is flagged."""
        workspace, _ = full_workspace
        ws_config = load_workspace_config(workspace)

        dag = {
            "nodes": [{
                "id": "needs-fortran",
                "worktree": "repo-a",
                "required_capabilities": ["nonexistent-fortran-compiler"],
            }]
        }
        mismatches = check_dag_worktree_capabilities(dag, workspace, ws_config)
        assert len(mismatches) == 1
        assert mismatches[0]["node_id"] == "needs-fortran"
        assert "nonexistent-fortran-compiler" in mismatches[0]["missing"]

    def test_backward_compat_single_repo_unchanged(self, tmp_path):
        """WI-1: Existing single-repo BMAD project is byte-for-byte unaffected."""
        from plugins.bmad.scripts.bmad_init import bootstrap

        project = tmp_path / "single-repo"
        project.mkdir()

        config = bootstrap(
            project,
            project_name="old-style",
            project_type="api",
            project_level=1,
            user_name="tester",
            force=True,
            interactive=False,
        )

        assert "workspace_mode" not in config
        assert "worktrees" not in config
        assert (project / "bmad" / "config.yaml").exists()
        assert (project / "planning-artifacts").is_dir()
        assert not (project / "worktree").exists()
        assert not (project / "AGENTS.md").exists()
