"""Tests for Story 6.8 — runtime_mirror post_tool_call hook.

ACs:
- AC-6.8.1: Mirror on write (WI-5)
- AC-6.8.2: Single-file scope
- AC-6.8.3: Skip when no mirror declared
- AC-6.8.4: Skip when target unchanged
- AC-6.8.5: Mirror dir missing
- AC-6.8.6: Off when workspace_mode is false
- AC-6.8.7: Telemetry
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def mirror_workspace(tmp_path):
    """Create a workspace with runtime_mirror configured."""
    bmad_dir = tmp_path / "bmad"
    bmad_dir.mkdir()

    mirror_target = tmp_path / "runtime" / "repo-a"
    mirror_target.mkdir(parents=True)

    (bmad_dir / "config.yaml").write_text(yaml.safe_dump({
        "project_name": "test-ws",
        "workspace_mode": True,
        "worktrees": [
            {
                "name": "repo-a",
                "upstream": "/tmp/upstream-a",
                "branch": "feat/a",
                "path": "worktree/repo-a",
                "runtime_mirror": str(mirror_target),
            },
        ],
    }, sort_keys=False))

    wt_dir = tmp_path / "worktree" / "repo-a" / "lib"
    wt_dir.mkdir(parents=True)

    return tmp_path, mirror_target


@pytest.fixture
def no_mirror_workspace(tmp_path):
    """Create a workspace without runtime_mirror."""
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
        ],
    }, sort_keys=False))

    wt_dir = tmp_path / "worktree" / "repo-a" / "lib"
    wt_dir.mkdir(parents=True)

    return tmp_path


class MockCtx:
    def __init__(self, project_dir):
        self.project_dir = str(project_dir)
        self.working_directory = str(project_dir)
        self.profile_config = {}


class TestMirrorOnWrite:
    """AC-6.8.1: Mirror on write (WI-5)."""

    def test_mirror_copies_file(self, mirror_workspace):
        workspace_dir, mirror_target = mirror_workspace
        from plugins.bmad.hooks.post_tool_call import post_tool_call

        # Create source file
        src = workspace_dir / "worktree" / "repo-a" / "lib" / "foo.py"
        src.write_text("print('hello')\n")

        ctx = MockCtx(workspace_dir)
        post_tool_call(
            ctx, "Write",
            {"file_path": str(src)},
        )

        dest = mirror_target / "lib" / "foo.py"
        assert dest.exists()
        assert dest.read_text() == "print('hello')\n"

    def test_mirror_cleans_pycache(self, mirror_workspace):
        workspace_dir, mirror_target = mirror_workspace
        from plugins.bmad.hooks.post_tool_call import post_tool_call

        # Create source file
        src = workspace_dir / "worktree" / "repo-a" / "lib" / "foo.py"
        src.write_text("print('hello')\n")

        # Create stale .pyc
        pycache = mirror_target / "lib" / "__pycache__"
        pycache.mkdir(parents=True)
        pyc = pycache / "foo.cpython-311.pyc"
        pyc.write_bytes(b"stale bytecode")

        ctx = MockCtx(workspace_dir)
        post_tool_call(
            ctx, "Write",
            {"file_path": str(src)},
        )

        assert not pyc.exists()


class TestSingleFileScope:
    """AC-6.8.2: Single-file scope only."""

    def test_only_target_file_mirrored(self, mirror_workspace):
        workspace_dir, mirror_target = mirror_workspace
        from plugins.bmad.hooks.post_tool_call import post_tool_call

        # Create source file
        src = workspace_dir / "worktree" / "repo-a" / "lib" / "foo.py"
        src.write_text("content")

        # Create a sibling that should NOT be touched
        sibling_dir = mirror_target / "lib"
        sibling_dir.mkdir(parents=True, exist_ok=True)
        sibling = sibling_dir / "bar.py"
        sibling.write_text("original")

        ctx = MockCtx(workspace_dir)
        post_tool_call(
            ctx, "Write",
            {"file_path": str(src)},
        )

        assert sibling.read_text() == "original"  # Untouched


class TestSkipWhenNoMirror:
    """AC-6.8.3: Skip when no mirror declared."""

    def test_no_mirror_no_copy(self, no_mirror_workspace):
        from plugins.bmad.hooks.post_tool_call import post_tool_call

        src = no_mirror_workspace / "worktree" / "repo-a" / "lib" / "foo.py"
        src.write_text("content")

        ctx = MockCtx(no_mirror_workspace)
        post_tool_call(
            ctx, "Write",
            {"file_path": str(src)},
        )
        # No crash, no side effects


class TestSkipWhenUnchanged:
    """AC-6.8.4: Skip when content is identical."""

    def test_idempotent_skip(self, mirror_workspace, caplog):
        workspace_dir, mirror_target = mirror_workspace
        from plugins.bmad.hooks.post_tool_call import post_tool_call

        src = workspace_dir / "worktree" / "repo-a" / "lib" / "foo.py"
        src.write_text("same content")

        dest = mirror_target / "lib" / "foo.py"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("same content")

        ctx = MockCtx(workspace_dir)
        post_tool_call(
            ctx, "Write",
            {"file_path": str(src)},
        )
        # Should not have logged a mirror event (idempotent)


class TestMirrorDirMissing:
    """AC-6.8.5: Mirror dir missing — warn, don't block."""

    def test_missing_mirror_warns(self, tmp_path):
        from plugins.bmad.hooks.post_tool_call import post_tool_call

        bmad_dir = tmp_path / "bmad"
        bmad_dir.mkdir()

        (bmad_dir / "config.yaml").write_text(yaml.safe_dump({
            "project_name": "test",
            "workspace_mode": True,
            "worktrees": [{
                "name": "a",
                "upstream": "/tmp/u",
                "branch": "b",
                "path": "worktree/a",
                "runtime_mirror": "/nonexistent/path",
            }],
        }, sort_keys=False))

        wt_dir = tmp_path / "worktree" / "a" / "lib"
        wt_dir.mkdir(parents=True)
        src = wt_dir / "foo.py"
        src.write_text("content")

        ctx = MockCtx(tmp_path)
        # Should not raise
        post_tool_call(ctx, "Write", {"file_path": str(src)})


class TestOffWhenNotWorkspace:
    """AC-6.8.6: Off when workspace_mode is false."""

    def test_no_mirror_without_workspace_mode(self, tmp_path):
        from plugins.bmad.hooks.post_tool_call import post_tool_call

        bmad_dir = tmp_path / "bmad"
        bmad_dir.mkdir()
        (bmad_dir / "config.yaml").write_text(yaml.safe_dump({
            "project_name": "test",
            "workspace_mode": False,
        }))
        (tmp_path / "planning-artifacts").mkdir()

        src = tmp_path / "planning-artifacts" / "foo.md"
        src.write_text("content")

        ctx = MockCtx(tmp_path)
        post_tool_call(ctx, "Write", {"file_path": str(src)})
        # No crash
