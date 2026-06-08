"""Epic 1 — Typed Memory Foundation: test suite.

Covers FR-1..FR-8, NFR-16, plus the code-review patches:
  P2  fail-closed on malformed valid_until + Z-suffix normalization
  P3  ULID monotonicity within a millisecond
  P4  CRLF / BOM / trailing-space tolerance in frontmatter detection
  P5  **kwargs cannot override canonical fields
  P6  atomic write (no partial files)
  P7  60-second debounce uses on-disk last_used_at (cross-process)
  P8  body returned verbatim (not stripped)
  P9  update_entry re-runs the secret scanner
  P11 non-ULID filenames are not rewritten on read
  P13 legacy entry touch backfills frontmatter
  P14 added tests for FR-3 surface, debounce, latency
"""
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

# conftest.py puts ~/.hermes on sys.path; import explicitly to fail loudly otherwise.
from autodream.memory import (
    _generate_ulid,
    _is_ulid,
    _scan_for_secrets,
    add_entry,
    expire_entry,
    read_entries,
    supersede_entry,
    update_entry,
)


@pytest.fixture
def memdir(tmp_path, monkeypatch):
    """Isolated memory dir per test, also exposed via HERMES_MEMORY_DIR."""
    d = tmp_path / "memory"
    d.mkdir()
    monkeypatch.setenv("HERMES_MEMORY_DIR", str(d))
    return d


def _read_fm(path):
    content = path.read_text(encoding="utf-8")
    fm_str = content.split("---\n", 2)[1]
    return yaml.safe_load(fm_str)


# ═════════════════════════════════════════════════════════════════════════════
# Story 1.1: Canonical writer with mandatory frontmatter
# ═════════════════════════════════════════════════════════════════════════════

class TestAddEntryFrontmatter:
    def test_emits_all_required_frontmatter_fields(self, memdir):
        entry_id = add_entry(
            type="preference",
            body="Always use pytest for testing.",
            source="user-correction",
        )
        files = list(memdir.glob("*.md"))
        assert len(files) == 1
        fm = _read_fm(files[0])
        assert fm["id"] == entry_id
        assert len(fm["id"]) == 26
        assert fm["type"] == "preference"
        assert "created_at" in fm
        assert "last_used_at" in fm
        assert fm["source"] == "user-correction"
        assert fm["valid_until"] is None
        assert fm["supersedes"] is None
        assert fm["evidence"] is None

    def test_ulid_is_unique_per_call(self, memdir):
        id1 = add_entry("fact", "Fact A", "self-derived")
        id2 = add_entry("fact", "Fact B", "self-derived")
        assert id1 != id2
        assert len(list(memdir.glob("*.md"))) == 2

    def test_body_preserved_verbatim(self, memdir):
        body = "  Line with leading spaces\n\nBlank line above\n\tTab indented\n"
        add_entry("fact", body, "self-derived")
        files = list(memdir.glob("*.md"))
        content = files[0].read_text(encoding="utf-8")
        stored_body = content.split("---\n", 2)[2]
        assert stored_body == body

    def test_unknown_optional_field_preserved(self, memdir):
        """FR-8 forward-compat."""
        add_entry(
            "preference", "Some body", "user-correction",
            priority="high", review_by="2026-06-01",
        )
        files = list(memdir.glob("*.md"))
        fm = _read_fm(files[0])
        assert fm["priority"] == "high"
        assert fm["review_by"] == "2026-06-01"

    def test_kwargs_cannot_override_canonical_keys(self, memdir):
        """P5: id / created_at / last_used_at cannot be overridden via **kwargs.
        (type/source/valid_until/supersedes/evidence are named parameters; Python
        already refuses duplicate-arg via TypeError.)
        """
        with pytest.raises(ValueError, match="canonical frontmatter keys"):
            add_entry("fact", "body", "self-derived", id="FAKE_ULID")
        with pytest.raises(ValueError, match="canonical frontmatter keys"):
            add_entry("fact", "body", "self-derived", created_at="yesterday")
        with pytest.raises(ValueError, match="canonical frontmatter keys"):
            add_entry("fact", "body", "self-derived", last_used_at="never")

    def test_timestamps_are_iso8601_with_tz(self, memdir):
        add_entry("fact", "Timestamp test", "self-derived")
        files = list(memdir.glob("*.md"))
        fm = _read_fm(files[0])
        for field in ("created_at", "last_used_at"):
            dt = datetime.fromisoformat(str(fm[field]))
            assert dt.tzinfo is not None, f"{field} must have timezone: {fm[field]}"


