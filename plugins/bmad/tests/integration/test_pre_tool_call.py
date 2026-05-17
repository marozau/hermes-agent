"""Integration tests for pre_tool_call hook — phase gate enforcement."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml
import pytest

from plugins.bmad.hooks.pre_tool_call import (
    pre_tool_call,
    _relative_to_project,
    _match_path,
    PATH_RULES,
)


def _make_status(phases: dict) -> dict:
    """Helper: build a full status dict."""
    return {
        "project": "test",
        "level": 2,
        "created": "2026-05-17",
        "last_updated": "2026-05-17",
        "phases": phases,
    }


def _mock_ctx(project_dir: str, bmad_yolo: bool = False):
    """Build a minimal mock context."""
    class MockCtx:
        pass
    ctx = MockCtx()
    ctx.project_dir = project_dir
    ctx.working_directory = project_dir
    ctx.profile_config = {"bmad_yolo": bmad_yolo}
    return ctx


class TestRelativeToProject:
    """Tests for _relative_to_project helper."""

    def test_path_inside_project(self, tmp_path: Path) -> None:
        rel = _relative_to_project(str(tmp_path / "bmad" / "config.yaml"), tmp_path)
        assert rel == "bmad/config.yaml"

    def test_path_outside_project(self, tmp_path: Path) -> None:
        rel = _relative_to_project("/etc/passwd", tmp_path)
        assert rel is None

    def test_root_path(self, tmp_path: Path) -> None:
        rel = _relative_to_project(str(tmp_path), tmp_path)
        assert rel == "."


class TestMatchPath:
    """Tests for _match_path with PATH_RULES."""

    def test_product_brief(self) -> None:
        result = _match_path("planning-artifacts/product-brief-myapp-2026-05-17.md")
        assert result == ("analysis", "product-brief")

    def test_prd(self) -> None:
        result = _match_path("planning-artifacts/prd-myapp-2026-05-17.md")
        assert result == ("planning", "prd")

    def test_architecture(self) -> None:
        result = _match_path("planning-artifacts/architecture-myapp-2026-05-17.md")
        assert result == ("solutioning", "architecture")

    def test_epics_stories(self) -> None:
        result = _match_path("planning-artifacts/epics-stories-myapp.md")
        assert result == ("solutioning", "epics-stories")

    def test_story_file(self) -> None:
        result = _match_path("implementation-artifacts/stories/epic1-story2.md")
        assert result == ("implementation", "story")

    def test_no_match(self) -> None:
        result = _match_path("src/main.py")
        assert result is None

    def test_research(self) -> None:
        result = _match_path("planning-artifacts/research/competitor-analysis.md")
        assert result == ("analysis", "research")


class TestPreToolCallNoBmadProject:
    """Hook is silent outside BMAD projects."""

    def test_non_bmad_directory(self, tmp_path: Path) -> None:
        ctx = _mock_ctx(str(tmp_path))
        result = pre_tool_call(ctx, "Write", {"file_path": "src/main.py"}, None)
        assert result is None

    def test_missing_ctx_project_dir(self) -> None:
        class BareCtx:
            pass
        result = pre_tool_call(BareCtx(), "Write", {"file_path": "/tmp/x"}, None)
        assert result is None


class TestPreToolCallGating:
    """Phase gate blocking for real BMAD projects."""

    def _setup_bmad_project(self, tmp_path: Path, phases: dict) -> Path:
        """Scaffold a minimal BMAD project with given phases."""
        (tmp_path / "bmad").mkdir()
        yaml.safe_dump({
            "project_name": "test",
            "project_type": "api",
            "project_level": 2,
            "user_name": "tester",
        }, open(tmp_path / "bmad" / "config.yaml", "w"), sort_keys=False)
        (tmp_path / "planning-artifacts").mkdir()
        (tmp_path / "planning-artifacts" / "research").mkdir()
        (tmp_path / "implementation-artifacts").mkdir()
        (tmp_path / "implementation-artifacts" / "stories").mkdir()
        yaml.safe_dump(_make_status(phases),
            open(tmp_path / "planning-artifacts" / "workflow-status.yaml", "w"),
            sort_keys=False)
        return tmp_path

    def test_blocks_planning_artifact_when_analysis_incomplete(self, tmp_path: Path) -> None:
        """Writing to planning/ requires analysis phase complete."""
        project = self._setup_bmad_project(tmp_path, {
            "analysis": {"product-brief": "not-started"},
            "planning": {},
            "solutioning": {},
            "implementation": {},
        })
        ctx = _mock_ctx(str(project))
        result = pre_tool_call(ctx, "Write",
            {"file_path": str(project / "planning-artifacts" / "prd-test.md")},
            None)
        assert result is not None
        assert result["action"] == "block"

    def test_allows_analysis_writes(self, tmp_path: Path) -> None:
        """Writing to analysis-phase artifacts is always allowed."""
        project = self._setup_bmad_project(tmp_path, {
            "analysis": {"product-brief": "not-started"},
            "planning": {},
            "solutioning": {},
            "implementation": {},
        })
        ctx = _mock_ctx(str(project))
        result = pre_tool_call(ctx, "Write",
            {"file_path": str(project / "planning-artifacts" / "product-brief-test.md")},
            None)
        assert result is None  # allowed

    def test_allows_when_preceding_phase_complete(self, tmp_path: Path) -> None:
        """Writing to planning is allowed when analysis is complete."""
        project = self._setup_bmad_project(tmp_path, {
            "analysis": {"product-brief": "complete"},
            "planning": {"prd": "not-started"},
            "solutioning": {},
            "implementation": {},
        })
        ctx = _mock_ctx(str(project))
        result = pre_tool_call(ctx, "Write",
            {"file_path": str(project / "planning-artifacts" / "prd-test.md")},
            None)
        assert result is None  # allowed

    def test_yolo_bypasses_gate(self, tmp_path: Path) -> None:
        """bmad_yolo=true allows all writes regardless of phase state."""
        project = self._setup_bmad_project(tmp_path, {
            "analysis": {"product-brief": "not-started"},
            "planning": {},
            "solutioning": {},
            "implementation": {},
        })
        ctx = _mock_ctx(str(project), bmad_yolo=True)
        result = pre_tool_call(ctx, "Write",
            {"file_path": str(project / "planning-artifacts" / "prd-test.md")},
            None)
        assert result is None  # allowed via YOLO

    def test_non_bmad_file_not_gated(self, tmp_path: Path) -> None:
        """Writing files that don't match BMAD artifact patterns is always allowed."""
        project = self._setup_bmad_project(tmp_path, {
            "analysis": {"product-brief": "not-started"},
            "planning": {},
            "solutioning": {},
            "implementation": {},
        })
        ctx = _mock_ctx(str(project))
        result = pre_tool_call(ctx, "Write",
            {"file_path": str(project / "src" / "main.py")},
            None)
        assert result is None  # not gated

    def test_ignores_read_tools(self, tmp_path: Path) -> None:
        """Read-only tools are never gated."""
        project = self._setup_bmad_project(tmp_path, {
            "analysis": {"product-brief": "not-started"},
            "planning": {},
            "solutioning": {},
            "implementation": {},
        })
        ctx = _mock_ctx(str(project))
        result = pre_tool_call(ctx, "Read", {"file_path": "planning-artifacts/prd-test.md"}, None)
        assert result is None  # Read tools ignored

    def test_hook_never_raises_on_bad_state(self, tmp_path: Path) -> None:
        """Missing workflow-status.yaml doesn't crash the hook."""
        project = self._setup_bmad_project(tmp_path, {
            "analysis": {"product-brief": "complete"},
            "planning": {},
            "solutioning": {},
            "implementation": {},
        })
        # Delete status file
        (project / "planning-artifacts" / "workflow-status.yaml").unlink()
        ctx = _mock_ctx(str(project))
        result = pre_tool_call(ctx, "Write",
            {"file_path": str(project / "planning-artifacts" / "prd-test.md")},
            None)
        assert result is None  # Silently allows on internal error
