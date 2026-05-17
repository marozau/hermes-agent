"""Integration tests for transform_terminal_output hook — status header."""

from __future__ import annotations

from pathlib import Path

import yaml
import pytest

from plugins.bmad.hooks.transform_terminal_output import (
    transform_terminal_output,
)


def _mock_ctx(project_dir: str, profile_config: dict | None = None):
    class MockCtx:
        pass
    ctx = MockCtx()
    ctx.project_dir = project_dir
    ctx.working_directory = project_dir
    ctx.profile_config = profile_config or {}
    return ctx


class TestTransformOutput:
    """Status header rendering."""

    def _setup_project(self, tmp_path: Path, level: int = 2) -> Path:
        """Scaffold a BMAD project with partial completion."""
        (tmp_path / "bmad").mkdir()
        yaml.safe_dump({
            "project_name": "test-proj",
            "project_type": "api",
            "project_level": level,
            "user_name": "tester",
        }, open(tmp_path / "bmad" / "config.yaml", "w"), sort_keys=False)
        (tmp_path / "planning-artifacts").mkdir()
        yaml.safe_dump({
            "project": "test-proj",
            "level": level,
            "created": "2026-05-17",
            "last_updated": "2026-05-17",
            "phases": {
                "analysis": {"product-brief": "complete"},
                "planning": {"prd": "not-started"},
                "solutioning": {},
                "implementation": {"sprint-planning": "not-started"},
            },
        }, open(tmp_path / "planning-artifacts" / "workflow-status.yaml", "w"), sort_keys=False)
        return tmp_path

    def test_prepends_header(self, tmp_path: Path) -> None:
        """Header is prepended to output text."""
        project = self._setup_project(tmp_path)
        ctx = _mock_ctx(str(project))

        result = transform_terminal_output(ctx, "Hello from user")
        assert result is not None
        assert result.startswith("BMAD:")
        assert "Hello from user" in result

    def test_contains_project_name(self, tmp_path: Path) -> None:
        """Header includes project name."""
        project = self._setup_project(tmp_path)
        ctx = _mock_ctx(str(project))

        result = transform_terminal_output(ctx, "Hello")
        assert "test-proj" in result

    def test_contains_next_command(self, tmp_path: Path) -> None:
        """Header indicates next action when analysis incomplete."""
        project = self._setup_project(tmp_path)
        ctx = _mock_ctx(str(project))

        result = transform_terminal_output(ctx, "Hello")
        # Level 2: next required is prd (in planning, since analysis complete)
        assert "next:" in result
        assert "prd" in result

    def test_all_complete_when_no_required(self, tmp_path: Path) -> None:
        """Header shows 'all-complete' when no next required slot."""
        project = self._setup_project(tmp_path)
        # Level 1: only product-brief is required
        yaml.safe_dump({
            "project": "test-proj",
            "level": 1,
            "created": "2026-05-17",
            "last_updated": "2026-05-17",
            "phases": {
                "analysis": {"product-brief": "complete"},
                "planning": {},
                "solutioning": {},
                "implementation": {"sprint-planning": "complete"},
            },
        }, open(project / "planning-artifacts" / "workflow-status.yaml", "w"), sort_keys=False)
        ctx = _mock_ctx(str(project))

        result = transform_terminal_output(ctx, "Hello")
        assert result is not None
        assert "all-complete" in result.lower() or "all" in result

    def test_suppressed_by_config(self, tmp_path: Path) -> None:
        """Header is suppressed when display.bmad_header=false."""
        project = self._setup_project(tmp_path)
        ctx = _mock_ctx(str(project), profile_config={"display": {"bmad_header": False}})

        result = transform_terminal_output(ctx, "Hello")
        assert result is None  # Header suppressed => passthrough

    def test_non_bmad_project_no_header(self, tmp_path: Path) -> None:
        """No header outside BMAD project."""
        ctx = _mock_ctx(str(tmp_path))
        result = transform_terminal_output(ctx, "Hello")
        assert result is None

    def test_header_not_too_long(self, tmp_path: Path) -> None:
        """Header is capped at 120 characters."""
        project = self._setup_project(tmp_path)
        ctx = _mock_ctx(str(project))

        result = transform_terminal_output(ctx, "Hello")
        header_line = result.split("\n")[0]
        assert len(header_line) <= 120

    def test_120_char_cap(self, tmp_path: Path) -> None:
        """Very long project names don't break the 120-char cap."""
        (tmp_path / "bmad").mkdir()
        yaml.safe_dump({
            "project_name": "a" * 200,
            "project_type": "api",
            "project_level": 2,
            "user_name": "tester",
        }, open(tmp_path / "bmad" / "config.yaml", "w"), sort_keys=False)
        (tmp_path / "planning-artifacts").mkdir()
        yaml.safe_dump({
            "project": "a" * 200,
            "level": 2,
            "created": "2026-05-17",
            "last_updated": "2026-05-17",
            "phases": {
                "analysis": {"product-brief": "complete"},
                "planning": {},
                "solutioning": {},
                "implementation": {"sprint-planning": "not-started"},
            },
        }, open(tmp_path / "planning-artifacts" / "workflow-status.yaml", "w"), sort_keys=False)
        ctx = _mock_ctx(str(tmp_path))

        result = transform_terminal_output(ctx, "Hello")
        header_line = result.split("\n")[0]
        assert len(header_line) <= 120

    def test_hook_never_raises(self, tmp_path: Path) -> None:
        """Internal errors don't propagate."""
        ctx = _mock_ctx(str(tmp_path))
        # tmp_path has no bmad/config.yaml
        result = transform_terminal_output(ctx, "Hello")
        assert result is None