class TestSecretScanner:
    """NFR-16."""

    def test_rejects_openai_key(self, memdir):
        with pytest.raises(ValueError, match=r"secret|api|credential"):
            add_entry("fact", "key is sk-1234567890abcdef1234567890abcdef12345678",
                      "self-derived")
        assert len(list(memdir.glob("*.md"))) == 0

    def test_rejects_anthropic_key(self, memdir):
        with pytest.raises(ValueError):
            add_entry("fact", "key=sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAA", "self-derived")
        assert len(list(memdir.glob("*.md"))) == 0

    def test_rejects_aws_key(self, memdir):
        with pytest.raises(ValueError):
            add_entry("fact", "AWS: AKIAIOSFODNN7EXAMPLE", "self-derived")

    def test_rejects_github_token(self, memdir):
        with pytest.raises(ValueError):
            add_entry("fact", "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAA", "self-derived")

    def test_rejects_slack_token(self, memdir):
        with pytest.raises(ValueError):
            add_entry("fact", "xoxb-1234567890-abcdef", "self-derived")

    def test_rejects_private_key_header(self, memdir):
        with pytest.raises(ValueError, match=r"Private key"):
            add_entry("fact", "-----BEGIN RSA PRIVATE KEY-----\n...",
                      "self-derived")

    def test_allows_normal_content(self, memdir):
        add_entry("procedure", "Run tests with pytest -xvs", "self-derived")
        assert len(list(memdir.glob("*.md"))) == 1

    def test_allows_documentation_mentions(self, memdir):
        """P15: regex tightening should not block benign 'password manager' mentions."""
        add_entry("preference",
                  "User preference: password manager is 1Password",
                  "user-correction")
        assert len(list(memdir.glob("*.md"))) == 1


class TestULIDMonotonicity:
    """P3."""

    def test_monotonic_within_ms(self):
        ulids = [_generate_ulid() for _ in range(50)]
        assert ulids == sorted(ulids), "ULIDs must be lexicographically monotonic"

    def test_ulid_format(self):
        u = _generate_ulid()
        assert _is_ulid(u)


# ═════════════════════════════════════════════════════════════════════════════
# Story 1.2: update / supersede / expire
# ═════════════════════════════════════════════════════════════════════════════

class TestSupersedeEntry:
    def test_marks_old_entry_as_superseded(self, memdir):
        old_id = add_entry("fact", "Old fact", "session:abc")
        new_id = add_entry("fact", "New fact", "session:def")
        supersede_entry(old_id, new_id)
        old_fm = _read_fm(memdir / f"{old_id}.md")
        new_fm = _read_fm(memdir / f"{new_id}.md")
        assert old_fm["type"] == "superseded"
        assert new_fm["supersedes"] == old_id

    def test_old_entry_not_deleted(self, memdir):
        old_id = add_entry("fact", "Old fact", "session:abc")
        new_id = add_entry("fact", "New fact", "session:def")
        supersede_entry(old_id, new_id)
        assert (memdir / f"{old_id}.md").exists()
        assert (memdir / f"{new_id}.md").exists()


