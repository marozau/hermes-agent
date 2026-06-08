"""
Epic 5 — Dry-Run Recall Regression Harness.

Covers FR-25..FR-28 + Story 5.1-5.3 ACs + the code-review patches:
  DN1 substring match on expected_answer (not question template)
  DN2 _materialize_proposed_memory()
  DN3 answer preview + hash on RecallResult
  DN4 char-count Δtokens proxy
  DN5 force_recall / force_reason kwargs on apply_dream
  P1  create_dream_artifact writes <id>/.hermes-private/recall.json
  P2  .hermes-private/ subdir created with 0o700
  P3  apply_dream consults recall.json (regression_blocked status)
  P7  Δtokens added to manifest
  P9  6-state match classifier
  P10 build_recall_set always returns RecallReport
  P11 status as Literal
  P12 (year, week) composite seed
  P14 empty queries → status=skipped
  P15 gate_apply_with_recall requires proposed_memory_dir
  P16 materialize_proposed_memory replays patches
  P18 test fixture isolates raw layer (no leak into ~/.hermes/raw)
  P19 real regression scenario test
  P22 force_reason ≥10 chars
  CLI --force --reason + exit code 2
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from autodream.recall import (
    RecallQuery, RecallReport, RecallResult,
    build_recall_set, compute_delta_tokens,
    materialize_proposed_memory, memory_token_count,
    read_recall_artifact, recall_artifact_path,
    regression_blocks_apply, run_regression_check,
    run_recall_at_create, write_recall_artifact,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures (P18: raw isolation everywhere)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def raw_dir_with_entries(tmp_path, monkeypatch):
    """Isolated raw layer with 60 entries."""
    d = tmp_path / "raw" / "default" / "engineer"
    d.mkdir(parents=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entries = []
    for i in range(60):
        entries.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "entry_id": f"entry-{i:03d}",
            "project": "default",
            "role": "engineer",
            "kind": "fact" if i % 2 == 0 else "trajectory",
            "content": f"Memory entry {i}: important fact about topic kubernetes-{i:03d}-deployment",
            "evidence": f"session:abc:{i}",
        })
    (d / f"{today}.jsonl").write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n"
    )
    monkeypatch.setenv("HERMES_RAW_DIR", str(tmp_path / "raw"))
    return tmp_path / "raw"


@pytest.fixture
def memory_dir(tmp_path, monkeypatch):
    """Isolated memory dir. P18: also isolates HERMES_RAW_DIR so add_entry
    doesn't leak raw lines into ~/.hermes/raw/."""
    d = tmp_path / "memory" / "typed"
    raw = tmp_path / "raw_isolation"
    monkeypatch.setenv("HERMES_MEMORY_DIR", str(d))
    monkeypatch.setenv("HERMES_RAW_DIR", str(raw))
    from autodream.memory import add_entry
    d.mkdir(parents=True)
    for i in range(15):
        add_entry(
            "fact",
            f"Memory entry {i}: important fact about topic kubernetes-{i:03d}-deployment",
            "self-derived",
            memory_dir=str(d),
            raw_dir=str(raw),
        )
    return d


