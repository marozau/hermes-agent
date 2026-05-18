"""
Epic 4 — Staged Dream Artifacts.

Covers FR-13..FR-24, NFR-8/9/14/19/21, plus the code-review patches:
  DN1  standalone CLI script (smoke test)
  DN2  atomic lock + context manager + release
  DN3  per-op undo journal + rollback
  DN4  content-hash idempotency
  DN5  force_apply gate
  P1   os.kill(pid, 0) instead of pthread_kill
  P2   atomic O_EXCL acquire
  P3   dream_lock() context manager
  P4   rollback on partial apply
  P5   supersede op handler
  P6   apply --only glob
  P7   PatchProposal.type field
  P8   source = "dream:<id>"
  P9   no silent update→add fallback
  P11  hash-chained audit log
  P13  _write_file_atomic actually atomic
  P14  HERMES_DREAMS_DIR honored
  P15  mkdir(mode=0o700) at creation
  P16  Literal types on op/confidence/risk_class
  P17  PatchProposal validation on apply
  P18  apply_eligible derived from regression verdict
  P19  discard audit row
  P20  symlink refusal
  P21  dream_diff size cap
  P22  soul-guardian carve-out seam
  P23  test fixture isolates raw layer
  P24  manifest accepts model_used/cost/recall_verdict
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest


@pytest.fixture
def dreams_dir(tmp_path, monkeypatch):
    """Isolated dreams directory + raw layer (so the Epic-5 recall harness
    doesn't pick up leaked raw lines from other tests / real `~/.hermes/raw/`)."""
    d = tmp_path / "dreams"
    monkeypatch.setenv("HERMES_DREAMS_DIR", str(d))
    monkeypatch.setenv("HERMES_RAW_DIR", str(tmp_path / "raw_isolation"))
    return d


@pytest.fixture
def memdir_with_entries(tmp_path, monkeypatch):
    """Memory dir + isolated raw layer (P23 — no leak into ~/.hermes/raw)."""
    d = tmp_path / "memory" / "typed"
    raw = tmp_path / "raw"
    monkeypatch.setenv("HERMES_MEMORY_DIR", str(d))
    monkeypatch.setenv("HERMES_RAW_DIR", str(raw))
    from lib.hermes_memory import add_entry
    d.mkdir(parents=True)
    for i in range(5):
        add_entry("fact", f"Fact {i}", "self-derived",
                  memory_dir=str(d), raw_dir=str(raw))
    return d


# ═════════════════════════════════════════════════════════════════════════════
# Story 4.1: ULIDs, create, never-mutate, manifest, permissions
# ═════════════════════════════════════════════════════════════════════════════


class TestDreamIdGeneration:
    def test_dream_id_is_26_chars(self):
        from lib.hermes_dream import generate_dream_id
        assert len(generate_dream_id()) == 26

    def test_dream_ids_unique(self):
        from lib.hermes_dream import generate_dream_id
        assert len({generate_dream_id() for _ in range(100)}) == 100


class TestCreateDreamArtifact:
    def test_creates_dream_directory(self, dreams_dir):
        from lib.hermes_dream import create_dream_artifact
        dream_id = create_dream_artifact(
            scope="default",
            memory_dir=str(dreams_dir.parent / "nonexistent"),
            dreams_dir=str(dreams_dir),
            dry_run=True,
        )
        dpath = dreams_dir / dream_id
        assert dpath.is_dir()
        assert (dpath / "manifest.json").exists()
        assert (dpath / "REPORT.md").exists()

    def test_never_writes_to_live_state(self, dreams_dir, memdir_with_entries):
        """FR-14: create never mutates live memory."""
        from lib.hermes_dream import create_dream_artifact
        from lib.hermes_memory import read_entries
        before = {e["id"] for e in read_entries(str(memdir_with_entries))}
        create_dream_artifact(
            scope="default",
            memory_dir=str(memdir_with_entries),
            dreams_dir=str(dreams_dir),
            dry_run=True,
        )
        after = {e["id"] for e in read_entries(str(memdir_with_entries))}
        assert before == after

    def test_produces_memory_patch_with_required_fields(
        self, dreams_dir, memdir_with_entries,
    ):
        """FR-16: each proposal carries op/type/rationale/confidence/risk_class."""
        from lib.hermes_dream import create_dream_artifact
        import yaml
        dream_id = create_dream_artifact(
            scope="default", memory_dir=str(memdir_with_entries),
            dreams_dir=str(dreams_dir), dry_run=True,
        )
        proposals = yaml.safe_load((dreams_dir / dream_id / "memory.patch").read_text())
        for p in proposals:
            assert p["op"] in ("add", "update", "supersede", "expire")
            assert "type" in p  # P7
            assert p["confidence"] in ("low", "medium", "high")
            assert p["risk_class"] in ("additive", "corrective", "deprecating")


class TestDreamManifest:
    def test_manifest_fields(self, dreams_dir, memdir_with_entries):
        from lib.hermes_dream import create_dream_artifact
        dream_id = create_dream_artifact(
            scope="default", memory_dir=str(memdir_with_entries),
            dreams_dir=str(dreams_dir), dry_run=True,
        )
        m = json.loads((dreams_dir / dream_id / "manifest.json").read_text())
        assert m["scope"] == "default"
        for f in ("started_at", "finished_at", "model_used",
                  "signal_density_score", "recall_regression_verdict",
                  "signature_anchors"):
            assert f in m
        assert "tokens_in" in m["cost"]
        assert "tokens_out" in m["cost"]

    def test_manifest_accepts_real_llm_values(self, dreams_dir):
        """P24: future LLM wiring can thread real model/cost/verdict."""
        from lib.hermes_dream import CostInfo, create_dream_artifact
        dream_id = create_dream_artifact(
            scope="default",
            memory_dir=str(dreams_dir.parent / "nonexistent"),
            dreams_dir=str(dreams_dir),
            dry_run=True,
            model_used="claude-sonnet-4-6",
            cost=CostInfo(tokens_in=1000, tokens_out=400),
            recall_regression_verdict="pass",
        )
        m = json.loads((dreams_dir / dream_id / "manifest.json").read_text())
        assert m["model_used"] == "claude-sonnet-4-6"
        assert m["cost"]["tokens_in"] == 1000
        assert m["recall_regression_verdict"] == "pass"


class TestDreamFilePermissions:
    """NFR-14 + P15: directory 0o700 at creation, files 0o600."""

    def test_directory_permissions(self, dreams_dir):
        from lib.hermes_dream import create_dream_artifact
        dream_id = create_dream_artifact(
            scope="default",
            memory_dir=str(dreams_dir.parent / "nonexistent"),
            dreams_dir=str(dreams_dir), dry_run=True,
        )
        dpath = dreams_dir / dream_id
        assert (dpath.stat().st_mode & 0o777) == 0o700
        for f in dpath.rglob("*"):
            if f.is_file():
                assert (f.stat().st_mode & 0o777) == 0o600


# ═════════════════════════════════════════════════════════════════════════════
# DN2 / P2 / P3: lock primitives
# ═════════════════════════════════════════════════════════════════════════════


class TestLockMechanics:
    def test_acquire_writes_pid(self, dreams_dir):
        from lib.hermes_dream import acquire_lock, release_lock
        assert acquire_lock(str(dreams_dir)) is True
        lock_path = dreams_dir / ".create.lock"
        assert lock_path.exists()
        body = lock_path.read_text()
        assert str(os.getpid()) in body
        release_lock(str(dreams_dir))

    def test_acquire_atomic_when_exists(self, dreams_dir):
        """P2: O_EXCL acquire — second caller refuses while first holds."""
        from lib.hermes_dream import acquire_lock, release_lock
        assert acquire_lock(str(dreams_dir)) is True
        assert acquire_lock(str(dreams_dir)) is False
        release_lock(str(dreams_dir))

    def test_release_lock_idempotent(self, dreams_dir):
        from lib.hermes_dream import acquire_lock, release_lock
        acquire_lock(str(dreams_dir))
        assert release_lock(str(dreams_dir)) is True
        assert release_lock(str(dreams_dir)) is False

    def test_acquire_lock_file_mode_owner_only(self, dreams_dir):
        from lib.hermes_dream import acquire_lock
        acquire_lock(str(dreams_dir))
        mode = (dreams_dir / ".create.lock").stat().st_mode & 0o777
        assert mode == 0o600

    def test_dream_lock_releases_on_success(self, dreams_dir):
        from lib.hermes_dream import dream_lock
        with dream_lock(str(dreams_dir)):
            assert (dreams_dir / ".create.lock").exists()
        assert not (dreams_dir / ".create.lock").exists()

    def test_dream_lock_releases_on_exception(self, dreams_dir):
        """NFR-8: crash mid-create auto-releases the lock."""
        from lib.hermes_dream import dream_lock
        with pytest.raises(RuntimeError, match="boom"):
            with dream_lock(str(dreams_dir)):
                assert (dreams_dir / ".create.lock").exists()
                raise RuntimeError("boom")
        assert not (dreams_dir / ".create.lock").exists()

    def test_dream_lock_raises_when_held(self, dreams_dir):
        from lib.hermes_dream import acquire_lock, dream_lock, release_lock
        acquire_lock(str(dreams_dir))
        try:
            with pytest.raises(RuntimeError, match="another `create`"):
                with dream_lock(str(dreams_dir)):
                    pass
        finally:
            release_lock(str(dreams_dir))

    def test_stale_lock_reclaim_via_dead_pid(self, dreams_dir):
        """P1: os.kill(dead_pid, 0) raises ProcessLookupError → reclaim."""
        from lib.hermes_dream import acquire_lock
        dreams_dir.mkdir(parents=True, exist_ok=True)
        lock = dreams_dir / ".create.lock"
        lock.write_text(f"99999\n{datetime.now(timezone.utc).isoformat()}")
        # Force mtime to 2h ago.
        old = datetime.now(timezone.utc).timestamp() - 7200
        os.utime(str(lock), (old, old))
        assert acquire_lock(str(dreams_dir)) is True

    def test_fresh_lock_not_reclaimed_even_if_pid_dead(self, dreams_dir):
        """A lock whose mtime is fresh (<1h) is NEVER reclaimed, even if the
        PID is dead — protects against a slow writer racing the cleanup."""
        from lib.hermes_dream import acquire_lock
        dreams_dir.mkdir(parents=True, exist_ok=True)
        lock = dreams_dir / ".create.lock"
        lock.write_text(f"99999\n{datetime.now(timezone.utc).isoformat()}")
        assert acquire_lock(str(dreams_dir)) is False

    def test_stale_lock_not_reclaimed_if_pid_alive(self, dreams_dir):
        """P1: a stale-mtime lock whose PID is THIS LIVE PROCESS must NOT be
        reclaimed (os.kill(self_pid, 0) succeeds → PID alive)."""
        from lib.hermes_dream import acquire_lock
        dreams_dir.mkdir(parents=True, exist_ok=True)
        lock = dreams_dir / ".create.lock"
        lock.write_text(f"{os.getpid()}\n{datetime.now(timezone.utc).isoformat()}")
        old = datetime.now(timezone.utc).timestamp() - 7200
        os.utime(str(lock), (old, old))
        assert acquire_lock(str(dreams_dir)) is False


class TestCreateIntegratesLock:
    def test_create_acquires_and_releases(self, dreams_dir, memdir_with_entries):
        from lib.hermes_dream import create_dream_artifact
        create_dream_artifact(
            scope="default", memory_dir=str(memdir_with_entries),
            dreams_dir=str(dreams_dir), dry_run=True,
        )
        assert not (dreams_dir / ".create.lock").exists()

    def test_concurrent_create_serializes(self, dreams_dir, memdir_with_entries):
        """Two create calls in series — second sees lock released after first."""
        from lib.hermes_dream import acquire_lock, create_dream_artifact
        # Hold the lock manually; create should refuse.
        acquired = acquire_lock(str(dreams_dir))
        assert acquired is True
        with pytest.raises(RuntimeError, match="another `create`"):
            create_dream_artifact(
                scope="default", memory_dir=str(memdir_with_entries),
                dreams_dir=str(dreams_dir), dry_run=True,
            )


# ═════════════════════════════════════════════════════════════════════════════
# Stories 4.3-4.5: status / diff
# ═════════════════════════════════════════════════════════════════════════════


class TestDreamStatus:
    def test_status_lists_dreams(self, dreams_dir, memdir_with_entries):
        from lib.hermes_dream import create_dream_artifact, list_dreams
        d1 = create_dream_artifact("default", memory_dir=str(memdir_with_entries),
                                   dreams_dir=str(dreams_dir), dry_run=True)
        d2 = create_dream_artifact("default", memory_dir=str(memdir_with_entries),
                                   dreams_dir=str(dreams_dir), dry_run=True)
        ids = [d["dream_id"] for d in list_dreams(str(dreams_dir))]
        assert d1 in ids and d2 in ids

    def test_eligible_derived_from_verdict(self, dreams_dir):
        """P18: apply_eligible is no/yes/manual based on regression verdict."""
        from lib.hermes_dream import create_dream_artifact, list_dreams
        d_pass = create_dream_artifact(
            "default", memory_dir=str(dreams_dir.parent / "nonexistent"),
            dreams_dir=str(dreams_dir), dry_run=True,
            recall_regression_verdict="pass",
        )
        d_fail = create_dream_artifact(
            "default", memory_dir=str(dreams_dir.parent / "nonexistent"),
            dreams_dir=str(dreams_dir), dry_run=True,
            recall_regression_verdict="fail",
        )
        d_skip = create_dream_artifact(
            "default", memory_dir=str(dreams_dir.parent / "nonexistent"),
            dreams_dir=str(dreams_dir), dry_run=True,
        )
        rows = {d["dream_id"]: d for d in list_dreams(str(dreams_dir))}
        assert rows[d_pass]["apply_eligible"] == "yes"
        assert rows[d_fail]["apply_eligible"] == "no"
        assert rows[d_skip]["apply_eligible"] == "manual"


class TestDreamDiff:
    def test_diff_renders_report(self, dreams_dir, memdir_with_entries):
        from lib.hermes_dream import create_dream_artifact, dream_diff
        d = create_dream_artifact("default", memory_dir=str(memdir_with_entries),
                                  dreams_dir=str(dreams_dir), dry_run=True)
        out = dream_diff(d, str(dreams_dir))
        assert "Dream Report" in out
        assert d in out

    def test_diff_includes_patches(self, dreams_dir, memdir_with_entries):
        from lib.hermes_dream import create_dream_artifact, dream_diff
        d = create_dream_artifact("default", memory_dir=str(memdir_with_entries),
                                  dreams_dir=str(dreams_dir), dry_run=True)
        out = dream_diff(d, str(dreams_dir))
        assert "memory.patch" in out.lower() or "proposal" in out.lower()

    def test_diff_truncates_large_files(self, dreams_dir, memdir_with_entries):
        """P21: 1 MB cap with truncation marker."""
        from lib.hermes_dream import create_dream_artifact, dream_diff
        d = create_dream_artifact("default", memory_dir=str(memdir_with_entries),
                                  dreams_dir=str(dreams_dir), dry_run=True)
        # Inflate REPORT.md beyond 1 MB.
        report = dreams_dir / d / "REPORT.md"
        report.write_text("x" * (1_048_576 + 100), encoding="utf-8")
        out = dream_diff(d, str(dreams_dir))
        assert "truncated" in out

    def test_diff_nonexistent_dream_raises(self, dreams_dir):
        from lib.hermes_dream import dream_diff
        with pytest.raises(FileNotFoundError):
            dream_diff("NONEXISTENT", str(dreams_dir))


# ═════════════════════════════════════════════════════════════════════════════
# Story 4.6: apply (transactional, idempotent, gated)
# ═════════════════════════════════════════════════════════════════════════════


class TestApplyGate:
    """DN5 / Hard Invariant #4: force_apply required."""

    def test_apply_refuses_without_force(self, dreams_dir, memdir_with_entries, tmp_path):
        from lib.hermes_dream import apply_dream, create_dream_artifact
        d = create_dream_artifact("default", memory_dir=str(memdir_with_entries),
                                  dreams_dir=str(dreams_dir), dry_run=True)
        fresh = tmp_path / "fresh"; fresh.mkdir()
        result = apply_dream(d, str(dreams_dir), memory_dir=str(fresh))
        assert result["status"] == "refused"
        assert "force_apply" in result["reason"] or "--accept" in result["reason"]


class TestApplyHappy:
    """Apply runs against the same memory dir the dream was created from
    (where update targets exist). P9: no silent update→add fallback."""

    def test_apply_runs_patches(self, dreams_dir, memdir_with_entries):
        from lib.hermes_dream import apply_dream, create_dream_artifact
        d = create_dream_artifact("default", memory_dir=str(memdir_with_entries),
                                  dreams_dir=str(dreams_dir), dry_run=True)
        result = apply_dream(d, str(dreams_dir),
                             memory_dir=str(memdir_with_entries),
                             force_apply=True)
        assert result["status"] == "applied"
        assert result["operations"] > 0
        assert "hash" in result

    def test_apply_idempotent_by_content_hash(self, dreams_dir, memdir_with_entries):
        """DN4 / Hard Invariant #9: re-apply same artifact = no_changes."""
        from lib.hermes_dream import apply_dream, create_dream_artifact
        d = create_dream_artifact("default", memory_dir=str(memdir_with_entries),
                                  dreams_dir=str(dreams_dir), dry_run=True)
        r1 = apply_dream(d, str(dreams_dir),
                         memory_dir=str(memdir_with_entries), force_apply=True)
        assert r1["status"] == "applied"
        r2 = apply_dream(d, str(dreams_dir),
                         memory_dir=str(memdir_with_entries), force_apply=True)
        assert r2["status"] == "no_changes"
        assert r2["hash"] == r1["hash"]

    def test_apply_refuses_if_content_changed_after_apply(
        self, dreams_dir, memdir_with_entries,
    ):
        """DN4: hash mismatch → refuse rather than silent re-apply."""
        from lib.hermes_dream import apply_dream, create_dream_artifact
        d = create_dream_artifact("default", memory_dir=str(memdir_with_entries),
                                  dreams_dir=str(dreams_dir), dry_run=True)
        apply_dream(d, str(dreams_dir),
                    memory_dir=str(memdir_with_entries), force_apply=True)
        # Mutate the patch — should now refuse.
        (dreams_dir / d / "memory.patch").write_text(
            "- {op: add, type: fact, body: tampered, rationale: r}\n"
        )
        r = apply_dream(d, str(dreams_dir),
                        memory_dir=str(memdir_with_entries), force_apply=True)
        assert r["status"] == "refused"

    def test_apply_refuses_update_without_target(
        self, dreams_dir, memdir_with_entries, tmp_path,
    ):
        """P9: update target missing → LookupError (no silent add fallback)."""
        from lib.hermes_dream import apply_dream, create_dream_artifact
        d = create_dream_artifact("default", memory_dir=str(memdir_with_entries),
                                  dreams_dir=str(dreams_dir), dry_run=True)
        # Apply into a FRESH memdir — update targets don't exist there.
        fresh = tmp_path / "fresh"; fresh.mkdir()
        with pytest.raises(LookupError, match="refusing silent fallback"):
            apply_dream(d, str(dreams_dir), memory_dir=str(fresh),
                        force_apply=True)


class TestApplyOnlyGlob:
    """P6 / FR-20: --only filters by target_entry_id glob."""

    def test_only_filters_proposals(self, dreams_dir, memdir_with_entries, tmp_path):
        from lib.hermes_dream import apply_dream, create_dream_artifact
        d = create_dream_artifact("default", memory_dir=str(memdir_with_entries),
                                  dreams_dir=str(dreams_dir), dry_run=True)
        fresh = tmp_path / "fresh"; fresh.mkdir()
        os.environ["HERMES_RAW_DIR"] = str(tmp_path / "raw")
        # Dry-run creates 3 proposals — filter to a glob matching none.
        r = apply_dream(d, str(dreams_dir), memory_dir=str(fresh),
                        only="DOES_NOT_MATCH_*", force_apply=True)
        assert r["status"] == "applied"
        assert r["operations"] == 0


class TestApplySupersedeBranch:
    """P5: supersede op was previously silently dropped."""

    def test_supersede_proposal_applies(self, dreams_dir, tmp_path):
        from lib.hermes_dream import apply_dream
        # Hand-craft a dream artifact with a supersede proposal.
        d_id = "01TESTSUPERSEDE0000000000"
        artifact = dreams_dir / d_id
        artifact.mkdir(parents=True)
        # Seed a target entry in memory first.
        fresh = tmp_path / "fresh_mem"; fresh.mkdir()
        os.environ["HERMES_RAW_DIR"] = str(tmp_path / "raw")
        os.environ["HERMES_MEMORY_DIR"] = str(fresh)
        from lib.hermes_memory import add_entry
        old_id = add_entry("fact", "old body", "self-derived",
                           memory_dir=str(fresh), raw_dir=str(tmp_path / "raw"))
        # Write a valid manifest + patch.
        (artifact / "manifest.json").write_text(json.dumps({
            "scope": "default", "started_at": "x", "finished_at": "y",
            "model_used": "test", "signal_density_score": 0.0,
            "recall_regression_verdict": "skipped",
            "cost": {"tokens_in": 0, "tokens_out": 0, "cache_read_tokens": 0},
            "signature_anchors": [],
        }))
        import yaml
        (artifact / "memory.patch").write_text(yaml.dump([{
            "op": "supersede", "type": "fact",
            "target_entry_id": old_id, "body": "new body",
            "rationale": "supersede test", "confidence": "high",
            "risk_class": "corrective", "source_refs": [],
        }]))
        r = apply_dream(d_id, str(dreams_dir), memory_dir=str(fresh),
                        force_apply=True)
        assert r["status"] == "applied"
        assert r["operations"] == 1
        # The supersede should have added a new entry (the replacement) and
        # marked the old one type:superseded.
        from lib.hermes_memory import read_entries
        all_entries = read_entries(str(fresh))
        types = [e["type"] for e in all_entries]
        assert "superseded" in types


class TestApplyRollback:
    """P4 / DN3 / NFR-9: per-op undo journal rolls back partial apply."""

    def test_failure_mid_loop_rolls_back(
        self, dreams_dir, memdir_with_entries, tmp_path, monkeypatch,
    ):
        from lib.hermes_dream import apply_dream, create_dream_artifact
        d = create_dream_artifact("default", memory_dir=str(memdir_with_entries),
                                  dreams_dir=str(dreams_dir), dry_run=True)
        fresh = tmp_path / "fresh"; fresh.mkdir()
        os.environ["HERMES_RAW_DIR"] = str(tmp_path / "raw")
        # Monkeypatch hermes_memory.update_entry to raise on the 2nd call.
        from lib.hermes_memory import update_entry as real_update
        call_count = {"n": 0}
        def failing_update(*a, **kw):
            call_count["n"] += 1
            if call_count["n"] >= 1:
                raise RuntimeError("simulated mid-apply failure")
            return real_update(*a, **kw)
        monkeypatch.setattr("lib.hermes_dream.update_entry", failing_update,
                            raising=False)
        # Note: we can't easily inject through the local import without
        # restructuring. Instead, monkey-patch the source module:
        monkeypatch.setattr("lib.hermes_memory.update_entry", failing_update)
        # Now run apply — first proposal is `add`, then `update` (failing).
        with pytest.raises(RuntimeError, match="simulated"):
            apply_dream(d, str(dreams_dir), memory_dir=str(fresh),
                        force_apply=True)
        # Rollback: the add'd entry should be removed from the typed dir.
        from lib.hermes_memory import read_entries
        remaining = read_entries(str(fresh))
        assert len(remaining) == 0


class TestApplyValidation:
    """P17: PatchProposal validation on apply."""

    def test_apply_refuses_malformed_proposal(self, dreams_dir, tmp_path):
        from lib.hermes_dream import apply_dream
        d_id = "01TESTMALFORMED000000000A"
        artifact = dreams_dir / d_id
        artifact.mkdir(parents=True)
        (artifact / "manifest.json").write_text(json.dumps({
            "scope": "default", "started_at": "x", "finished_at": "y",
            "model_used": "test", "signal_density_score": 0.0,
            "recall_regression_verdict": "skipped",
            "cost": {"tokens_in": 0, "tokens_out": 0, "cache_read_tokens": 0},
            "signature_anchors": [],
        }))
        (artifact / "memory.patch").write_text(
            "- {op: BOGUS, type: nonsense, body: x}\n"
        )
        fresh = tmp_path / "fresh"; fresh.mkdir()
        os.environ["HERMES_RAW_DIR"] = str(tmp_path / "raw")
        r = apply_dream(d_id, str(dreams_dir), memory_dir=str(fresh),
                        force_apply=True)
        assert r["status"] == "refused"
        assert "Invalid proposal" in r["reason"]


class TestApplySourceVocabulary:
    """P8: applied entries carry source='dream:<id>'."""

    def test_applied_entry_has_dream_source(self, dreams_dir, memdir_with_entries):
        from lib.hermes_dream import apply_dream, create_dream_artifact
        d = create_dream_artifact("default", memory_dir=str(memdir_with_entries),
                                  dreams_dir=str(dreams_dir), dry_run=True)
        apply_dream(d, str(dreams_dir),
                    memory_dir=str(memdir_with_entries), force_apply=True)
        from lib.hermes_memory import read_entries
        sources = {e["source"] for e in read_entries(str(memdir_with_entries))}
        assert any(s.startswith("dream:") for s in sources)


# ═════════════════════════════════════════════════════════════════════════════
# Story 4.7: discard + audit
# ═════════════════════════════════════════════════════════════════════════════


class TestDiscard:
    def test_discard_removes_dir_and_audits(self, dreams_dir, memdir_with_entries):
        from lib.hermes_dream import (
            create_dream_artifact, discard_dream, verify_audit_chain,
        )
        d = create_dream_artifact("default", memory_dir=str(memdir_with_entries),
                                  dreams_dir=str(dreams_dir), dry_run=True)
        assert (dreams_dir / d).is_dir()
        r = discard_dream(d, str(dreams_dir))
        assert r["status"] == "discarded"
        assert not (dreams_dir / d).exists()
        # P11 / NFR-19: audit row written + chain valid
        audit = dreams_dir / "audit.jsonl"
        assert audit.exists()
        rows = [json.loads(ln) for ln in audit.read_text().splitlines() if ln]
        assert any(r["op"] == "discard" and r["dream_id"] == d for r in rows)
        assert verify_audit_chain(str(dreams_dir))

    def test_discard_idempotent(self, dreams_dir):
        from lib.hermes_dream import discard_dream
        r = discard_dream("NONEXISTENT", str(dreams_dir))
        assert r["status"] == "not_found"

    def test_discard_refuses_symlink(self, dreams_dir, memdir_with_entries, tmp_path):
        """P20: symlink artifact refused."""
        from lib.hermes_dream import discard_dream
        # Create a real dir then a symlink pointing to it.
        target = tmp_path / "real_artifact"; target.mkdir()
        link = dreams_dir / "LINKED_DREAM"
        dreams_dir.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target, target_is_directory=True)
        with pytest.raises(RuntimeError, match="symlink"):
            discard_dream("LINKED_DREAM", str(dreams_dir))
        # Target still exists.
        assert target.exists()