class TestExpireEntry:
    def test_sets_valid_until_to_now(self, memdir):
        before = datetime.now(timezone.utc)
        entry_id = add_entry("fact", "Expiring fact", "self-derived")
        expire_entry(entry_id)
        after = datetime.now(timezone.utc)
        fm = _read_fm(memdir / f"{entry_id}.md")
        vu = datetime.fromisoformat(str(fm["valid_until"]))
        assert before <= vu <= after

    def test_entry_remains_on_disk(self, memdir):
        entry_id = add_entry("fact", "Will expire", "self-derived")
        expire_entry(entry_id)
        assert (memdir / f"{entry_id}.md").exists()


class TestUpdateEntry:
    def test_replaces_body(self, memdir):
        entry_id = add_entry("procedure", "Original body", "session:xyz")
        original_fm = _read_fm(memdir / f"{entry_id}.md")
        time.sleep(0.01)
        update_entry(entry_id, "Updated body")
        new_content = (memdir / f"{entry_id}.md").read_text(encoding="utf-8")
        parts = new_content.split("---\n", 2)
        new_fm = yaml.safe_load(parts[1])
        assert new_fm["id"] == entry_id
        assert new_fm["created_at"] == original_fm["created_at"]
        assert new_fm["source"] == original_fm["source"]
        assert new_fm["type"] == "procedure"
        assert parts[2].rstrip("\n") == "Updated body"

    def test_bumps_last_used_at(self, memdir):
        entry_id = add_entry("fact", "Will update", "self-derived")
        original = _read_fm(memdir / f"{entry_id}.md")
        time.sleep(0.02)
        update_entry(entry_id, "Updated")
        updated = _read_fm(memdir / f"{entry_id}.md")
        assert updated["last_used_at"] != original["last_used_at"]

    def test_update_rejects_secret(self, memdir):
        """P9: update_entry re-runs the secret scanner."""
        entry_id = add_entry("fact", "clean body", "self-derived")
        with pytest.raises(ValueError):
            update_entry(entry_id, "leaked sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAA")


# ═════════════════════════════════════════════════════════════════════════════
# Story 1.3 + 1.4: Reader (valid_until filter, last_used_at bump, legacy)
# ═════════════════════════════════════════════════════════════════════════════

class TestValidUntilFilter:
    def test_filters_past_entry(self, memdir):
        add_entry("fact", "Expired", "self-derived",
                  valid_until="2020-01-01T00:00:00+00:00")
        add_entry("fact", "Active", "self-derived")
        entries = read_entries()
        bodies = [e["body"].rstrip("\n") for e in entries]
        assert "Active" in bodies
        assert "Expired" not in bodies

    def test_includes_null_valid_until(self, memdir):
        add_entry("fact", "Permanent", "self-derived")
        assert len(read_entries()) == 1

    def test_includes_future_valid_until(self, memdir):
        add_entry("fact", "Future", "self-derived",
                  valid_until="2099-12-31T23:59:59+00:00")
        assert len(read_entries()) == 1

    def test_expired_entry_not_deleted(self, memdir):
        entry_id = add_entry("fact", "Expired", "self-derived",
                             valid_until="2020-01-01T00:00:00+00:00")
        read_entries()
        assert (memdir / f"{entry_id}.md").exists()

    def test_z_suffix_normalized(self, memdir):
        """P2: Z-suffix ISO strings must filter correctly (not fail-open)."""
        add_entry("fact", "Z-expired", "self-derived",
                  valid_until="2020-01-01T00:00:00Z")
        entries = read_entries()
        assert all("Z-expired" not in e["body"] for e in entries)

    def test_naive_iso_treated_as_utc(self, memdir):
        """P2: naive datetime strings are treated as UTC, not silently included."""
        add_entry("fact", "Naive-expired", "self-derived",
                  valid_until="2020-01-01T00:00:00")
        entries = read_entries()
        assert all("Naive-expired" not in e["body"] for e in entries)

    def test_malformed_valid_until_fails_closed(self, memdir):
        """P2: malformed valid_until → filtered, not silently included."""
        entry_id = add_entry("fact", "Bad-vu", "self-derived")
        # Corrupt the entry's valid_until on disk
        path = memdir / f"{entry_id}.md"
        content = path.read_text(encoding="utf-8")
        content = content.replace("valid_until: null", "valid_until: not-a-date")
        path.write_text(content, encoding="utf-8")
        entries = read_entries()
        assert all(e["id"] != entry_id for e in entries)