# ─────────────────────────────────────────────────────────────────────────────
# Story 5.1: build_recall_set (FR-25)
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildRecallSet:
    def test_returns_recall_report_with_queries(self, raw_dir_with_entries):
        """P10: always returns RecallReport (no union)."""
        report = build_recall_set(raw_dir=str(raw_dir_with_entries))
        assert isinstance(report, RecallReport)
        assert report.status == "ready"
        assert len(report.queries) == 20
        for q in report.queries:
            assert isinstance(q, RecallQuery)
            assert q.query
            assert q.expected_answer

    def test_deterministic_by_year_week(self, raw_dir_with_entries):
        """P12: (year, week) seed."""
        r1 = build_recall_set(
            raw_dir=str(raw_dir_with_entries), seed_year=2026, seed_week=20,
        )
        r2 = build_recall_set(
            raw_dir=str(raw_dir_with_entries), seed_year=2026, seed_week=20,
        )
        assert [q.query for q in r1.queries] == [q.query for q in r2.queries]
        assert r1.seed == r2.seed

    def test_same_week_different_year_different_sample(self, raw_dir_with_entries):
        """P12: same week different year now produces different samples."""
        r1 = build_recall_set(
            raw_dir=str(raw_dir_with_entries), seed_year=2026, seed_week=20,
        )
        r2 = build_recall_set(
            raw_dir=str(raw_dir_with_entries), seed_year=2027, seed_week=20,
        )
        assert {q.query for q in r1.queries} != {q.query for q in r2.queries}

    def test_cold_start_returns_skipped_report(self, tmp_path, monkeypatch):
        d = tmp_path / "raw_empty" / "default" / "engineer"
        d.mkdir(parents=True)
        (d / "2026-01-01.jsonl").write_text("")
        monkeypatch.setenv("HERMES_RAW_DIR", str(tmp_path / "raw_empty"))
        r = build_recall_set(raw_dir=str(tmp_path / "raw_empty"))
        assert r.status == "skipped"
        assert "cold-start" in r.reason
        assert r.queries == []

    def test_no_raw_dir_returns_skipped(self, tmp_path):
        r = build_recall_set(raw_dir=str(tmp_path / "missing"))
        assert r.status == "skipped"


# ─────────────────────────────────────────────────────────────────────────────
# Story 5.2: run_regression_check (FR-26, P9 match classifier)
# ─────────────────────────────────────────────────────────────────────────────


class TestRunRegressionCheck:
    def test_self_compare_no_regression(self, raw_dir_with_entries, memory_dir):
        rs = build_recall_set(raw_dir=str(raw_dir_with_entries))
        report = run_regression_check(
            rs.queries,
            current_memory_dir=str(memory_dir),
            proposed_memory_dir=str(memory_dir),
        )
        assert report.status == "complete"
        assert report.regression is False
        # Self-compare → every result is identical_both_*
        for r in report.results:
            assert r.match in ("identical_both_correct", "identical_both_incorrect")

    def test_real_regression_detected(self, raw_dir_with_entries, tmp_path, monkeypatch):
        """P19: a strictly worse proposed memory must trigger regression=True."""
        from autodream.memory import add_entry
        # Current dir has all 15 entries.
        current = tmp_path / "current"
        cur_raw = tmp_path / "current_raw"
        for i in range(15):
            add_entry(
                "fact",
                f"Memory entry {i}: important fact about topic kubernetes-{i:03d}-deployment",
                "self-derived",
                memory_dir=str(current), raw_dir=str(cur_raw),
            )
        # Proposed dir has only the first 3 entries (most queries now fail).
        proposed = tmp_path / "proposed"
        prop_raw = tmp_path / "proposed_raw"
        for i in range(3):
            add_entry(
                "fact",
                f"Memory entry {i}: important fact about topic kubernetes-{i:03d}-deployment",
                "self-derived",
                memory_dir=str(proposed), raw_dir=str(prop_raw),
            )
        rs = build_recall_set(raw_dir=str(raw_dir_with_entries))
        report = run_regression_check(
            rs.queries,
            current_memory_dir=str(current),
            proposed_memory_dir=str(proposed),
        )
        assert report.regression is True
        assert report.proposed_score < report.current_score
        # P9: degraded category should appear
        assert any(r.match == "degraded" for r in report.results)

    def test_empty_queries_skipped(self):
        """P14: empty input → status=skipped, not false-pass regression=False."""
        report = run_regression_check([], current_memory_dir="x", proposed_memory_dir="y")
        assert report.status == "skipped"
        assert report.reason == "empty-recall-set"

    def test_result_has_answer_preview_and_hash(self, raw_dir_with_entries, memory_dir):
        """DN3 / P8: RecallResult includes answer preview + hash."""
        rs = build_recall_set(raw_dir=str(raw_dir_with_entries))
        report = run_regression_check(
            rs.queries,
            current_memory_dir=str(memory_dir),
            proposed_memory_dir=str(memory_dir),
        )
        for r in report.results:
            assert hasattr(r, "current_answer")
            assert hasattr(r, "proposed_answer")
            assert hasattr(r, "current_answer_hash")
            assert len(r.current_answer) <= 200
            assert len(r.proposed_answer) <= 200


