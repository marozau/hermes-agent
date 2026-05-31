"""Tests for Story 6.6 — provision-profiles per-worktree capability check.

ACs:
- AC-6.6.1: Per-worktree inventory
- AC-6.6.2: DAG×worktree×capability cross-check
- AC-6.6.3: Stale-worktree detection
- AC-6.6.4: Single-worktree case unchanged
- AC-6.6.5: Output is deterministic
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plugins.bmad.lib.config import WorkspaceConfig, WorktreeSpec
from plugins.bmad.lib.capability_check import (
    check_dag_worktree_capabilities,
    check_stale_worktrees,
    check_worktree_capabilities,
    generate_capability_report,
    inventory_worktree_capabilities,
)


@pytest.fixture
def ws_config():
    return WorkspaceConfig(
        workspace_mode=True,
        worktrees=[
            WorktreeSpec(
                name="python-repo",
                upstream="/tmp/upstream-py",
                branch="feat/py",
                path="worktree/python-repo",
            ),
            WorktreeSpec(
                name="go-repo",
                upstream="/tmp/upstream-go",
                branch="feat/go",
                path="worktree/go-repo",
            ),
        ],
    )


@pytest.fixture
def workspace_root(tmp_path):
    # Create worktree dirs with marker files
    py_dir = tmp_path / "worktree" / "python-repo"
    py_dir.mkdir(parents=True)
    (py_dir / "requirements.txt").write_text("pytest\n")
    (py_dir / "pyproject.toml").write_text("[project]\nname='test'\n")

    go_dir = tmp_path / "worktree" / "go-repo"
    go_dir.mkdir(parents=True)
    (go_dir / "go.mod").write_text("module test\n")

    return tmp_path


class TestInventoryCapabilities:
    """AC-6.6.1: Per-worktree capability inventory."""

    def test_inventory_returns_list(self, workspace_root):
        caps = inventory_worktree_capabilities(
            workspace_root / "worktree" / "python-repo"
        )
        assert isinstance(caps, list)
        assert "git" in caps  # Always present

    def test_inventory_detects_python_project(self, workspace_root):
        caps = inventory_worktree_capabilities(
            workspace_root / "worktree" / "python-repo"
        )
        assert "python-project" in caps

    def test_inventory_detects_go_project(self, workspace_root):
        caps = inventory_worktree_capabilities(
            workspace_root / "worktree" / "go-repo"
        )
        assert "go-project" in caps


class TestCheckCapabilities:
    """AC-6.6.2: DAG×worktree×capability cross-check."""

    def test_satisfied_capabilities(self, workspace_root):
        missing = check_worktree_capabilities(
            "python-repo",
            workspace_root / "worktree" / "python-repo",
            ["git", "python-project"],
        )
        assert missing == []

    def test_missing_capabilities(self, workspace_root):
        missing = check_worktree_capabilities(
            "python-repo",
            workspace_root / "worktree" / "python-repo",
            ["nonexistent-tool-xyz"],  # Definitely missing
        )
        assert "nonexistent-tool-xyz" in missing

    def test_nonexistent_worktree(self, tmp_path):
        missing = check_worktree_capabilities(
            "missing",
            tmp_path / "nonexistent",
            ["git"],
        )
        assert len(missing) == 1
        assert "does not exist" in missing[0]


class TestDagCapabilityCrossCheck:
    """AC-6.6.2: Full DAG cross-check."""

    def test_catches_mismatch(self, ws_config, workspace_root):
        dag = {
            "nodes": [
                {
                    "id": "node-go",
                    "worktree": "python-repo",
                    "required_capabilities": ["nonexistent-tool-xyz"],
                },
            ]
        }
        mismatches = check_dag_worktree_capabilities(dag, workspace_root, ws_config)
        assert len(mismatches) == 1
        assert mismatches[0]["node_id"] == "node-go"
        assert "nonexistent-tool-xyz" in mismatches[0]["missing"]

    def test_no_mismatch_when_satisfied(self, ws_config, workspace_root):
        dag = {
            "nodes": [
                {
                    "id": "node-py",
                    "worktree": "python-repo",
                    "required_capabilities": ["git", "python-project"],
                },
            ]
        }
        mismatches = check_dag_worktree_capabilities(dag, workspace_root, ws_config)
        assert len(mismatches) == 0


class TestDeterministicOutput:
    """AC-6.6.5: Output is deterministic."""

    def test_report_deterministic(self, ws_config, workspace_root):
        r1 = generate_capability_report(workspace_root, ws_config)
        r2 = generate_capability_report(workspace_root, ws_config)
        assert r1 == r2
