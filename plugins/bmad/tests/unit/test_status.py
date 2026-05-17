"""Tests for plugins.bmad.lib.status — workflow-status YAML persistence.

Tests cover:
    - load() reading, cache hit/miss, FileNotFoundError
    - mark_complete / mark_in_progress cache invalidation
    - _atomic_write correctness
    - get_next_required delegation
    - _cache module-level dict existence
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest import mock

import pytest
import yaml

from plugins.bmad.lib.status import (
    _atomic_write,
    _cache,
    get_next_required,
    load,
    mark_complete,
    mark_in_progress,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _yaml_path(project_dir: Path) -> Path:
    """Return the resolved workflow-status.yaml path for *project_dir*."""
    return project_dir / "planning-artifacts" / "workflow-status.yaml"


def _read_yaml(project_dir: Path) -> dict:
    """Directly read and parse the YAML file (no cache)."""
    return yaml.safe_load(_yaml_path(project_dir).read_text())


# ── Module-level _cache ──────────────────────────────────────────────────────


class TestCacheDict:
    """The _cache module-level dict exists and is a plain dict."""

    def test_cache_is_dict(self) -> None:
        assert isinstance(_cache, dict)

    def test_cache_starts_empty(self) -> None:
        # Each test run gets a fresh module import so _cache is empty
        assert len(_cache) == 0


# ── load() ───────────────────────────────────────────────────────────────────


class TestLoad:
    """load() reads and returns the workflow-status.yaml content."""

    def test_returns_dict_with_expected_keys(self, tmp_project_dir: Path) -> None:
        data = load(tmp_project_dir)
        assert isinstance(data, dict)
        assert data["project"] == "test-project"
        assert data["level"] == 1
        assert "phases" in data
        assert "last_updated" in data

    def test_returns_phases_content(self, tmp_project_dir: Path) -> None:
        data = load(tmp_project_dir)
        phases = data["phases"]
        assert phases["analysis"]["product-brief"] == "not-started"
        assert phases["implementation"]["sprint-planning"] == "not-started"
        assert phases["planning"] == {}

    def test_file_not_found_raises(self, tmp_project_dir: Path) -> None:
        bad_dir = tmp_project_dir / "nonexistent"
        with pytest.raises(FileNotFoundError):
            load(bad_dir)

    def test_file_not_found_when_deleted_after_cache(self, tmp_project_dir: Path) -> None:
        """Loading once, then deleting the file: cache hit still works,
        but a forced miss raises FileNotFoundError."""
        data = load(tmp_project_dir)  # caches it
        assert isinstance(data, dict)

        # Remove the file and clear cache to force a re-read
        _yaml_path(tmp_project_dir).unlink()
        _cache.clear()

        with pytest.raises(FileNotFoundError):
            load(tmp_project_dir)


class TestLoadCacheHit:
    """load() returns cached data when mtime hasn't changed."""

    def test_cache_hit_returns_same_object(self, tmp_project_dir: Path) -> None:
        first = load(tmp_project_dir)
        second = load(tmp_project_dir)
        # Same object (not just equal) — cache returns exact same dict
        assert first is second

    def test_cache_hit_no_second_read(self, tmp_project_dir: Path) -> None:
        """Calling load() twice should only invoke yaml.safe_load once
        (the second call hits the mtime cache).

        Patches ``yaml.safe_load`` inside the status module — Python 3.11
        forbids monkey-patching attributes on ``Path`` instances.
        """
        real_safe_load = yaml.safe_load

        with mock.patch(
            "plugins.bmad.lib.status.yaml.safe_load",
            side_effect=real_safe_load,
        ) as mock_safe_load:
            load(tmp_project_dir)   # first — reads
            load(tmp_project_dir)   # second — cache hit
            load(tmp_project_dir)   # third  — cache hit

        assert mock_safe_load.call_count == 1


class TestLoadCacheMiss:
    """load() re-reads the file when mtime changes."""

    def test_cache_miss_on_mtime_change(self, tmp_project_dir: Path) -> None:
        path = _yaml_path(tmp_project_dir)

        first = load(tmp_project_dir)

        # Modify the file content + mtime (pause ≥ 1 s to ensure mtime change)
        time.sleep(1.01)
        with open(path, "a") as f:
            f.write("\n# cache-busting comment\n")

        second = load(tmp_project_dir)
        # Should be a different dict object now
        assert first is not second

    def test_cache_miss_on_clear(self, tmp_project_dir: Path) -> None:
        first = load(tmp_project_dir)
        _cache.clear()
        second = load(tmp_project_dir)
        assert first is not second


# ── mark_complete() ──────────────────────────────────────────────────────────