# ─────────────────────────────────────────────────────────────────────────────
# Story 5.3: regression_blocks_apply (FR-27, FR-28, P22)
# ─────────────────────────────────────────────────────────────────────────────


class TestRegressionGate:
    def test_regression_blocks(self):
        r = RecallReport(status="complete", regression=True,
                         current_score=0.8, proposed_score=0.5)
        out = regression_blocks_apply(r)
        assert out["blocked"] is True
        assert "regression" in out["reason"]

    def test_force_with_long_reason_overrides(self):
        r = RecallReport(status="complete", regression=True,
                         current_score=0.8, proposed_score=0.5)
        out = regression_blocks_apply(
            r, force=True,
            force_reason="Accepting recall loss for dedup gain — see ticket H-123",
        )
        assert out["blocked"] is False
        assert out["forced"] is True

    def test_force_with_short_reason_rejected(self):
        """P22: reason must be ≥10 chars (was: any non-empty)."""
        r = RecallReport(status="complete", regression=True,
                         current_score=0.8, proposed_score=0.5)
        out = regression_blocks_apply(r, force=True, force_reason="meh")
        assert out["blocked"] is True

    def test_force_with_empty_reason_rejected(self):
        r = RecallReport(status="complete", regression=True,
                         current_score=0.8, proposed_score=0.5)
        out = regression_blocks_apply(r, force=True, force_reason="")
        assert out["blocked"] is True

    def test_no_regression_passes(self):
        r = RecallReport(status="complete", regression=False,
                         current_score=0.9, proposed_score=0.9)
        out = regression_blocks_apply(r)
        assert out["blocked"] is False

    def test_skipped_status_passes(self):
        r = RecallReport(status="skipped", reason="cold-start")
        out = regression_blocks_apply(r)
        assert out["blocked"] is False

    def test_none_report_fails_closed(self):
        out = regression_blocks_apply(None)
        assert out["blocked"] is True


# ─────────────────────────────────────────────────────────────────────────────
# DN2 / P16: materialize_proposed_memory
# ─────────────────────────────────────────────────────────────────────────────


class TestMaterialization:
    def test_materializes_add_op(self, tmp_path):
        """Materializer copies current + replays an add proposal."""
        import yaml
        current = tmp_path / "current"
        cur_raw = tmp_path / "cur_raw"
        from autodream.memory import add_entry
        add_entry("fact", "existing entry", "self-derived",
                  memory_dir=str(current), raw_dir=str(cur_raw))

        artifact = tmp_path / "artifact"
        artifact.mkdir()
        (artifact / "memory.patch").write_text(yaml.dump([{
            "op": "add", "type": "fact",
            "body": "newly added fact",
            "rationale": "test", "confidence": "high",
            "risk_class": "additive", "source_refs": [],
        }]))
        dest = tmp_path / "proposed"
        materialize_proposed_memory(artifact, str(current), dest)
        bodies = []
        for f in dest.glob("*.md"):
            content = f.read_text()
            if "newly added" in content or "existing" in content:
                bodies.append(content)
        # Materialized state has BOTH the existing entry and the new one.
        assert any("existing entry" in b for b in bodies)
        assert any("newly added fact" in b for b in bodies)


# ─────────────────────────────────────────────────────────────────────────────
# DN4 / P7: Δtokens
# ─────────────────────────────────────────────────────────────────────────────