class TestLegacyEntries:
    """FR-6 + P13 legacy backfill."""

    def test_legacy_entry_no_frontmatter(self, memdir):
        legacy_file = memdir / "legacy.md"
        legacy_file.write_text("This is a legacy entry without frontmatter.\n",
                               encoding="utf-8")
        entries = read_entries()
        assert len(entries) == 1
        assert entries[0]["type"] == "unknown"
        assert "This is a legacy entry" in entries[0]["body"]

    def test_legacy_entry_supersede_backfills_frontmatter(self, memdir):
        """P13 / Story 1.5 AC #3: dream-pipeline touch backfills."""
        # Create a non-ULID legacy file with no frontmatter.
        # We use a stem that looks like a ULID for supersede to find it.
        legacy_id = _generate_ulid()
        legacy_file = memdir / f"{legacy_id}.md"
        legacy_file.write_text("Legacy body.\n", encoding="utf-8")
        new_id = add_entry("fact", "Replacement", "self-derived")
        supersede_entry(legacy_id, new_id)
        # Old entry is now type:superseded and has frontmatter
        fm = _read_fm(legacy_file)
        assert fm["type"] == "superseded"
        assert fm["id"] == legacy_id


class TestLastUsedAtBump:
    def test_bumps_on_read(self, memdir):
        entry_id = add_entry("fact", "Read me", "self-derived")
        original = _read_fm(memdir / f"{entry_id}.md")
        # Force the on-disk last_used_at to a stale value so the debounce permits a bump
        path = memdir / f"{entry_id}.md"
        content = path.read_text(encoding="utf-8")
        content = content.replace(
            f"last_used_at: {original['last_used_at']}",
            "last_used_at: 2020-01-01T00:00:00+00:00",
        )
        path.write_text(content, encoding="utf-8")
        read_entries()
        updated = _read_fm(path)
        assert updated["last_used_at"] != "2020-01-01T00:00:00+00:00"

    def test_debounce_within_60s(self, memdir):
        """Story 1.4 AC #2: second read within 60s does not rewrite."""
        entry_id = add_entry("fact", "Debounce me", "self-derived")
        read_entries()
        mtime1 = (memdir / f"{entry_id}.md").stat().st_mtime
        time.sleep(0.05)
        read_entries()
        mtime2 = (memdir / f"{entry_id}.md").stat().st_mtime
        assert mtime1 == mtime2, "Debounced bump should not rewrite the file"

    def test_debounce_survives_new_process(self, memdir):
        """P7: debounce uses on-disk last_used_at, not module-level state.
        Simulate a "new process" by clearing the in-process state.
        """
        import importlib
        import autodream.memory as hm
        entry_id = add_entry("fact", "Cross-process", "self-derived")
        read_entries()
        mtime1 = (memdir / f"{entry_id}.md").stat().st_mtime
        # Simulate restart: re-import the module
        importlib.reload(hm)
        time.sleep(0.05)
        hm.read_entries()
        mtime2 = (memdir / f"{entry_id}.md").stat().st_mtime
        assert mtime1 == mtime2, \
            "Debounce must use entry's own last_used_at, not in-process tracker"

    def test_read_only_skips_bump(self, memdir):
        """DN3: read_only=True must not mutate any file."""
        entry_id = add_entry("fact", "Read-only", "self-derived")
        # Force stale last_used_at
        path = memdir / f"{entry_id}.md"
        content = path.read_text(encoding="utf-8")
        content = content.replace(
            "last_used_at:",
            "last_used_at: 2020-01-01T00:00:00+00:00\n_dummy:",
        )
        # Quick & dirty: just rewrite with a stale ts
        fm = _read_fm(path)
        body = path.read_text(encoding="utf-8").split("---\n", 2)[2]
        fm["last_used_at"] = "2020-01-01T00:00:00+00:00"
        path.write_text(
            "---\n" + yaml.dump(fm, default_flow_style=False, sort_keys=False)
            + "---\n" + body,
            encoding="utf-8",
        )
        mtime_before = path.stat().st_mtime
        time.sleep(0.05)
        read_entries(read_only=True)
        mtime_after = path.stat().st_mtime
        assert mtime_before == mtime_after


