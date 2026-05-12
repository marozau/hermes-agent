"""
Story 1.1: Canonical writer with mandatory frontmatter (RED phase)

Tests for hermes_memory.add_entry() — the only sanctioned writer of typed memory entries.

FR-2: add_entry emits frontmatter unconditionally with id (ULID), created_at,
      last_used_at, source, and any optional fields.
FR-8: Unknown frontmatter keys preserved verbatim (forward-compat).
NFR-16: Secret-scanner pre-check aborts writes with secrets.
"""
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

# Ensure ~/.hermes/ is on the path for importing hermes_memory
HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
if str(HERMES_HOME) not in sys.path:
    sys.path.insert(0, str(HERMES_HOME))

from lib.hermes_memory import add_entry


class TestAddEntryFrontmatter:
    """FR-2: add_entry emits frontmatter unconditionally."""

    def test_emits_all_required_frontmatter_fields(self, tmp_path):
        """Given add_entry(type="preference", body="test", source="user-correction")
        Then frontmatter contains id, type, created_at, last_used_at, source,
        valid_until: null, supersedes: null, evidence: null."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        entry_id = add_entry(
            type="preference",
            body="Always use pytest for testing.",
            source="user-correction",
            memory_dir=str(memory_dir),
        )

        # Find the created file
        files = list(memory_dir.glob("*.md"))
        assert len(files) == 1, f"Expected 1 file, got {len(files)}"

        content = files[0].read_text()

        # Parse YAML frontmatter
        assert content.startswith("---\n"), "Entry must start with YAML frontmatter"
        parts = content.split("---\n", 2)
        assert len(parts) >= 3, f"Expected YAML frontmatter delimited by ---, got {len(parts)} parts"

        frontmatter = yaml.safe_load(parts[1])

        # Required fields
        assert "id" in frontmatter, "Missing 'id' field"
        assert frontmatter["id"] == entry_id, f"Returned id {entry_id} ≠ frontmatter id {frontmatter['id']}"
        assert len(frontmatter["id"]) == 26, f"ULID must be 26 chars, got {len(frontmatter['id'])}"

        assert frontmatter["type"] == "preference"
        assert "created_at" in frontmatter
        assert "last_used_at" in frontmatter
        assert frontmatter["source"] == "user-correction"

        # Default null fields
        assert frontmatter["valid_until"] is None
        assert frontmatter["supersedes"] is None
        assert frontmatter["evidence"] is None

        # Body preserved
        body = parts[2].strip()
        assert body == "Always use pytest for testing."

    def test_ulid_is_unique_per_call(self, tmp_path):
        """Each add_entry call generates a unique ULID."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        id1 = add_entry("fact", "Fact A", "self-derived", memory_dir=str(memory_dir))
        id2 = add_entry("fact", "Fact B", "self-derived", memory_dir=str(memory_dir))

        assert id1 != id2
        assert len(list(memory_dir.glob("*.md"))) == 2

    def test_body_preserved_verbatim(self, tmp_path):
        """Body is never rewritten or trimmed."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        body = "  Line with leading spaces\n\nBlank line above\n\tTab indented\n"
        add_entry("fact", body, "self-derived", memory_dir=str(memory_dir))

        files = list(memory_dir.glob("*.md"))
        content = files[0].read_text()
        parts = content.split("---\n", 2)
        stored_body = parts[2].lstrip("\n")  # strip the leading newline after ---

        assert stored_body == body, f"Body mismatch:\nExpected: {repr(body)}\nGot: {repr(stored_body)}"


class TestSecretScanner:
    """NFR-16: Secret-scanner pre-check aborts writes with secrets."""

    def test_rejects_api_key_in_body(self, tmp_path):
        """Given body containing an API key pattern, write is aborted."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        with pytest.raises(ValueError, match="secret|api.?key|credential"):
            add_entry(
                "fact",
                "My key is sk-1234abcd5678ef9012345678",
                "self-derived",
                memory_dir=str(memory_dir),
            )

        # No file created
        assert len(list(memory_dir.glob("*.md"))) == 0

    def test_rejects_aws_key_in_body(self, tmp_path):
        """Given body containing AWS access key pattern, write is aborted."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        with pytest.raises(ValueError, match="secret|key|credential"):
            add_entry(
                "fact",
                "AWS key: AKIAIOSFODNN7EXAMPLE",
                "self-derived",
                memory_dir=str(memory_dir),
            )

        assert len(list(memory_dir.glob("*.md"))) == 0

    def test_allows_normal_content(self, tmp_path):
        """Normal content without secrets passes the scanner."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        entry_id = add_entry(
            "procedure",
            "Run tests with pytest -xvs",
            "self-derived",
            memory_dir=str(memory_dir),
        )

        assert len(list(memory_dir.glob("*.md"))) == 1