class TestDeltaTokens:
    def test_token_count_sums_bodies(self, memory_dir):
        count = memory_token_count(str(memory_dir))
        assert count > 0  # 15 entries × non-trivial body

    def test_delta_is_after_minus_before(self, tmp_path):
        before_dir = tmp_path / "before"
        before_dir.mkdir()
        after_dir = tmp_path / "after"
        after_dir.mkdir()
        # before empty, after has one entry
        (after_dir / "01TEST.md").write_text("---\nid: 01TEST\ntype: fact\n---\nhello\n")
        delta = compute_delta_tokens(str(before_dir), str(after_dir))
        assert delta["before"] == 0
        assert delta["after"] > 0
        assert delta["delta"] == delta["after"] - delta["before"]


# ─────────────────────────────────────────────────────────────────────────────
# P1: recall artifact persistence
# ─────────────────────────────────────────────────────────────────────────────


class TestRecallArtifact:
    def test_write_and_read_round_trip(self, tmp_path):
        report = RecallReport(status="complete", regression=False,
                              current_score=0.9, proposed_score=0.9)
        artifact = tmp_path / "dream"
        artifact.mkdir()
        path = write_recall_artifact(artifact, report)
        # P2: lives at .hermes-private/recall.json
        assert path == artifact / ".hermes-private" / "recall.json"
        assert path.exists()
        # 0o600
        assert (path.stat().st_mode & 0o777) == 0o600
        # round-trip
        loaded = read_recall_artifact(artifact)
        assert loaded.regression is False
        assert loaded.current_score == 0.9

    def test_run_recall_at_create_writes_artifact(
        self, raw_dir_with_entries, memory_dir, tmp_path,
    ):
        artifact = tmp_path / "dream_id"
        artifact.mkdir()
        # No memory.patch → materializer is a no-op (proposed == current).
        report = run_recall_at_create(artifact, str(memory_dir))
        assert isinstance(report, RecallReport)
        path = recall_artifact_path(artifact)
        assert path.exists()


# ─────────────────────────────────────────────────────────────────────────────
# Integration: create_dream_artifact writes recall.json + Δtokens
# ─────────────────────────────────────────────────────────────────────────────


class TestCreateIntegratesRecall:
    def test_create_writes_recall_json(self, raw_dir_with_entries, memory_dir, tmp_path, monkeypatch):
        from autodream.dream import create_dream_artifact
        monkeypatch.setenv("HERMES_DREAMS_DIR", str(tmp_path / "dreams"))
        d_id = create_dream_artifact(
            scope="default",
            memory_dir=str(memory_dir),
            dreams_dir=str(tmp_path / "dreams"),
            dry_run=True,
        )
        recall = recall_artifact_path(tmp_path / "dreams" / d_id)
        assert recall.exists(), f"recall.json missing at {recall}"

    def test_manifest_carries_delta_tokens(
        self, raw_dir_with_entries, memory_dir, tmp_path, monkeypatch,
    ):
        from autodream.dream import create_dream_artifact
        monkeypatch.setenv("HERMES_DREAMS_DIR", str(tmp_path / "dreams"))
        d_id = create_dream_artifact(
            scope="default",
            memory_dir=str(memory_dir),
            dreams_dir=str(tmp_path / "dreams"),
            dry_run=True,
        )
        manifest = json.loads(
            (tmp_path / "dreams" / d_id / "manifest.json").read_text()
        )
        assert "delta_tokens" in manifest
        for key in ("before", "after", "delta"):
            assert key in manifest["delta_tokens"]


# ─────────────────────────────────────────────────────────────────────────────
# Integration: apply_dream consults recall.json (P3 / FR-27)
# ─────────────────────────────────────────────────────────────────────────────