class TestCRLFAndBOM:
    """P4: legacy-detection robustness."""

    def test_crlf_frontmatter_parsed(self, memdir):
        """A CRLF-delimited frontmatter file must be recognized as typed."""
        eid = _generate_ulid()
        crlf_content = (
            "---\r\n"
            f"id: {eid}\r\n"
            "type: preference\r\n"
            "valid_until: '2020-01-01T00:00:00+00:00'\r\n"
            "---\r\n"
            "Body line.\r\n"
        )
        (memdir / f"{eid}.md").write_text(crlf_content, encoding="utf-8")
        entries = read_entries()
        # The CRLF entry's valid_until is past → must be filtered
        assert all(e["id"] != eid for e in entries), \
            "CRLF frontmatter must be parsed (not treated as legacy)"

    def test_bom_prefix_tolerated(self, memdir):
        eid = _generate_ulid()
        bom_content = (
            "﻿---\n"
            f"id: {eid}\n"
            "type: preference\n"
            "valid_until: '2020-01-01T00:00:00+00:00'\n"
            "---\n"
            "BOM body.\n"
        )
        (memdir / f"{eid}.md").write_text(bom_content, encoding="utf-8")
        entries = read_entries()
        assert all(e["id"] != eid for e in entries), \
            "BOM-prefixed frontmatter must be parsed"


class TestNonULIDFilenames:
    """P11: non-ULID stems are read but not rewritten on bump."""

    def test_non_ulid_file_not_rewritten(self, memdir):
        path = memdir / "README.md"
        path.write_text("# Notes about this memory dir\nNot an entry.\n",
                        encoding="utf-8")
        mtime_before = path.stat().st_mtime
        time.sleep(0.05)
        read_entries()
        mtime_after = path.stat().st_mtime
        assert mtime_before == mtime_after, \
            "Non-ULID files must not be rewritten by read_entries"


# ═════════════════════════════════════════════════════════════════════════════
# Story 1.3 AC #3 / NFR-3 latency budget (P14)
# ═════════════════════════════════════════════════════════════════════════════

class TestLatencyBudget:
    def test_read_p95_under_5ms_per_entry(self, memdir):
        """100 entries × repeated reads: p95 per-entry should stay sub-5ms."""
        ids = [add_entry("fact", f"entry-{i}", "self-derived") for i in range(100)]
        # Prime: first read bumps everything; subsequent reads should be debounce-fast.
        read_entries()
        samples = []
        for _ in range(20):
            t0 = time.perf_counter()
            entries = read_entries()
            t1 = time.perf_counter()
            samples.append((t1 - t0) / max(len(entries), 1))
        samples.sort()
        p95 = samples[int(len(samples) * 0.95) - 1]
        assert p95 < 0.005, f"per-entry p95 was {p95*1000:.2f}ms (>5ms NFR-3)"


# ═════════════════════════════════════════════════════════════════════════════
# Atomic-write smoke test (P6)
# ═════════════════════════════════════════════════════════════════════════════

class TestAtomicWrite:
    def test_no_tmp_left_behind(self, memdir):
        add_entry("fact", "atomic", "self-derived")
        tmp_files = list(memdir.glob("*.tmp"))
        assert tmp_files == [], f"Stale .tmp files: {tmp_files}"


# ═════════════════════════════════════════════════════════════════════════════
# FR-3 surface coverage (P14): only sanctioned writer touches the memory dir
# ═════════════════════════════════════════════════════════════════════════════

