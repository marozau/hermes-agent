"""Tests for Story 9.1 — access_count + last_hit_at reinforcement."""
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest


@pytest.fixture
def memory_dirs(tmp_path):
    """Provide isolated memory + raw dirs."""
    mem = tmp_path / "memory" / "typed"
    mem.mkdir(parents=True)
    raw = tmp_path / "raw"
    raw.mkdir(parents=True)
    os.environ["HERMES_MEMORY_DIR"] = str(mem)
    os.environ["HERMES_RAW_DIR"] = str(raw)
    os.environ["HERMES_PROJECT"] = "test-proj"
    os.environ["HERMES_ROLE"] = "test-role"
    yield mem, raw
    os.environ.pop("HERMES_MEMORY_DIR", None)
    os.environ.pop("HERMES_RAW_DIR", None)
    os.environ.pop("HERMES_PROJECT", None)
    os.environ.pop("HERMES_ROLE", None)


def _add_test_entry(memory_dirs, body="test body", type="fact", source="test"):
    """Helper to add an entry and return its ID."""
    from lib.hermes_memory import add_entry
    entry_id = add_entry(type=type, body=body, source=source)
    return entry_id


class TestReinforceEntry:
    """Story 9.1 AC: reinforce_entry() behavior."""

    def test_increments_access_count(self, memory_dirs):
        """AC: bump access_count on each reinforce."""
        from lib.hermes_memory import reinforce_entry, _read_entry_file, _resolve_memory_dir
        eid = _add_test_entry(memory_dirs)
        mem_path = _resolve_memory_dir()
        # Pre-existing entry has no access_count
        fm, _ = _read_entry_file(eid, mem_path)
        assert fm.get("access_count", 0) == 0

        reinforce_entry(eid)
        fm, _ = _read_entry_file(eid, mem_path)
        assert fm["access_count"] == 1

        reinforce_entry(eid, source="different-source")
        fm, _ = _read_entry_file(eid, mem_path)
        assert fm["access_count"] == 2

    def test_sets_last_hit_at(self, memory_dirs):
        """AC: set last_hit_at=now on reinforce."""
        from lib.hermes_memory import reinforce_entry, _read_entry_file, _resolve_memory_dir
        eid = _add_test_entry(memory_dirs)
        mem_path = _resolve_memory_dir()

        before = datetime.now(timezone.utc).isoformat()
        reinforce_entry(eid)
        fm, _ = _read_entry_file(eid, mem_path)
        after = datetime.now(timezone.utc).isoformat()

        assert fm["last_hit_at"] is not None
        assert before[:19] <= fm["last_hit_at"][:19] <= after[:19]

    def test_body_unchanged(self, memory_dirs):
        """AC: body bytes unchanged (content-hash stable)."""
        from lib.hermes_memory import reinforce_entry, _read_entry_file, _resolve_memory_dir
        body = "this is the sacred body that must not change"
        eid = _add_test_entry(memory_dirs, body=body)
        mem_path = _resolve_memory_dir()

        _, body_before = _read_entry_file(eid, mem_path)
        reinforce_entry(eid)
        _, body_after = _read_entry_file(eid, mem_path)

        assert body_before == body_after

    def test_raw_layer_event_written(self, memory_dirs):
        """AC: pairs with raw-layer append {event: reinforce, id, source}."""
        from lib.hermes_memory import reinforce_entry
        _, raw_dir = memory_dirs
        eid = _add_test_entry(memory_dirs)

        reinforce_entry(eid, source="verify-cited-hit")

        # Find the raw JSONL file
        raw_day_dir = raw_dir / "test-proj" / "test-role"
        jsonl_files = list(raw_day_dir.glob("*.jsonl"))
        assert len(jsonl_files) >= 1

        reinforce_events = []
        for f in jsonl_files:
            for line in f.read_text().splitlines():
                row = json.loads(line)
                if row.get("kind") == "reinforce":
                    reinforce_events.append(row)

        assert len(reinforce_events) == 1
        assert reinforce_events[0]["entry_id"] == eid
        assert reinforce_events[0]["content"] == "verify-cited-hit"
        assert "ts" in reinforce_events[0]

    def test_idempotency_same_source(self, memory_dirs):
        """AC: same (entry_id, source) pair reported twice doesn't double-bump."""
        from lib.hermes_memory import reinforce_entry, _read_entry_file, _resolve_memory_dir
        eid = _add_test_entry(memory_dirs)
        mem_path = _resolve_memory_dir()

        reinforce_entry(eid, source="verify-cited-hit")
        reinforce_entry(eid, source="verify-cited-hit")  # second call — should be no-op

        fm, _ = _read_entry_file(eid, mem_path)
        assert fm["access_count"] == 1  # not 2

    def test_different_source_allows_reinforce(self, memory_dirs):
        """Different source strings are not deduped against each other."""
        from lib.hermes_memory import reinforce_entry, _read_entry_file, _resolve_memory_dir
        eid = _add_test_entry(memory_dirs)
        mem_path = _resolve_memory_dir()

        reinforce_entry(eid, source="verify-cited-hit")
        reinforce_entry(eid, source="trajectory-rematch")

        fm, _ = _read_entry_file(eid, mem_path)
        assert fm["access_count"] == 2

    def test_backward_compat_no_access_count_field(self, memory_dirs):
        """AC: pre-existing entries without access_count read as 0."""
        from lib.hermes_memory import reinforce_entry, _read_entry_file, _resolve_memory_dir
        eid = _add_test_entry(memory_dirs)
        mem_path = _resolve_memory_dir()

        # Verify entry starts without access_count
        fm, _ = _read_entry_file(eid, mem_path)
        assert "access_count" not in fm or fm.get("access_count") is None

        reinforce_entry(eid)
        fm, _ = _read_entry_file(eid, mem_path)
        assert fm["access_count"] == 1

    def test_negative_access_count_reset(self, memory_dirs):
        """Malformed negative access_count resets to 0 before bump."""
        from lib.hermes_memory import reinforce_entry, _read_entry_file, _write_entry_file, _resolve_memory_dir
        eid = _add_test_entry(memory_dirs)
        mem_path = _resolve_memory_dir()

        # Manually corrupt the entry with a negative access_count
        fm, body = _read_entry_file(eid, mem_path)
        fm["access_count"] = -5
        _write_entry_file(eid, fm, body, mem_path)

        reinforce_entry(eid)
        fm, _ = _read_entry_file(eid, mem_path)
        assert fm["access_count"] == 1  # reset to 0 then bumped to 1

    def test_different_sessions_both_count(self, memory_dirs):
        """A3: Two different sessions citing same entry both increment (not deduped)."""
        from lib.hermes_memory import reinforce_entry, _read_entry_file, _resolve_memory_dir
        eid = _add_test_entry(memory_dirs)
        mem_path = _resolve_memory_dir()

        reinforce_entry(eid, source="verify-cited-hit", session_id="session-A")
        reinforce_entry(eid, source="verify-cited-hit", session_id="session-B")

        fm, _ = _read_entry_file(eid, mem_path)
        assert fm["access_count"] == 2  # both counted

    def test_same_session_different_entry_ids(self, memory_dirs):
        """A3: Same session citing different entries — both count."""
        from lib.hermes_memory import reinforce_entry, _read_entry_file, _resolve_memory_dir
        eid1 = _add_test_entry(memory_dirs, body="entry 1")
        eid2 = _add_test_entry(memory_dirs, body="entry 2")
        mem_path = _resolve_memory_dir()

        reinforce_entry(eid1, source="verify-cited-hit", session_id="session-X")
        reinforce_entry(eid2, source="verify-cited-hit", session_id="session-X")

        fm1, _ = _read_entry_file(eid1, mem_path)
        fm2, _ = _read_entry_file(eid2, mem_path)
        assert fm1["access_count"] == 1
        assert fm2["access_count"] == 1

    def test_file_not_found_raises(self, memory_dirs):
        """Reinforcing a non-existent entry raises FileNotFoundError."""
        from lib.hermes_memory import reinforce_entry
        with pytest.raises(FileNotFoundError):
            reinforce_entry("nonexistent-entry-id")


class TestReadEntriesIncludesNewFields:
    """Story 9.1 AC: read_entries returns access_count + last_hit_at."""

    def test_new_entries_have_fields(self, memory_dirs):
        from lib.hermes_memory import read_entries, reinforce_entry
        eid = _add_test_entry(memory_dirs)
        reinforce_entry(eid)

        entries = read_entries()
        entry = next(e for e in entries if e["id"] == eid)
        assert entry["access_count"] == 1
        assert entry["last_hit_at"] is not None

    def test_old_entries_default_to_zero(self, memory_dirs):
        from lib.hermes_memory import read_entries
        _add_test_entry(memory_dirs)

        entries = read_entries()
        assert len(entries) == 1
        assert entries[0]["access_count"] == 0
        assert entries[0]["last_hit_at"] is None
