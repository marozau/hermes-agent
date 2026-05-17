"""Integration tests for on_session_start hook — project detection & cache warm."""

from __future__ import annotations

from pathlib import Path

import yaml
import pytest

from plugins.bmad.hooks.on_session_start import on_session_start, _resolve_project_dir
from plugins.bmad.lib import status as status_module


def _mock_ctx(project_dir: str | None):
    class MockCtx:
        pass
    ctx = MockCtx()
    if project_dir is not None:
        ctx.project_dir = project_dir
        ctx.working_directory = project_dir
    return ctx


def _scaffold(tmp_path: Path, level: int = 2) -> Path:
    (tmp_path / "bmad").mkdir()
    yaml.safe_dump({
        "project_name": "session-test",
        "project_type": "api",
        "project_level": level,
        "user_name": "tester",
    }, open(tmp_path / "bmad" / "config.yaml", "w"), sort_keys=False)
    (tmp_path / "planning-artifacts").mkdir()
    yaml.safe_dump({
        "project": "session-test",
        "level": level,
        "created": "2026-05-17",
        "last_updated": "2026-05-17",
        "phases": {
            "analysis": {"product-brief": "not-started"},
            "planning": {"prd": "not-started"},
            "solutioning": {},
            "implementation": {"sprint-planning": "not-started"},
        },
    }, open(tmp_path / "planning-artifacts" / "workflow-status.yaml", "w"), sort_keys=False)
    return tmp_path


class TestOnSessionStart:
    """Hook fires at session start and warms the status cache for BMAD projects."""

    def test_warms_cache_when_bmad_project(self, tmp_path: Path) -> None:
        project = _scaffold(tmp_path)
        ctx = _mock_ctx(str(project))

        on_session_start(ctx)

        status_path = project / "planning-artifacts" / "workflow-status.yaml"
        assert status_path in status_module._cache, (
            "Cache should contain status after on_session_start"
        )

    def test_silent_when_no_bmad_config(self, tmp_path: Path) -> None:
        """No bmad/config.yaml → silent no-op (no errors, no env changes)."""
        ctx = _mock_ctx(str(tmp_path))
        # Should not raise
        result = on_session_start(ctx)
        assert result is None
        status_path = tmp_path / "planning-artifacts" / "workflow-status.yaml"
        assert status_path not in status_module._cache

    def test_silent_when_no_project_dir(self) -> None:
        """ctx without project_dir → silent no-op."""
        ctx = _mock_ctx(None)
        result = on_session_start(ctx)
        assert result is None

    def test_hook_never_raises_on_corrupt_status(self, tmp_path: Path) -> None:
        """A malformed workflow-status.yaml must not crash the session."""
        (tmp_path / "bmad").mkdir()
        (tmp_path / "bmad" / "config.yaml").write_text("project_name: bad\n")
        (tmp_path / "planning-artifacts").mkdir()
        (tmp_path / "planning-artifacts" / "workflow-status.yaml").write_text(
            ":\n\t- not: valid: yaml\n:::"
        )
        ctx = _mock_ctx(str(tmp_path))
        # Should not raise even though YAML parse will fail
        result = on_session_start(ctx)
        assert result is None

    def test_hook_never_raises_on_missing_status_file(self, tmp_path: Path) -> None:
        """bmad/config.yaml present but workflow-status.yaml missing → no crash."""
        (tmp_path / "bmad").mkdir()
        (tmp_path / "bmad" / "config.yaml").write_text("project_name: x\n")
        # No planning-artifacts dir at all
        ctx = _mock_ctx(str(tmp_path))
        result = on_session_start(ctx)
        assert result is None


class TestResolveProjectDir:
    """_resolve_project_dir prefers project_dir, falls back to working_directory."""

    def test_uses_project_dir(self) -> None:
        ctx = _mock_ctx("/tmp/foo")
        assert _resolve_project_dir(ctx) == Path("/tmp/foo")

    def test_falls_back_to_working_directory(self) -> None:
        class MockCtx:
            pass
        ctx = MockCtx()
        ctx.working_directory = "/tmp/bar"
        # project_dir attribute missing
        assert _resolve_project_dir(ctx) == Path("/tmp/bar")

    def test_returns_none_when_both_missing(self) -> None:
        ctx = _mock_ctx(None)
        assert _resolve_project_dir(ctx) is None