class TestFR3WriterMonopoly:
    """Sanity check: after add_entry, the only files in memory_dir are .md (typed)."""

    def test_add_entry_creates_only_md_files(self, memdir):
        add_entry("fact", "x", "self-derived")
        add_entry("fact", "y", "self-derived")
        files = list(memdir.iterdir())
        assert all(f.suffix == ".md" for f in files), \
            f"Unexpected non-.md artifacts: {[f.name for f in files if f.suffix != '.md']}"


# ═══════════════════════════════════════════════════════════════════════════════
# Epic 2 / Story 2.1: Raw layer JSONL writer (transactional, FR-12)
# ═══════════════════════════════════════════════════════════════════════════════

import json


@pytest.fixture
def rawdir(tmp_path, monkeypatch):
    """Isolated raw-layer directory for Story 2.1 tests."""
    d = tmp_path / "raw"
    monkeypatch.setenv("HERMES_RAW_DIR", str(d))
    return d


class TestRawLayerAppend:
    """FR-12: add_entry appends one JSONL line to the raw layer."""

    def test_raw_layer_line_appended_on_add_entry(self, memdir, rawdir):
        """add_entry -> typed .md + one JSONL line in raw/<role>/<date>.jsonl.

        P20 / FR-9 schema pin: assert ALL expected fields are present with
        correct types and shape. Drift here breaks every downstream consumer.
        """
        entry_id = add_entry(
            "fact",
            "Test raw content",
            "session:abc123",
            evidence="session:abc123:sha:deadbeef",
            model="claude-sonnet-4-6",
        )

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        raw_files = list(rawdir.rglob(f"*{today}*.jsonl"))
        assert raw_files, f"No raw file found for {today} in {rawdir}"

        raw_file = raw_files[0]
        lines = raw_file.read_text().strip().split("\n")
        matching = [l for l in lines if entry_id in l]
        assert len(matching) >= 1, f"No raw line contains entry id {entry_id}"

        line = json.loads(matching[0])
        # FR-9 schema: {ts, entry_id, project, role, model, kind, content, evidence}
        expected_keys = {"ts", "entry_id", "project", "role", "model", "kind", "content", "evidence"}
        assert expected_keys.issubset(line.keys()), \
            f"Missing keys: {expected_keys - set(line.keys())}"
        # ts is ISO8601 with TZ
        ts_dt = datetime.fromisoformat(line["ts"])
        assert ts_dt.tzinfo is not None
        assert line["entry_id"] == entry_id
        assert line["kind"] == "fact"
        assert line["content"] == "Test raw content"
        assert line["evidence"] == "session:abc123:sha:deadbeef"  # DN3
        assert line["model"] == "claude-sonnet-4-6"               # P11
        assert "evidence_span" not in line, "evidence_span was renamed to evidence (DN3)"

    def test_raw_layer_file_permissions(self, memdir, rawdir):
        """Raw file created with 0o600, parent dirs 0o700."""
        add_entry("fact", "perm test", "self-derived")

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        raw_files = list(rawdir.rglob(f"*{today}*.jsonl"))
        assert raw_files

        raw_file = raw_files[0]
        stat = raw_file.stat()
        assert stat.st_mode & 0o777 == 0o600, \
            f"Expected 0o600, got {oct(stat.st_mode & 0o777)}"

        for parent in raw_file.parents:
            if parent == rawdir.parent:
                break
            parent_stat = parent.stat()
            assert parent_stat.st_mode & 0o777 == 0o700, \
                f"Parent {parent} expected 0o700, got {oct(parent_stat.st_mode & 0o777)}"