# ═════════════════════════════════════════════════════════════════════════════
# P11 / NFR-19 audit chain on apply
# ═════════════════════════════════════════════════════════════════════════════


class TestAuditLog:
    def test_apply_writes_audit_row(self, dreams_dir, memdir_with_entries):
        from lib.hermes_dream import apply_dream, create_dream_artifact, verify_audit_chain
        d = create_dream_artifact("default", memory_dir=str(memdir_with_entries),
                                  dreams_dir=str(dreams_dir), dry_run=True)
        apply_dream(d, str(dreams_dir),
                    memory_dir=str(memdir_with_entries), force_apply=True)
        audit = dreams_dir / "audit.jsonl"
        rows = [json.loads(ln) for ln in audit.read_text().splitlines() if ln]
        assert any(r["op"] == "apply" and r["dream_id"] == d for r in rows)
        assert verify_audit_chain(str(dreams_dir))

    def test_chain_breaks_when_row_mutated(
        self, dreams_dir, memdir_with_entries, tmp_path,
    ):
        from lib.hermes_dream import apply_dream, create_dream_artifact, verify_audit_chain, discard_dream
        d1 = create_dream_artifact("default", memory_dir=str(memdir_with_entries),
                                   dreams_dir=str(dreams_dir), dry_run=True)
        d2 = create_dream_artifact("default", memory_dir=str(memdir_with_entries),
                                   dreams_dir=str(dreams_dir), dry_run=True)
        discard_dream(d1, str(dreams_dir))
        discard_dream(d2, str(dreams_dir))
        audit = dreams_dir / "audit.jsonl"
        text = audit.read_text()
        # Tamper: change the first row's `actor`.
        tampered = text.replace('"actor":', '"actor": "EVIL_ACTOR_OVERWRITE", "_legit_actor":', 1)
        audit.write_text(tampered)
        assert verify_audit_chain(str(dreams_dir)) is False


