"""Integration tests for post_tool_call hook — auto-status tracking."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from plugins.bmad.hooks.post_tool_call import (
    post_tool_call,
    _relative_to_project,
    _match_path,
)


def _mock_ctx(project_dir: str):
    class MockCtx:
        pass
    ctx = MockCtx()
    ctx.project_dir = project_dir
    ctx.working_directory = project_dir
    ctx.profile_config = {}
    return ctx


class TestPostToolCall:
    """Auto-status tracking on file writes."""

    def _setup_project(self, tmp_path: Path, level: int = 2) -> Path:
        """Scaffold a BMAD project."""
        (tmp_path / "bmad").mkdir()
        yaml.safe_dump({
            "project_name": "test",
            "project_type": "api",
            "project_level": level,
            "user_name": "tester",
        }, open(tmp_path / "bmad" / "config.yaml", "w"), sort_keys=False)
        (tmp_path / "planning-artifacts").mkdir()
        (tmp_path / "planning-artifacts" / "research").mkdir()
        (tmp_path / "implementation-artifacts").mkdir()
        (tmp_path / "implementation-artifacts" / "stories").mkdir()
        yaml.safe_dump({
            "project": "test",
            "level": level,
            "created": "2026-05-17",
            "last_updated": "2026-05-17",
            "phases": {
                "analysis": {"product-brief": "not-started"},
                "planning": {},
                "solutioning": {},
                "implementation": {},
            },
        }, open(tmp_path / "planning-artifacts" / "workflow-status.yaml", "w"), sort_keys=False)
        return tmp_path

    def test_marks_product_brief_complete(self, tmp_path: Path) -> None:
        """Writing plan-artifacts/product-brief-*.md marks analysis/product-brief complete."""
        project = self._setup_project(tmp_path)
        ctx = _mock_ctx(str(project))

        post_tool_call(ctx, "Write",
            {"file_path": str(project / "planning-artifacts" / "product-brief-test-2026-05-17.md")},
            {"success": True})

        data = yaml.safe_load(
            (project / "planning-artifacts" / "workflow-status.yaml").read_text()
        )
        assert data["phases"]["analysis"]["product-brief"] == (
            "planning-artifacts/product-brief-test-2026-05-17.md"
        )

    def test_idempotent_no_bump(self, tmp_path: Path) -> None:
        """Writing the same artifact twice doesn't change last_updated."""
        project = self._setup_project(tmp_path)
        ctx = _mock_ctx(str(project))

        post_tool_call(ctx, "Write",
            {"file_path": str(project / "planning-artifacts" / "product-brief-first.md")},
            {"success": True})

        data1 = yaml.safe_load(
            (project / "planning-artifacts" / "workflow-status.yaml").read_text()
        )
        ts1 = data1["last_updated"]

        post_tool_call(ctx, "Write",
            {"file_path": str(project / "planning-artifacts" / "product-brief-first.md")},
            {"success": True})

        data2 = yaml.safe_load(
            (project / "planning-artifacts" / "workflow-status.yaml").read_text()
        )
        assert data2["last_updated"] == ts1  # No bump

    def test_ignores_non_artifact_files(self, tmp_path: Path) -> None:
        """Writing non-BMAD files doesn't change status."""
        project = self._setup_project(tmp_path)
        ctx = _mock_ctx(str(project))

        post_tool_call(ctx, "Write",
            {"file_path": str(project / "src" / "main.py")},
            {"success": True})

        data = yaml.safe_load(
            (project / "planning-artifacts" / "workflow-status.yaml").read_text()
        )
        assert data["phases"]["analysis"]["product-brief"] == "not-started"

    def test_ignores_read_tools(self, tmp_path: Path) -> None:
        """Read tool calls don't trigger status updates."""
        project = self._setup_project(tmp_path)
        ctx = _mock_ctx(str(project))

        post_tool_call(ctx, "Read",
            {"file_path": str(project / "planning-artifacts" / "product-brief-test.md")},
            None)

        data = yaml.safe_load(
            (project / "planning-artifacts" / "workflow-status.yaml").read_text()
        )
        assert data["phases"]["analysis"]["product-brief"] == "not-started"

    def test_marks_research_complete(self, tmp_path: Path) -> None:
        """Writing research files marks analysis/research complete."""
        project = self._setup_project(tmp_path)
        ctx = _mock_ctx(str(project))

        post_tool_call(ctx, "Write",
            {"file_path": str(project / "planning-artifacts" / "research" / "competitor-analysis.md")},
            {"success": True})

        data = yaml.safe_load(
            (project / "planning-artifacts" / "workflow-status.yaml").read_text()
        )
        assert data["phases"]["analysis"]["research"] == (
            "planning-artifacts/research/competitor-analysis.md"
        )

    def test_marks_story_complete(self, tmp_path: Path) -> None:
        """Writing implementation-artifacts/stories/*.md marks implementation/story complete."""
        project = self._setup_project(tmp_path)
        ctx = _mock_ctx(str(project))

        post_tool_call(ctx, "Write",
            {"file_path": str(project / "implementation-artifacts" / "stories" / "epic1-story1.md")},
            {"success": True})

        data = yaml.safe_load(
            (project / "planning-artifacts" / "workflow-status.yaml").read_text()
        )
        assert data["phases"]["implementation"]["story"] == (
            "implementation-artifacts/stories/epic1-story1.md"
        )

    def test_non_bmad_project_noop(self, tmp_path: Path) -> None:
        """Hook is silent outside BMAD projects."""
        ctx = _mock_ctx(str(tmp_path))
        result = post_tool_call(ctx, "Write",
            {"file_path": str(tmp_path / "planning-artifacts" / "product-brief.md")},
            {"success": True})
        # Should not throw, no assertion needed

    def test_hook_never_raises(self, tmp_path: Path) -> None:
        """Internal errors in the hook don't propagate."""
        project = self._setup_project(tmp_path)
        # Delete the status file to cause an error
        (project / "planning-artifacts" / "workflow-status.yaml").unlink()
        ctx = _mock_ctx(str(project))
        # Should not raise
        post_tool_call(ctx, "Write",
            {"file_path": str(project / "planning-artifacts" / "product-brief-test.md")},
            {"success": True})


class TestMatchPath:
    """Path pattern matching."""

    def test_solutioning_gate_check(self) -> None:
        result = _match_path("planning-artifacts/solutioning-gate-check-test.md")
        assert result == ("solutioning", "solutioning-gate-check")

    def test_epics_stories_dir(self) -> None:
        result = _match_path("planning-artifacts/epics-stories/epic1.md")
        assert result == ("solutioning", "epics-stories")

    def test_prd_underscore(self) -> None:
        result = _match_path("planning-artifacts/prd_myapp_2026.md")
        assert result is not None