class TestRawLayerTransactional:
    """FR-12 (DN1 raw-first ordering):
       - raw-fails-first: typed write never happens; nothing on disk.
       - typed-fails-after: raw line is durable (immutable audit); no typed.
    """

    def test_raw_failure_prevents_typed_write(self, memdir, monkeypatch, tmp_path):
        """If raw append fails first, typed .md is never written."""
        bad_raw = tmp_path / "bad_raw"
        bad_raw.mkdir(mode=0o444)
        try:
            monkeypatch.setenv("HERMES_RAW_DIR", str(bad_raw))
            with pytest.raises((OSError, PermissionError)):
                add_entry("fact", "raw-first-failure", "self-derived")
            md_files = list(memdir.glob("*.md"))
            assert len(md_files) == 0, \
                f"Typed write should be skipped on raw failure; found {len(md_files)}"
        finally:
            # Restore writability for cleanup
            bad_raw.chmod(0o700)

    def test_typed_failure_keeps_raw_orphan(self, memdir, monkeypatch, rawdir, tmp_path):
        """If raw succeeds but typed write fails, raw line is durable.
        DN1 rationale: raw is the audit-of-record; orphan raw is information,
        orphan typed is corruption.
        """
        import autodream.memory as hm
        # Monkeypatch _atomic_write to raise after raw has already appended.
        real_write = hm._atomic_write
        def boom(*a, **kw):
            raise OSError("simulated typed-write failure")
        monkeypatch.setattr(hm, "_atomic_write", boom)
        with pytest.raises(OSError, match="simulated"):
            add_entry("fact", "orphan-raw-test", "self-derived")
        # No typed file
        assert list(memdir.glob("*.md")) == []
        # But a raw line exists
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        raw_files = list(rawdir.rglob(f"*{today}*.jsonl"))
        assert raw_files, "Raw line must persist even when typed write fails"
        line = json.loads(raw_files[0].read_text().strip().split("\n")[-1])
        assert line["content"] == "orphan-raw-test"


class TestMutationEventsAppendToRaw:
    """P6: update/supersede/expire must emit raw mutation events
    so the raw layer remains the source-of-truth log (Hard Invariant #6)."""

    def _read_raw_lines(self, rawdir):
        lines = []
        for f in rawdir.rglob("*.jsonl"):
            for ln in f.read_text(encoding="utf-8").split("\n"):
                if ln.strip():
                    lines.append(json.loads(ln))
        return lines

    def test_update_entry_appends_update_event(self, memdir, rawdir):
        eid = add_entry("fact", "v1", "self-derived")
        update_entry(eid, "v2")
        events = [l for l in self._read_raw_lines(rawdir) if l["entry_id"] == eid]
        kinds = [e["kind"] for e in events]
        assert "update" in kinds
        update_event = next(e for e in events if e["kind"] == "update")
        assert update_event["content"] == "v2"

    def test_supersede_entry_appends_supersede_event(self, memdir, rawdir):
        old_id = add_entry("fact", "old", "self-derived")
        new_id = add_entry("fact", "new", "self-derived")
        supersede_entry(old_id, new_id)
        events = [l for l in self._read_raw_lines(rawdir)
                  if l["entry_id"] == old_id and l["kind"] == "supersede"]
        assert len(events) == 1
        assert events[0]["content"] == new_id

    def test_expire_entry_appends_expire_event(self, memdir, rawdir):
        eid = add_entry("fact", "transient", "self-derived")
        expire_entry(eid)
        events = [l for l in self._read_raw_lines(rawdir)
                  if l["entry_id"] == eid and l["kind"] == "expire"]
        assert len(events) == 1


class TestRawLayerDefaultResolution:
    """DN2: raw_dir auto-resolves from HERMES_RAW_DIR -> HERMES_HOME/raw."""

    def test_raw_dir_defaults_to_hermes_home(self, memdir, monkeypatch, tmp_path):
        """If HERMES_RAW_DIR unset, raw layer goes to HERMES_HOME/raw."""
        monkeypatch.delenv("HERMES_RAW_DIR", raising=False)
        hermes_home = tmp_path / "hermes_home"
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))

        add_entry("fact", "raw default test", "self-derived")

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        expected_raw = hermes_home / "raw"
        raw_files = list(expected_raw.rglob(f"*{today}*.jsonl"))
        assert raw_files, f"No raw file in default location {expected_raw}"
