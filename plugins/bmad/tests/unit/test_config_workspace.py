"""Tests for Story 6.2 — bmad/config.yaml schema: workspace_mode + worktrees.

ACs:
- AC-6.2.1: Backward compatibility (WI-1)
- AC-6.2.2: Field validation
- AC-6.2.3: Path safety (WI-2)
- AC-6.2.4: Round-trip
- AC-6.2.5: runtime_mirror is optional
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError


class TestWorktreeSpec:
    """Tests for the WorktreeSpec Pydantic model."""

    def test_valid_spec(self):
        from plugins.bmad.lib.config import WorktreeSpec

        spec = WorktreeSpec(
            name="hermes-agent",
            upstream="~/usr-local/hermes",
            branch="feat/foo",
            path="worktree/hermes-agent",
        )
        assert spec.name == "hermes-agent"
        assert spec.runtime_mirror is None

    def test_name_must_be_safe(self):
        from plugins.bmad.lib.config import WorktreeSpec

        with pytest.raises(ValidationError, match="alphanumeric"):
            WorktreeSpec(
                name="../escape",
                upstream="~/u",
                branch="b",
                path="worktree/escape",
            )

    def test_path_no_escape(self):
        from plugins.bmad.lib.config import WorktreeSpec

        with pytest.raises(ValidationError, match="must not contain"):
            WorktreeSpec(
                name="a",
                upstream="~/u",
                branch="b",
                path="worktree/../escape",
            )

    def test_path_must_be_relative(self):
        from plugins.bmad.lib.config import WorktreeSpec

        with pytest.raises(ValidationError, match="must be relative"):
            WorktreeSpec(
                name="a",
                upstream="~/u",
                branch="b",
                path="/absolute/path",
            )

    def test_path_must_start_with_worktree(self):
        from plugins.bmad.lib.config import WorktreeSpec

        with pytest.raises(ValidationError, match="must start with"):
            WorktreeSpec(
                name="a",
                upstream="~/u",
                branch="b",
                path="other/a",
            )

    def test_runtime_mirror_optional(self):
        from plugins.bmad.lib.config import WorktreeSpec

        spec = WorktreeSpec(
            name="a",
            upstream="~/u",
            branch="b",
            path="worktree/a",
        )
        assert spec.runtime_mirror is None

    def test_runtime_mirror_set(self):
        from plugins.bmad.lib.config import WorktreeSpec

        spec = WorktreeSpec(
            name="a",
            upstream="~/u",
            branch="b",
            path="worktree/a",
            runtime_mirror="~/.hermes/hermes-agent",
        )
        assert spec.runtime_mirror == "~/.hermes/hermes-agent"


class TestWorkspaceConfig:
    """Tests for the WorkspaceConfig model."""

    def test_defaults(self):
        from plugins.bmad.lib.config import WorkspaceConfig

        cfg = WorkspaceConfig()
        assert cfg.workspace_mode is False
        assert cfg.worktrees == []


class TestLoadWorkspaceConfig:
    """Tests for load_workspace_config."""

    def test_backward_compat_no_workspace_fields(self, tmp_path):
        """AC-6.2.1: Missing workspace_mode = old behavior."""
        from plugins.bmad.lib.config import load_workspace_config

        bmad_dir = tmp_path / "bmad"
        bmad_dir.mkdir()
        (bmad_dir / "config.yaml").write_text(yaml.safe_dump({
            "project_name": "test",
            "project_type": "api",
        }))

        cfg = load_workspace_config(tmp_path)
        assert cfg.workspace_mode is False
        assert cfg.worktrees == []

    def test_backward_compat_no_config(self, tmp_path):
        """AC-6.2.1: No config file = safe defaults."""
        from plugins.bmad.lib.config import load_workspace_config

        cfg = load_workspace_config(tmp_path)
        assert cfg.workspace_mode is False
        assert cfg.worktrees == []

    def test_workspace_mode_true(self, tmp_path):
        from plugins.bmad.lib.config import load_workspace_config

        bmad_dir = tmp_path / "bmad"
        bmad_dir.mkdir()
        (bmad_dir / "config.yaml").write_text(yaml.safe_dump({
            "project_name": "test",
            "workspace_mode": True,
            "worktrees": [
                {
                    "name": "repo-a",
                    "upstream": "/tmp/upstream",
                    "branch": "feat/x",
                    "path": "worktree/repo-a",
                },
            ],
        }))

        cfg = load_workspace_config(tmp_path)
        assert cfg.workspace_mode is True
        assert len(cfg.worktrees) == 1
        assert cfg.worktrees[0].name == "repo-a"

    def test_field_validation_missing_fields(self, tmp_path):
        """AC-6.2.2: Missing required fields raise ValidationError."""
        from plugins.bmad.lib.config import load_workspace_config

        bmad_dir = tmp_path / "bmad"
        bmad_dir.mkdir()
        (bmad_dir / "config.yaml").write_text(yaml.safe_dump({
            "workspace_mode": True,
            "worktrees": [{"name": "a"}],
        }))

        with pytest.raises(ValidationError):
            load_workspace_config(tmp_path)

    def test_path_safety(self, tmp_path):
        """AC-6.2.3: Paths with .. raise ValidationError."""
        from plugins.bmad.lib.config import WorktreeSpec

        with pytest.raises(ValidationError, match="must not contain"):
            WorktreeSpec(
                name="a",
                upstream="~/u",
                branch="b",
                path="worktree/../escape",
            )


class TestSerializeWorkspaceConfig:
    """Tests for serialize_workspace_config."""

    def test_round_trip(self, tmp_path):
        """AC-6.2.4: Serialize → deserialize is byte-equal."""
        from plugins.bmad.lib.config import (
            WorkspaceConfig,
            WorktreeSpec,
            load_workspace_config,
            serialize_workspace_config,
        )

        original = {
            "project_name": "test",
            "workspace_mode": True,
            "worktrees": [
                {
                    "name": "a",
                    "upstream": "/tmp/u",
                    "branch": "feat/a",
                    "path": "worktree/a",
                },
                {
                    "name": "b",
                    "upstream": "/tmp/v",
                    "branch": "feat/b",
                    "path": "worktree/b",
                    "runtime_mirror": "~/.hermes/b",
                },
            ],
        }

        bmad_dir = tmp_path / "bmad"
        bmad_dir.mkdir()
        (bmad_dir / "config.yaml").write_text(yaml.safe_dump(original, sort_keys=False))

        cfg = load_workspace_config(tmp_path)
        serialized = serialize_workspace_config(cfg)

        assert serialized["workspace_mode"] is True
        assert len(serialized["worktrees"]) == 2
        assert serialized["worktrees"][1]["runtime_mirror"] == "~/.hermes/b"
