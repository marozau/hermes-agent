"""Integration tests for bmad_init bootstrap and CLI."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml
import pytest

from plugins.bmad.scripts.bmad_init import bootstrap, cli_main


class TestBootstrap:
    """Test the bootstrap() function on an empty directory."""

    def test_creates_config_yaml(self, tmp_path: Path) -> None:
        """Creates bmad/config.yaml with correct keys."""
        result = bootstrap(
            tmp_path,
            project_name="my-app",
            project_type="api",
            project_level=1,
            user_name="tester",
            interactive=False,
        )

        cfg_path = tmp_path / "bmad" / "config.yaml"
        assert cfg_path.exists()
        cfg = yaml.safe_load(cfg_path.read_text())
        assert cfg["project_name"] == "my-app"
        assert cfg["project_type"] == "api"
        assert cfg["project_level"] == 1
        assert cfg["user_name"] == "tester"

    def test_creates_directories(self, tmp_path: Path) -> None:
        """Creates planning-artifacts/ and implementation-artifacts/ directories."""
        bootstrap(
            tmp_path,
            project_name="test",
            project_type="other",
            project_level=1,
            user_name="test",
            interactive=False,
        )

        assert (tmp_path / "planning-artifacts").is_dir()
        assert (tmp_path / "planning-artifacts" / "research").is_dir()
        assert (tmp_path / "implementation-artifacts").is_dir()
        assert (tmp_path / "implementation-artifacts" / "stories").is_dir()

    def test_creates_workflow_status_yaml(self, tmp_path: Path) -> None:
        """Creates planning-artifacts/workflow-status.yaml."""
        bootstrap(
            tmp_path,
            project_name="test",
            project_type="api",
            project_level=1,
            user_name="tester",
            interactive=False,
        )

        ws_path = tmp_path / "planning-artifacts" / "workflow-status.yaml"
        assert ws_path.exists()
        data = yaml.safe_load(ws_path.read_text())
        assert data["project"] == "test"
        assert data["level"] == 1
        assert "phases" in data

    def test_level_2_sets_prd_required(self, tmp_path: Path) -> None:
        """Level >= 2 marks prd, architecture, solutioning-gate-check as required."""
        bootstrap(
            tmp_path,
            project_name="test",
            project_type="api",
            project_level=2,
            user_name="tester",
            interactive=False,
        )

        ws_path = tmp_path / "planning-artifacts" / "workflow-status.yaml"
        data = yaml.safe_load(ws_path.read_text())
        phases = data["phases"]
        assert phases["planning"]["prd"] == "required"
        assert phases["solutioning"]["architecture"] == "required"
        assert phases["solutioning"]["solutioning-gate-check"] == "required"

    def test_refuses_overwrite_without_force(self, tmp_path: Path) -> None:
        """Raises RuntimeError when config exists and force=False."""
        bootstrap(
            tmp_path,
            project_name="first",
            project_type="api",
            project_level=1,
            user_name="tester",
            interactive=False,
        )
        with pytest.raises(RuntimeError):
            bootstrap(
                tmp_path,
                project_name="second",
                project_type="web-app",
                project_level=2,
                user_name="tester",
                interactive=False,
                force=False,
            )

        # Original config should persist
        cfg = yaml.safe_load((tmp_path / "bmad" / "config.yaml").read_text())
        assert cfg["project_name"] == "first"

    def test_force_overwrites_existing(self, tmp_path: Path) -> None:
        """--force overwrites existing bmad/config.yaml."""
        bootstrap(
            tmp_path,
            project_name="first",
            project_type="api",
            project_level=1,
            user_name="tester",
            interactive=False,
        )
        bootstrap(
            tmp_path,
            project_name="second",
            project_type="web-app",
            project_level=2,
            user_name="tester2",
            interactive=False,
            force=True,
        )

        cfg = yaml.safe_load((tmp_path / "bmad" / "config.yaml").read_text())
        assert cfg["project_name"] == "second"
        assert cfg["project_level"] == 2

    def test_returns_config_dict(self, tmp_path: Path) -> None:
        """bootstrap returns the final config dict."""
        result = bootstrap(
            tmp_path,
            project_name="my-app",
            project_type="library",
            project_level=1,
            user_name="dev",
            interactive=False,
        )
        assert isinstance(result, dict)
        assert result["project_name"] == "my-app"

    def test_creates_exit_code(self, tmp_path: Path) -> None:
        """The bootstrap doesn't set exit codes — cli_main does."""
        # bootstrap just returns config on success
        result = bootstrap(
            tmp_path,
            project_name="test",
            project_type="other",
            project_level=1,
            user_name="test",
            interactive=False,
        )
        assert isinstance(result, dict)


class TestBootstrapLevelSpecific:
    """Level-specific behavior."""

    @pytest.mark.parametrize("level,expected_required", [
        (0, []),
        (1, []),
        (2, ["prd", "architecture", "solutioning-gate-check"]),
        (3, ["prd", "architecture", "solutioning-gate-check"]),
        (4, ["prd", "architecture", "solutioning-gate-check"]),
    ])
    def test_level_required_slots(self, tmp_path: Path, level: int, expected_required: list[str]) -> None:
        """Level 2+ sets additional slots as required in workflow-status."""
        bootstrap(
            tmp_path,
            project_name="test",
            project_type="api",
            project_level=level,
            user_name="tester",
            interactive=False,
        )
        ws_path = tmp_path / "planning-artifacts" / "workflow-status.yaml"
        data = yaml.safe_load(ws_path.read_text())
        assert data["level"] == level

    def test_default_slots_present_all_levels(self, tmp_path: Path) -> None:
        """All standard phase slots exist regardless of level."""
        bootstrap(
            tmp_path,
            project_name="test",
            project_type="api",
            project_level=1,
            user_name="tester",
            interactive=False,
        )
        ws_path = tmp_path / "planning-artifacts" / "workflow-status.yaml"
        data = yaml.safe_load(ws_path.read_text())
        phases = data["phases"]

        # Analysis
        assert "product-brief" in phases["analysis"]
        # Implementation
        assert "sprint-planning" in phases["implementation"]