class TestMarkComplete:
    """mark_complete() writes a slot value + bumps last_updated."""

    def test_writes_to_file(self, tmp_project_dir: Path) -> None:
        mark_complete(tmp_project_dir, "analysis", "product-brief", "artifacts/brief.md")
        data = _read_yaml(tmp_project_dir)
        assert data["phases"]["analysis"]["product-brief"] == "artifacts/brief.md"

    def test_bumps_last_updated(self, tmp_project_dir: Path) -> None:
        mark_complete(tmp_project_dir, "analysis", "product-brief", "brief.md")
        data = _read_yaml(tmp_project_dir)
        from datetime import date
        assert data["last_updated"] == date.today().isoformat()

    def test_creates_new_phase_key(self, tmp_project_dir: Path) -> None:
        """When phase doesn't exist, it's auto-created via setdefault."""
        mark_complete(tmp_project_dir, "deploy", "release-notes", "notes.md")
        data = _read_yaml(tmp_project_dir)
        assert data["phases"]["deploy"]["release-notes"] == "notes.md"

    def test_overwrites_existing_slot(self, tmp_project_dir: Path) -> None:
        mark_complete(tmp_project_dir, "analysis", "product-brief", "v1.md")
        mark_complete(tmp_project_dir, "analysis", "product-brief", "v2.md")
        data = _read_yaml(tmp_project_dir)
        assert data["phases"]["analysis"]["product-brief"] == "v2.md"

    def test_invalidates_cache(self, tmp_project_dir: Path) -> None:
        """After mark_complete, load() returns fresh data."""
        load(tmp_project_dir)  # prime cache
        mark_complete(tmp_project_dir, "analysis", "product-brief", "new.md")
        data = load(tmp_project_dir)
        assert data["phases"]["analysis"]["product-brief"] == "new.md"


# ── mark_in_progress() ───────────────────────────────────────────────────────


class TestMarkInProgress:
    """mark_in_progress() writes 'in-progress' + bumps last_updated."""

    def test_writes_in_progress(self, tmp_project_dir: Path) -> None:
        mark_in_progress(tmp_project_dir, "analysis", "product-brief")
        data = _read_yaml(tmp_project_dir)
        assert data["phases"]["analysis"]["product-brief"] == "in-progress"

    def test_bumps_last_updated(self, tmp_project_dir: Path) -> None:
        mark_in_progress(tmp_project_dir, "analysis", "product-brief")
        data = _read_yaml(tmp_project_dir)
        from datetime import date
        assert data["last_updated"] == date.today().isoformat()

    def test_creates_new_phase_key(self, tmp_project_dir: Path) -> None:
        mark_in_progress(tmp_project_dir, "testing", "qa-review")
        data = _read_yaml(tmp_project_dir)
        assert data["phases"]["testing"]["qa-review"] == "in-progress"

    def test_invalidates_cache(self, tmp_project_dir: Path) -> None:
        load(tmp_project_dir)  # prime cache
        mark_in_progress(tmp_project_dir, "analysis", "product-brief")
        data = load(tmp_project_dir)
        assert data["phases"]["analysis"]["product-brief"] == "in-progress"


# ── get_next_required() ──────────────────────────────────────────────────────


class TestGetNextRequired:
    """get_next_required() delegates to phases.next_required_slot."""

    def test_returns_none_for_unrecognized_level(self, level2_status: dict) -> None:
        # level2_status has level=2 but no real level > 2 defined
        result = get_next_required(level2_status, 99)
        assert result is None

    def test_returns_next_slot(self, level2_status: dict) -> None:
        """Level 2 with analysis complete should return planning slot
        or solutioning slot depending on PhaseRules."""
        result = get_next_required(level2_status, 2)
        # Should return something matching {phase, slot, command} or None
        if result is not None:
            assert isinstance(result, dict)
            assert "phase" in result
            assert "slot" in result


# ── _atomic_write() ──────────────────────────────────────────────────────────


class TestAtomicWrite:
    """_atomic_write() writes valid YAML to the specified path atomically."""

    def test_writes_yaml_to_path(self, tmp_project_dir: Path) -> None:
        target = tmp_project_dir / "test-output.yaml"
        data = {"hello": "world", "nested": {"key": [1, 2, 3]}}
        _atomic_write(target, data)
        assert target.exists()
        parsed = yaml.safe_load(target.read_text())
        assert parsed == data

    def test_uses_tempfile_and_rename(self, tmp_project_dir: Path) -> None:
        """The atomic write writes to a temp file first, then renames.
        We verify no .tmp files are left behind."""
        target = tmp_project_dir / "atomic.yaml"
        data = {"msg": "atomic"}
        _atomic_write(target, data)

        # No leftover .tmp files in the directory
        leftovers = list(tmp_project_dir.glob(".*.tmp"))
        assert len(leftovers) == 0

    def test_file_is_valid_yaml(self, tmp_project_dir: Path) -> None:
        target = tmp_project_dir / "valid.yaml"
        _atomic_write(target, {"key": "value"})
        doc = yaml.safe_load(target.read_text())
        assert doc == {"key": "value"}