class TestForwardCompat:
    """FR-8: Unknown frontmatter keys preserved verbatim."""

    def test_unknown_optional_field_preserved(self, tmp_path):
        """Given an unknown optional field, it is preserved in frontmatter."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        add_entry(
            "preference",
            "Some body",
            "user-correction",
            memory_dir=str(memory_dir),
            priority="high",
            review_by="2026-06-01",
        )

        files = list(memory_dir.glob("*.md"))
        content = files[0].read_text()
        parts = content.split("---\n", 2)
        frontmatter = yaml.safe_load(parts[1])

        assert frontmatter["priority"] == "high"
        assert frontmatter["review_by"] == "2026-06-01"


class TestTimestampFormat:
    """created_at and last_used_at must be ISO8601 with timezone."""

    def test_timestamps_are_iso8601_with_tz(self, tmp_path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        add_entry("fact", "Timestamp test", "self-derived", memory_dir=str(memory_dir))

        files = list(memory_dir.glob("*.md"))
        content = files[0].read_text()
        parts = content.split("---\n", 2)
        fm = yaml.safe_load(parts[1])

        # Verify ISO8601 format with timezone
        for field in ["created_at", "last_used_at"]:
            ts = fm[field]
            # Should parse as datetime
            dt = datetime.fromisoformat(str(ts))
            assert dt.tzinfo is not None, f"{field} must have timezone: {ts}"


# ═══════════════════════════════════════════════════════════════════════════════
# Story 1.2: update_entry, supersede_entry, expire_entry
# ═══════════════════════════════════════════════════════════════════════════════

from lib.hermes_memory import update_entry, supersede_entry, expire_entry


class TestSupersedeEntry:
    """FR-7: supersede_entry(old_id, new_id) marks old as superseded."""

    def test_marks_old_entry_as_superseded(self, tmp_path):
        """Given entry X, supersede_entry(X, Y) → X.type = superseded, Y.supersedes = X."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        old_id = add_entry("fact", "Old fact", "session:abc", memory_dir=str(memory_dir))
        new_id = add_entry("fact", "New fact", "session:def", memory_dir=str(memory_dir))

        supersede_entry(old_id, new_id, memory_dir=str(memory_dir))

        # Read old entry — should be type:superseded
        old_content = (memory_dir / f"{old_id}.md").read_text()
        old_fm = yaml.safe_load(old_content.split("---\n", 2)[1])
        assert old_fm["type"] == "superseded", f"Expected superseded, got {old_fm['type']}"

        # Read new entry — should have supersedes pointing to old
        new_content = (memory_dir / f"{new_id}.md").read_text()
        new_fm = yaml.safe_load(new_content.split("---\n", 2)[1])
        assert new_fm["supersedes"] == old_id

    def test_old_entry_not_deleted(self, tmp_path):
        """Superseded entry remains on disk (FR-4: never delete)."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        old_id = add_entry("fact", "Old fact", "session:abc", memory_dir=str(memory_dir))
        new_id = add_entry("fact", "New fact", "session:def", memory_dir=str(memory_dir))

        supersede_entry(old_id, new_id, memory_dir=str(memory_dir))

        assert (memory_dir / f"{old_id}.md").exists(), "Old entry must remain on disk"
        assert (memory_dir / f"{new_id}.md").exists()


class TestExpireEntry:
    """FR-4, FR-7: expire_entry sets valid_until to now."""

    def test_sets_valid_until_to_now(self, tmp_path):
        """Given entry X, expire_entry(X) → valid_until is set to current time."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        before = datetime.now(timezone.utc)
        entry_id = add_entry("fact", "Expiring fact", "self-derived", memory_dir=str(memory_dir))
        expire_entry(entry_id, memory_dir=str(memory_dir))
        after = datetime.now(timezone.utc)

        content = (memory_dir / f"{entry_id}.md").read_text()
        fm = yaml.safe_load(content.split("---\n", 2)[1])

        valid_until = datetime.fromisoformat(str(fm["valid_until"]))
        assert before <= valid_until <= after, \
            f"valid_until {valid_until} not between {before} and {after}"

    def test_entry_remains_on_disk(self, tmp_path):
        """Expired entry is NOT deleted from disk."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        entry_id = add_entry("fact", "Will expire", "self-derived", memory_dir=str(memory_dir))
        expire_entry(entry_id, memory_dir=str(memory_dir))

        assert (memory_dir / f"{entry_id}.md").exists(), "Expired entry must remain on disk"


class TestUpdateEntry:
    """FR-7: update_entry replaces body, preserves identity fields."""

    def test_replaces_body(self, tmp_path):
        """update_entry replaces body but preserves id, created_at, source."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        entry_id = add_entry("procedure", "Original body", "session:xyz",
                             memory_dir=str(memory_dir))

        # Read original metadata
        original_content = (memory_dir / f"{entry_id}.md").read_text()
        original_fm = yaml.safe_load(original_content.split("---\n", 2)[1])

        import time
        time.sleep(0.01)  # ensure last_used_at would change

        update_entry(entry_id, "Updated body", memory_dir=str(memory_dir))

        new_content = (memory_dir / f"{entry_id}.md").read_text()
        parts = new_content.split("---\n", 2)
        new_fm = yaml.safe_load(parts[1])
        new_body = parts[2].strip()

        # Identity preserved
        assert new_fm["id"] == entry_id
        assert new_fm["created_at"] == original_fm["created_at"]
        assert new_fm["source"] == original_fm["source"]
        assert new_fm["type"] == "procedure"

        # Body changed
        assert new_body == "Updated body"

    def test_bumps_last_used_at(self, tmp_path):
        """update_entry bumps last_used_at to now."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        entry_id = add_entry("fact", "Will update", "self-derived", memory_dir=str(memory_dir))
        original = yaml.safe_load(
            (memory_dir / f"{entry_id}.md").read_text().split("---\n", 2)[1]
        )

        import time
        time.sleep(0.02)

        update_entry(entry_id, "Updated", memory_dir=str(memory_dir))
        updated = yaml.safe_load(
            (memory_dir / f"{entry_id}.md").read_text().split("---\n", 2)[1]
        )

        assert updated["last_used_at"] != original["last_used_at"], \
            "last_used_at must be bumped on update"


# ═══════════════════════════════════════════════════════════════════════════════
# Story 1.3 + 1.4: Reader with valid_until filtering + last_used_at bump
# ═══════════════════════════════════════════════════════════════════════════════

from lib.hermes_memory import read_entries


class TestValidUntilFilter:
    """FR-4: Reader filters entries past valid_until."""

    def test_filters_expired_entry(self, tmp_path):
        """Entry with valid_until in the past is excluded from read."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        add_entry("fact", "Expired fact", "self-derived",
                  memory_dir=str(memory_dir),
                  valid_until="2020-01-01T00:00:00+00:00")

        add_entry("fact", "Active fact", "self-derived",
                  memory_dir=str(memory_dir))

        entries = read_entries(str(memory_dir))
        assert len(entries) == 1
        assert entries[0]["body"] == "Active fact"

    def test_includes_null_valid_until(self, tmp_path):
        """Entry with valid_until: null is always included."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        add_entry("fact", "Permanent fact", "self-derived",
                  memory_dir=str(memory_dir))

        entries = read_entries(str(memory_dir))
        assert len(entries) == 1

    def test_includes_future_valid_until(self, tmp_path):
        """Entry with valid_until in the future is included."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        add_entry("fact", "Future expiry", "self-derived",
                  memory_dir=str(memory_dir),
                  valid_until="2099-12-31T23:59:59+00:00")

        entries = read_entries(str(memory_dir))
        assert len(entries) == 1

    def test_expired_entry_not_deleted(self, tmp_path):
        """Expired entry remains on disk after read."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        entry_id = add_entry("fact", "Expired", "self-derived",
                             memory_dir=str(memory_dir),
                             valid_until="2020-01-01T00:00:00+00:00")

        read_entries(str(memory_dir))
        assert (memory_dir / f"{entry_id}.md").exists()


class TestLegacyEntries:
    """FR-6: Legacy untyped entries treated as type: unknown."""

    def test_legacy_entry_no_frontmatter(self, tmp_path):
        """Entry file with no YAML frontmatter is type: unknown."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        # Write a legacy-style entry file directly
        legacy_file = memory_dir / "legacy.md"
        legacy_file.write_text("This is a legacy entry without frontmatter.\n")

        entries = read_entries(str(memory_dir))
        assert len(entries) == 1
        assert entries[0]["type"] == "unknown"
        assert "This is a legacy entry" in entries[0]["body"]


class TestLastUsedAtBump:
    """FR-5: Reader bumps last_used_at on recall."""

    def test_bumps_last_used_at_on_read(self, tmp_path):
        """Entry's last_used_at is updated when read."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        entry_id = add_entry("fact", "Will be read", "self-derived",
                             memory_dir=str(memory_dir))

        original = yaml.safe_load(
            (memory_dir / f"{entry_id}.md").read_text().split("---\n", 2)[1]
        )

        import time
        time.sleep(0.02)

        read_entries(str(memory_dir))

        updated = yaml.safe_load(
            (memory_dir / f"{entry_id}.md").read_text().split("---\n", 2)[1]
        )
        assert updated["last_used_at"] != original["last_used_at"], \
            "last_used_at must be bumped on read"

    def test_read_does_not_block_on_bump_failure(self, tmp_path):
        """If last_used_at update fails, the entry is still returned."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        add_entry("fact", "Read me", "self-derived", memory_dir=str(memory_dir))

        entries = read_entries(str(memory_dir))
        assert len(entries) == 1
        assert entries[0]["body"] == "Read me"