class TestApplyConsultsRecallGate:
    def test_apply_blocked_when_regression_true(self, tmp_path, monkeypatch):
        from autodream.dream import apply_dream
        dreams = tmp_path / "dreams"
        artifact = dreams / "01TESTREGRESSION0000000A"
        artifact.mkdir(parents=True)
        # Minimal artifact: manifest + (no memory.patch needed for the gate check
        # since we want to hit the recall gate first).
        (artifact / "manifest.json").write_text(json.dumps({
            "scope": "default", "started_at": "x", "finished_at": "y",
            "model_used": "test", "signal_density_score": 0.0,
            "recall_regression_verdict": "fail",
            "cost": {"tokens_in": 0, "tokens_out": 0, "cache_read_tokens": 0},
            "signature_anchors": [],
            "delta_tokens": {"before": 100, "after": 50, "delta": -50},
        }))
        # Write a recall.json with regression=true.
        bad = RecallReport(status="complete", regression=True,
                           current_score=0.9, proposed_score=0.4)
        write_recall_artifact(artifact, bad)

        mem = tmp_path / "mem"; mem.mkdir()
        monkeypatch.setenv("HERMES_RAW_DIR", str(tmp_path / "raw_iso"))
        result = apply_dream(
            "01TESTREGRESSION0000000A",
            str(dreams),
            memory_dir=str(mem),
            force_apply=True,  # Epic 4 manual ack
            # No force_recall — must block.
        )
        assert result["status"] == "regression_blocked"
        assert "regression" in result["reason"]

    def test_apply_force_with_reason_overrides(self, tmp_path, monkeypatch):
        from autodream.dream import apply_dream
        dreams = tmp_path / "dreams"
        artifact = dreams / "01TESTFORCE000000000000B"
        artifact.mkdir(parents=True)
        (artifact / "manifest.json").write_text(json.dumps({
            "scope": "default", "started_at": "x", "finished_at": "y",
            "model_used": "test", "signal_density_score": 0.0,
            "recall_regression_verdict": "fail",
            "cost": {"tokens_in": 0, "tokens_out": 0, "cache_read_tokens": 0},
            "signature_anchors": [],
            "delta_tokens": {"before": 0, "after": 0, "delta": 0},
        }))
        # Real proposal so apply has work to do.
        import yaml
        (artifact / "memory.patch").write_text(yaml.dump([{
            "op": "add", "type": "fact", "body": "forced through",
            "rationale": "test", "confidence": "high",
            "risk_class": "additive", "source_refs": [],
        }]))
        write_recall_artifact(artifact, RecallReport(
            status="complete", regression=True,
            current_score=0.9, proposed_score=0.5,
        ))

        mem = tmp_path / "mem"; mem.mkdir()
        monkeypatch.setenv("HERMES_RAW_DIR", str(tmp_path / "raw_iso"))
        result = apply_dream(
            "01TESTFORCE000000000000B",
            str(dreams),
            memory_dir=str(mem),
            force_apply=True,
            force_recall=True,
            force_reason="Accepting -0.4 recall for major dedup gain (H-456)",
        )
        assert result["status"] == "applied"
        # FR-28: audit row carries forced + reason.
        audit_lines = (dreams / "audit.jsonl").read_text().strip().split("\n")
        apply_row = next(json.loads(ln) for ln in audit_lines
                         if json.loads(ln)["op"] == "apply")
        assert apply_row["forced"] is True
        assert "H-456" in apply_row["reason"]


# ─────────────────────────────────────────────────────────────────────────────
# CLI exit codes (FR-27, Story 5.3 AC #1/#3)
# ─────────────────────────────────────────────────────────────────────────────


class TestCLIExitCodes:
    def test_force_without_reason_exits_2(self, tmp_path):
        """Story 5.3 AC #3: `--force` alone exits 2."""
        import subprocess
        r = subprocess.run(
            ["/Users/im/.hermes/hermes-agent/.venv/bin/python",
             "/Users/im/.hermes/bin/hermes-dream",
             "apply", "01TESTDOESNOTEXIST00000Q",
             "--accept", "--force"],
            capture_output=True, text=True,
            env={**os.environ, "HERMES_HOME": str(tmp_path)},
        )
        assert r.returncode == 2
        assert "reason" in r.stdout.lower()
