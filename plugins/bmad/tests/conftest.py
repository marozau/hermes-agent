"""Shared test fixtures for BMAD plugin tests."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _clear_status_cache():
    """Clear the module-level status cache between tests.

    The cache is process-global; without this, parallel tests pollute each
    other (e.g. test_cache_starts_empty fails if any other test ran first).
    """
    try:
        from plugins.bmad.lib import status as _status
        _status._cache.clear()
    except ImportError:
        pass
    yield
    try:
        from plugins.bmad.lib import status as _status
        _status._cache.clear()
    except ImportError:
        pass


@pytest.fixture
def tmp_project_dir() -> Path:
    """Create a temporary directory that looks like a BMAD project root.

    Creates:
      bmad/config.yaml
      planning-artifacts/workflow-status.yaml (with default level-1 slots)
      planning-artifacts/research/
      implementation-artifacts/stories/
    """
    with tempfile.TemporaryDirectory() as td:
        path = Path(td)

        # bmad/config.yaml
        (path / "bmad").mkdir()
        import yaml
        yaml.safe_dump({
            "project_name": "test-project",
            "project_type": "api",
            "project_level": 1,
            "user_name": "tester",
            "planning_artifacts": "planning-artifacts",
            "implementation_artifacts": "implementation-artifacts",
            "created": "2026-05-17",
        }, open(path / "bmad" / "config.yaml", "w"), sort_keys=False)

        # planning-artifacts/
        (path / "planning-artifacts").mkdir()
        (path / "planning-artifacts" / "research").mkdir()
        (path / "implementation-artifacts").mkdir()
        (path / "implementation-artifacts" / "stories").mkdir()

        # workflow-status.yaml
        yaml.safe_dump({
            "project": "test-project",
            "level": 1,
            "created": "2026-05-17",
            "last_updated": "2026-05-17",
            "phases": {
                "analysis": {"product-brief": "not-started"},
                "planning": {},
                "solutioning": {},
                "implementation": {"sprint-planning": "not-started"},
            },
        }, open(path / "planning-artifacts" / "workflow-status.yaml", "w"), sort_keys=False)

        yield path


@pytest.fixture
def mock_ctx() -> object:
    """A minimal mock Hermes context with profile_config."""
    class MockCtx:
        project_dir = "/tmp/test-project"
        working_directory = "/tmp/test-project"
        profile_config = {}

    return MockCtx()


@pytest.fixture
def level2_status() -> dict:
    """A level-2 status dict with some completions."""
    return {
        "project": "test-project",
        "level": 2,
        "created": "2026-05-17",
        "last_updated": "2026-05-17",
        "phases": {
            "analysis": {"product-brief": "complete"},
            "planning": {"prd": "complete"},
            "solutioning": {"architecture": "not-started"},
            "implementation": {"sprint-planning": "not-started"},
        },
    }