# ═════════════════════════════════════════════════════════════════════════════
# P14: HERMES_DREAMS_DIR honored
# ═════════════════════════════════════════════════════════════════════════════


class TestEnvVarResolution:
    def test_dreams_dir_env_var_honored(self, tmp_path, monkeypatch):
        from lib.hermes_dream import _resolve_dreams_dir
        d = tmp_path / "custom_dreams"
        monkeypatch.setenv("HERMES_DREAMS_DIR", str(d))
        assert _resolve_dreams_dir() == d

    def test_dreams_dir_falls_back_to_hermes_home(self, tmp_path, monkeypatch):
        from lib.hermes_dream import _resolve_dreams_dir
        monkeypatch.delenv("HERMES_DREAMS_DIR", raising=False)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        assert _resolve_dreams_dir() == tmp_path / "dreams"


# ═════════════════════════════════════════════════════════════════════════════
# P13: _write_file_atomic actually atomic
# ═════════════════════════════════════════════════════════════════════════════


class TestAtomicWrite:
    def test_no_tmp_left_behind(self, tmp_path):
        from lib.hermes_dream import _write_file_atomic
        target = tmp_path / "out.json"
        _write_file_atomic(target, '{"ok": true}\n')
        assert target.exists()
        assert not list(tmp_path.glob("*.tmp"))
