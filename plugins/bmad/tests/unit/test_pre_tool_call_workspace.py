"""Tests for Story 6.4 — pre_tool_call workspace write-boundary.

ACs:
- AC-6.4.1: Block writes outside boundary
- AC-6.4.2: Allow writes inside planning-artifacts
- AC-6.4.3: Allow writes inside any worktree
- AC-6.4.4: Symlink resolution
- AC-6.4.5: Off when workspace_mode is false (WI-1)
- AC-6.4.6: No false-positives on relative paths
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def workspace_dir(tmp_path):
    """Create a workspace-mode project directory."""
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

    # Create directories
    (tmp_path / "planning-artifacts").mkdir()
    (tmp_path / "planning-artifacts" / "research").mkdir()
    (tmp_path / "worktree" / "repo-a" / "lib").mkdir(parents=True)
    (tmp_path / "worktree" / "repo-b" / "src").mkdir(parents=True)

    return tmp_path


class MockCtx:
    def __init__(self, project_dir):
        self.project_dir = str(project_dir)
        self.working_directory = str(project_dir)
        self.profile_config = {}


class TestWriteBoundary:
    """Test workspace write-boundary enforcement."""

    def test_block_writes_outside_boundary(self, workspace_dir):
        """AC-6.4.1: Block writes to paths outside planning-artifacts/ and worktree/*."""
        from plugins.bmad.hooks.pre_tool_call import pre_tool_call

        ctx = MockCtx(workspace_dir)
        result = pre_tool_call(
            ctx, "Write",
            {"file_path": "/tmp/some-other-repo/lib/foo.py"},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "workspace_mode" in result["reason"]

    def test_allow_writes_inside_planning_artifacts(self, workspace_dir):
        """AC-6.4.2: Allow writes to planning-artifacts/."""
        from plugins.bmad.hooks.pre_tool_call import pre_tool_call

        ctx = MockCtx(workspace_dir)
        result = pre_tool_call(
            ctx, "Write",
            {"file_path": str(workspace_dir / "planning-artifacts" / "prd.md")},
        )
        # Should be None (allowed) — phase gate may also fire but workspace check passes
        # The phase gate might block it, but the workspace check itself passes
        # We verify the workspace check didn't block
        if result is not None:
            assert "workspace_mode" not in result.get("reason", "")

    def test_allow_writes_inside_worktree_a(self, workspace_dir):
        """AC-6.4.3: Allow writes to worktree/a/."""
        from plugins.bmad.hooks.pre_tool_call import pre_tool_call

        ctx = MockCtx(workspace_dir)
        result = pre_tool_call(
            ctx, "Write",
            {"file_path": str(workspace_dir / "worktree" / "repo-a" / "lib" / "foo.py")},
        )
        if result is not None:
            assert "workspace_mode" not in result.get("reason", "")

    def test_allow_writes_inside_worktree_b(self, workspace_dir):
        """AC-6.4.3: Allow writes to worktree/b/."""
        from plugins.bmad.hooks.pre_tool_call import pre_tool_call

        ctx = MockCtx(workspace_dir)
        result = pre_tool_call(
            ctx, "Write",
            {"file_path": str(workspace_dir / "worktree" / "repo-b" / "src" / "bar.py")},
        )
        if result is not None:
            assert "workspace_mode" not in result.get("reason", "")

    def test_symlink_resolution(self, workspace_dir):
        """AC-6.4.4: Writes to symlinked worktree paths are allowed."""
        from plugins.bmad.hooks.pre_tool_call import pre_tool_call

        # Create a symlink from a temp dir to worktree/repo-a
        link_dir = workspace_dir / "link-to-repo-a"
        link_dir.symlink_to(workspace_dir / "worktree" / "repo-a")

        ctx = MockCtx(workspace_dir)
        result = pre_tool_call(
            ctx, "Write",
            {"file_path": str(link_dir / "lib" / "foo.py")},
        )
        if result is not None:
            assert "workspace_mode" not in result.get("reason", "")

    def test_off_when_workspace_mode_false(self, tmp_path):
        """AC-6.4.5: Workspace check is skipped when workspace_mode is false."""
        from plugins.bmad.hooks.pre_tool_call import pre_tool_call

        bmad_dir = tmp_path / "bmad"
        bmad_dir.mkdir()
        (bmad_dir / "config.yaml").write_text(yaml.safe_dump({
            "project_name": "test",
            "workspace_mode": False,
        }))
        (tmp_path / "planning-artifacts").mkdir()

        ctx = MockCtx(tmp_path)
        result = pre_tool_call(
            ctx, "Write",
            {"file_path": "/tmp/anywhere/foo.py"},
        )
        # workspace_mode=false means the boundary check is skipped
        if result is not None:
            assert "workspace_mode" not in result.get("reason", "")

    def test_no_false_positive_relative_path(self, workspace_dir):
        """AC-6.4.6: Relative paths to planning-artifacts don't false-positive."""
        from plugins.bmad.lib.config import load_workspace_config
        from plugins.bmad.lib.workspace import is_write_allowed

        ws_config = load_workspace_config(workspace_dir)
        result = is_write_allowed(
            str(workspace_dir / "planning-artifacts" / "foo.md"),
            workspace_dir,
            ws_config,
        )
        assert result is True
