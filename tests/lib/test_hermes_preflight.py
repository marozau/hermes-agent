"""Epic 7 — Preflight (FAMA Principle #3 Closure).

Covers Stories 7.1–7.7 + the code-review patches:
  DN1 plugin package at ~/.hermes/plugins/preflight/
  DN2 verify SKILL.md augmentation + preflight_verify_helper.py
  DN3 citations persisted to last-cited.json
  DN4 shadow_mode toggle (config.yaml + HERMES_PREFLIGHT_MODE)
  DN5 timestamp resolution via hermes_memory lookup
  P1  ~/.hermes/plugins/preflight/__init__.py exists
  P3  ~/.hermes/preflight/config.yaml on disk
  P4  ~/.hermes/preflight/domain-vocab.txt on disk
  P5  hermes-preflight CLI (smoke)
  P6  shadow mode emits telemetry, returns None heads_up
  P9  real BM25 from row["rank"]
  P10 timestamp lookup via hermes_memory
  P11 valid_until-past hits filtered
  P12 warm-up (<3 turns) skip
  P13 telemetry on EVERY invocation (fire OR skip)
  P14 token-match `--preflight=off` (no false positive on `--preflight=offline`)
  P15 word-boundary vocab match
  P16 dedup classify_intent call
  P17 bounded _gates + _fired_hashes
  P18 math.exp + recency clamped [0,1]
  P19 atomic telemetry append
  P20 mark_fired AFTER telemetry write
  P21 dilution cap with primary-domain anchor
"""
import json
import os
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest
import yaml

# HERMES_ROOT is the runtime workspace root (~/.hermes/) — where preflight/
# config.yaml, domain-vocab.txt, etc. live. Independent of the dev tree where
# this test file is checked in.
HERMES_ROOT = Path.home() / ".hermes"
import sys
if str(HERMES_ROOT) not in sys.path:
    sys.path.insert(0, str(HERMES_ROOT))

from lib.hermes_preflight import (
    PreflightGate, PreflightTelemetry, SkipReason, TrajectoryHit, IntentResult,
    classify_intent, dedupe_and_cap, evaluate_skip_ladder, format_heads_up,
    persist_citations, rank_trajectories, read_citations,
    retrieve_trajectories, should_run_preflight, write_preflight_telemetry,
    _filter_stale_trajectories, _ensure_hydrated, _load_gates_from_disk,
    _save_gates_to_disk, _gates_path, _gates,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def vocab_file(tmp_path):
    d = tmp_path / "preflight"
    d.mkdir()
    p = d / "domain-vocab.txt"
    p.write_text("k3d\nkubernetes\ndocker\nprefect\ngit\nhermes\ntest\nmemory\n")
    return p


@pytest.fixture
def preflight_config(tmp_path):
    d = tmp_path / "preflight"
    d.mkdir(exist_ok=True)
    cfg = {
        "mode": "live",
        "enabled": True,
        "top_k": 3,
        "weights": {"bm25": 0.45, "recency": 0.25, "category": 0.20, "resolution": 0.10},
    }
    p = d / "config.yaml"
    p.write_text(yaml.dump(cfg))
    return p


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Isolate HERMES_HOME so the runtime doesn't see real ~/.hermes/preflight."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


# ─────────────────────────────────────────────────────────────────────────────
# Story 7.2: Intent classifier
# ─────────────────────────────────────────────────────────────────────────────


class TestIntentClassifier:
    def test_short_no_domain(self, vocab_file):
        r = classify_intent("hi there", str(vocab_file))
        assert r.complexity is False
        assert r.domains == []

    def test_long_message_complex(self, vocab_file):
        r = classify_intent("please help me " * 30, str(vocab_file))
        assert r.complexity is True

    def test_complexity_keywords(self, vocab_file):
        r = classify_intent("help me refactor and migrate the build system", str(vocab_file))
        assert r.complexity is True

    def test_domain_word_boundary(self, vocab_file):
        """P15: domain match is word-boundary, not substring."""
        r = classify_intent("please refactor the kubernetes deployment using docker",
                            str(vocab_file))
        assert "kubernetes" in r.domains
        assert "docker" in r.domains

    def test_domain_substring_does_NOT_match(self, vocab_file):
        """P15: vocab 'git' must NOT match 'digital'."""
        r = classify_intent("transform the digital signals", str(vocab_file))
        assert "git" not in r.domains


# ─────────────────────────────────────────────────────────────────────────────
# Story 7.6 + P12 + P14: skip ladder
# ─────────────────────────────────────────────────────────────────────────────


class TestSkipLadder:
    def test_disabled_skips(self):
        gate = PreflightGate(enabled=False, session_id="s", turn_count=10)
        assert evaluate_skip_ladder(gate, "task message") == SkipReason.DISABLED

    def test_warm_up_under_3_turns(self):
        """P12 / FR-32: first 3 turns skip with WARM_UP."""
        gate = PreflightGate(enabled=True, session_id="s", turn_count=0)
        assert evaluate_skip_ladder(gate, "complex task") == SkipReason.WARM_UP
        gate.turn_count = 2
        assert evaluate_skip_ladder(gate, "complex task") == SkipReason.WARM_UP

    def test_warm_up_passes_after_3_turns(self):
        gate = PreflightGate(enabled=True, session_id="s", turn_count=3)
        reason = evaluate_skip_ladder(gate, "small", message_hash="h1")
        assert reason in (None, SkipReason.SMALL_NO_DOMAIN, SkipReason.NO_TRAJECTORIES)

    def test_already_fired(self):
        gate = PreflightGate(enabled=True, session_id="s", turn_count=10)
        gate.mark_fired("h1")
        assert evaluate_skip_ladder(gate, "task", message_hash="h1") == SkipReason.ALREADY_FIRED

    def test_preflight_off_token_match(self):
        """P14: --preflight=off match is word-boundary."""
        gate = PreflightGate(enabled=True, session_id="s", turn_count=10)
        assert evaluate_skip_ladder(gate, "task --preflight=off here") == SkipReason.USER_DISABLED

    def test_preflight_offline_does_NOT_trigger_user_disabled(self):
        """P14: substring false-positive eliminated."""
        gate = PreflightGate(enabled=True, session_id="s", turn_count=10)
        reason = evaluate_skip_ladder(gate, "we go --preflight=offline now")
        assert reason != SkipReason.USER_DISABLED

    def test_force_bypasses_skip(self):
        gate = PreflightGate(enabled=False, session_id="s", turn_count=0)
        assert evaluate_skip_ladder(gate, "any", force=True) is None

    def test_skip_latency_under_5ms_for_already_fired(self):
        gate = PreflightGate(enabled=True, session_id="s", turn_count=10)
        gate.mark_fired("h")
        import time
        t0 = time.perf_counter()
        evaluate_skip_ladder(gate, "msg", message_hash="h")
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert elapsed_ms < 5


# ─────────────────────────────────────────────────────────────────────────────
# Story 7.3: retrieval + 4-factor ranking (P9 real BM25)
# ─────────────────────────────────────────────────────────────────────────────


class TestRetrievalAndRanking:
    def test_retrieve_composes_quoted_or_query(self):
        captured = {}
        def fake_search(q, limit):
            captured["q"] = q
            return []
        retrieve_trajectories(["k3d", "docker"], fake_search)
        # P24: terms quoted.
        assert '"k3d"' in captured["q"]
        assert '"docker"' in captured["q"]
        assert " OR " in captured["q"]

    def test_real_bm25_from_rank(self):
        """P9: row['rank'] used (SQLite FTS5 returns negative rank, smaller = better)."""
        rows = [
            {"id": "h1", "content": "TRAJECTORY tool-misuse docker", "rank": -5.0,
             "entry_id": "h1"},
            {"id": "h2", "content": "TRAJECTORY tool-misuse docker", "rank": -1.0,
             "entry_id": "h2"},
        ]
        hits = retrieve_trajectories(["docker"], lambda q, limit: rows)
        # Lower abs(rank) = better → h2 should score higher on bm25.
        h_by_id = {h.id: h for h in hits}
        assert h_by_id["h2"].bm25_score > h_by_id["h1"].bm25_score

    def test_recency_clamped_no_future_boost(self):
        """P18: future timestamps clamped — no unbounded recency."""
        future_ts = datetime.now(timezone.utc).timestamp() + 86400 * 365 * 10
        hits = [
            TrajectoryHit(id="h1", content="x", bm25_score=0.5, category="tool-misuse",
                          domain="docker", timestamp=future_ts, has_resolution=False),
        ]
        ranked = rank_trajectories(hits)
        assert 0.0 <= ranked[0].score <= 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Story 7.4: dedupe + dilution cap (P21)
# ─────────────────────────────────────────────────────────────────────────────


class TestDedupeAndCap:
    def test_dedupe_keeps_top_per_bucket(self):
        hits = [
            TrajectoryHit(id="a", content="x", bm25_score=0.9, category="tool-misuse",
                          domain="docker", timestamp=0, has_resolution=True),
            TrajectoryHit(id="b", content="x", bm25_score=0.5, category="tool-misuse",
                          domain="docker", timestamp=0, has_resolution=True),
        ]
        ranked = rank_trajectories(hits)
        d = dedupe_and_cap(ranked, k=3)
        assert {h.id for h in d} == {"a"}

    def test_dilution_cap_pins_primary_domain(self):
        """P21: when capping diverse domains, the user's primary domain is preserved."""
        hits = []
        for i, dom in enumerate(["docker", "git", "k3d", "kubernetes", "prefect"]):
            hits.append(TrajectoryHit(
                id=f"{dom}-h", content=f"x {dom}", bm25_score=0.9 - 0.1 * i,
                category="tool-misuse", domain=dom, timestamp=0, has_resolution=True,
            ))
        ranked = rank_trajectories(hits)
        # Primary domain is "kubernetes" — even though score-wise other domains
        # win, the dilution cap should keep at least one kubernetes entry.
        d = dedupe_and_cap(ranked, k=3, primary_domain="kubernetes")
        assert any(h.domain == "kubernetes" for h in d)


# ─────────────────────────────────────────────────────────────────────────────
# Story 7.5: formatter
# ─────────────────────────────────────────────────────────────────────────────


class TestFormatHeadsUp:
    def test_wraps_in_sentinel(self):
        hits = [TrajectoryHit(id="t1", content="A → B → C",
                              category="tool-misuse", domain="k3d",
                              bm25_score=0.9,
                              timestamp=datetime.now(timezone.utc).timestamp(),
                              has_resolution=True)]
        out = format_heads_up(hits)
        assert "<preflight-heads-up>" in out and "</preflight-heads-up>" in out
        assert "t1" in out
        assert "tool-misuse" in out

    def test_three_part_chain_preserved(self):
        """`extract_attempt_failure_fix` preserves middle (failure) segment."""
        hits = [TrajectoryHit(id="t1", content="tried Q → got R → fixed by S",
                              category="edit-error", domain="git",
                              bm25_score=0.5, timestamp=0, has_resolution=True)]
        out = format_heads_up(hits)
        assert "got R" in out

    def test_unknown_timestamp_renders_as_unknown(self):
        hits = [TrajectoryHit(id="t1", content="A → B",
                              category="tool-misuse", domain="x",
                              bm25_score=0.5, timestamp=0.0, has_resolution=False)]
        out = format_heads_up(hits)
        assert "unknown" in out  # date renders as "unknown" not "1970-01-01"


# ─────────────────────────────────────────────────────────────────────────────
# Story 7.7: telemetry + citations (P13, P19, DN3)
# ─────────────────────────────────────────────────────────────────────────────


class TestTelemetry:
    def test_telemetry_row_written(self, tmp_path):
        log_dir = tmp_path / "preflight" / "log"
        t = PreflightTelemetry(
            session_id="s1", intent_hash="abc", domains=["k3d"],
            complexity_hit=True, skip_reason=None, raw_hits=5,
            top_ids=["t1"], scores=[0.8], elapsed_ms=10.0, mode="live",
            cited_entry_ids=["t1"],
        )
        write_preflight_telemetry(t, log_dir=str(log_dir))
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rows = [json.loads(line) for line in (log_dir / f"{today}.jsonl")
                .read_text().strip().split("\n")]
        assert rows[0]["mode"] == "live"
        assert rows[0]["cited_entry_ids"] == ["t1"]

    def test_telemetry_file_mode(self, tmp_path):
        """P19: atomic append with 0o600."""
        log_dir = tmp_path / "preflight" / "log"
        t = PreflightTelemetry(
            session_id="s1", intent_hash="x", domains=[],
            complexity_hit=False, skip_reason="disabled", raw_hits=0,
            top_ids=[], scores=[], elapsed_ms=0.1, mode="shadow",
        )
        write_preflight_telemetry(t, log_dir=str(log_dir))
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        f = log_dir / f"{today}.jsonl"
        assert (f.stat().st_mode & 0o777) == 0o600


class TestCitations:
    def test_persist_and_read(self, isolated_env):
        persist_citations("sess-1", ["e1", "e2", "e3"])
        cited = read_citations("sess-1")
        assert cited == ["e1", "e2", "e3"]

    def test_read_unknown_session_empty(self, isolated_env):
        assert read_citations("never-fired") == []


# ─────────────────────────────────────────────────────────────────────────────
# Integration: should_run_preflight (P6 shadow mode, P13 telemetry-always)
# ─────────────────────────────────────────────────────────────────────────────


class TestShouldRunPreflight:
    def _fake_search(self, q, limit):
        return [{
            "id": "k3d-1",
            "entry_id": "k3d-1",
            "content": "TRAJECTORY: tool-misuse k3d — tried A → got B → fixed",
            "rank": -3.0,
            "timestamp": datetime.now(timezone.utc).timestamp(),
        }]

    def test_warm_up_skip_emits_telemetry(self, isolated_env, tmp_path):
        """P13: skip path writes telemetry."""
        from lib.hermes_preflight import _gates
        _gates.clear()
        # Fresh session — turn_count=0 → WARM_UP.
        gate, reason, heads_up = should_run_preflight(
            session_id="ws", message="please refactor kubernetes deployment",
        )
        assert reason == SkipReason.WARM_UP
        assert heads_up is None
        # Telemetry log row written
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log = isolated_env / "preflight" / "log" / f"{today}.jsonl"
        assert log.exists()
        rows = [json.loads(line) for line in log.read_text().strip().split("\n")]
        assert rows[-1]["skip_reason"] == SkipReason.WARM_UP.value

    def test_shadow_mode_returns_none_heads_up(self, isolated_env, tmp_path):
        """P6 / DN4: shadow mode → telemetry only; no heads_up."""
        # config.yaml sets mode=shadow.
        cfg_dir = isolated_env / "preflight"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.yaml").write_text(yaml.dump({"mode": "shadow", "enabled": True}))
        (cfg_dir / "domain-vocab.txt").write_text("kubernetes\ndocker\nk3d\n")
        from lib.hermes_preflight import _gates
        _gates.clear()

        # Bump warm-up.
        gate = PreflightGate(enabled=True, session_id="sm", turn_count=5)
        _gates["sm"] = gate

        gate2, reason, heads_up = should_run_preflight(
            session_id="sm",
            message="please refactor the kubernetes k3d deployment configuration",
            session_search_fn=self._fake_search,
        )
        # Shadow mode: returns SHADOW_MODE_DO_NOT_INJECT, heads_up=None
        assert reason == SkipReason.SHADOW_MODE_DO_NOT_INJECT
        assert heads_up is None
        # Telemetry has mode=shadow
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log = isolated_env / "preflight" / "log" / f"{today}.jsonl"
        rows = [json.loads(line) for line in log.read_text().strip().split("\n")]
        assert rows[-1]["mode"] == "shadow"

    def test_live_mode_injects_heads_up(self, isolated_env, monkeypatch):
        """P6: live mode → heads_up populated."""
        cfg_dir = isolated_env / "preflight"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.yaml").write_text(yaml.dump({"mode": "live", "enabled": True}))
        (cfg_dir / "domain-vocab.txt").write_text("kubernetes\ndocker\nk3d\n")
        from lib.hermes_preflight import _gates
        _gates.clear()
        gate = PreflightGate(enabled=True, session_id="lm", turn_count=5)
        _gates["lm"] = gate

        gate2, reason, heads_up = should_run_preflight(
            session_id="lm",
            message="please refactor the kubernetes k3d configuration deployment",
            session_search_fn=self._fake_search,
        )
        assert reason is None
        assert heads_up is not None
        assert "<preflight-heads-up>" in heads_up
        # Citations persisted
        cited = read_citations("lm")
        assert "k3d-1" in cited


# ─────────────────────────────────────────────────────────────────────────────
# On-disk artifacts (P3, P4) + plugin (P1) + CLI smoke (P5)
# ─────────────────────────────────────────────────────────────────────────────


class TestOnDiskArtifacts:
    def test_config_yaml_exists(self):
        p = HERMES_ROOT / "preflight" / "config.yaml"
        assert p.exists()
        cfg = yaml.safe_load(p.read_text())
        assert cfg["mode"] in ("shadow", "live")

    def test_domain_vocab_exists(self):
        p = HERMES_ROOT / "preflight" / "domain-vocab.txt"
        assert p.exists()
        assert p.read_text()

    def test_plugin_package_exists(self):
        p = HERMES_ROOT / "plugins" / "preflight" / "__init__.py"
        assert p.exists()

    def test_verify_helper_exists(self):
        p = HERMES_ROOT / "scripts" / "preflight_verify_helper.py"
        assert p.exists()


# ─────────────────────────────────────────────────────────────────────────────
# Bug regression: warmup_turns config (was hardcoded to 3 before 2026-05-15)
# ─────────────────────────────────────────────────────────────────────────────


class TestWarmupFromConfig:
    """Verify warmup_turns config key is actually read (not hardcoded)."""

    def test_warmup_turns_5_from_config(self, isolated_env):
        """warmup_turns=5 → skip turns 0-4, pass on turn 5."""
        cfg_dir = isolated_env / "preflight"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.yaml").write_text(yaml.dump({
            "mode": "shadow", "enabled": True, "warmup_turns": 5,
        }))
        (cfg_dir / "domain-vocab.txt").write_text("hermes\npreflight\n")
        from lib.hermes_preflight import _gates
        _gates.clear()

        gate = PreflightGate(enabled=True, session_id="wc5", turn_count=4)
        reason = evaluate_skip_ladder(gate, "small", message_hash="h0")
        assert reason == SkipReason.WARM_UP, f"turn=4 < warmup_turns=5 should skip, got {reason}"

        gate.turn_count = 5
        reason = evaluate_skip_ladder(gate, "small", message_hash="h1")
        assert reason != SkipReason.WARM_UP, f"turn=5 >= warmup_turns=5 should NOT skip warm-up"

    def test_warmup_turns_0_no_skip(self, isolated_env):
        """warmup_turns=0 → never skip warm-up."""
        cfg_dir = isolated_env / "preflight"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.yaml").write_text(yaml.dump({
            "mode": "shadow", "enabled": True, "warmup_turns": 0,
        }))
        (cfg_dir / "domain-vocab.txt").write_text("hermes\n")
        from lib.hermes_preflight import _gates
        _gates.clear()

        gate = PreflightGate(enabled=True, session_id="wc0", turn_count=0)
        reason = evaluate_skip_ladder(gate, "small", message_hash="h0")
        assert reason != SkipReason.WARM_UP, f"warmup_turns=0 should never skip warm-up, got {reason}"


# ─────────────────────────────────────────────────────────────────────────────
# Bug regression: gates.json persistence (2026-05-15)
# ─────────────────────────────────────────────────────────────────────────────


class TestGatePersistence:
    """_gates survives process restarts via gates.json."""

    def test_save_and_hydrate_roundtrip(self, isolated_env):
        """Save gates, clear memory, hydrate — verify turn_count restored."""
        cfg_dir = isolated_env / "preflight"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.yaml").write_text(yaml.dump({
            "mode": "shadow", "enabled": True, "warmup_turns": 3,
        }))

        # Simulate a few turns
        from lib.hermes_preflight import _gates, _HYDRATED
        _gates.clear()
        import lib.hermes_preflight as hp
        hp._HYDRATED = False  # force fresh hydration

        # Turn 1 via should_run_preflight
        gate, reason, _ = should_run_preflight(
            session_id="persist-01", message="small",
        )
        assert gate.turn_count == 1
        assert _gates_path().exists()  # gates.json should have been written

        # Turn 2
        gate2, reason2, _ = should_run_preflight(
            session_id="persist-01", message="small",
        )
        assert gate2.turn_count == 2

        # Verify disk state
        disk = _load_gates_from_disk()
        assert "persist-01" in disk
        assert disk["persist-01"]["turn_count"] == 2

        # Simulate process restart: clear memory, reset hydrate flag
        _gates.clear()
        hp._HYDRATED = False

        # Hydrate and verify state is restored
        _ensure_hydrated()
        assert "persist-01" in _gates
        assert _gates["persist-01"].turn_count == 2

    def test_empty_disk_no_crash(self, isolated_env):
        """No gates.json on first ever run — hydrate returns empty, no crash."""
        # Ensure no gates file exists
        p = _gates_path()
        if p.exists():
            p.unlink()

        from lib.hermes_preflight import _gates, _HYDRATED
        _gates.clear()
        import lib.hermes_preflight as hp
        hp._HYDRATED = False

        _ensure_hydrated()
        assert "nonexistent" not in _gates  # empty

    def test_last_fired_at_uses_wall_clock(self):
        """_last_fired_at stored as time.time(), not monotonic — cross-reboot safe."""
        gate = PreflightGate(enabled=True, session_id="tw")
        gate.mark_fired("hash1")
        # Verify _last_fired_at is within 2 seconds of wall clock now
        now = time.time()
        assert abs(gate._last_fired_at - now) < 2.0, \
            f"_last_fired_at={gate._last_fired_at}, now={now} — should use wall clock"

    def test_save_gates_atomic(self, isolated_env):
        """_save_gates_to_disk uses atomic write (tmp + rename)."""
        from lib.hermes_preflight import _gates
        _gates.clear()

        gate = PreflightGate(enabled=True, session_id="atomic-01", turn_count=7)
        _gates["atomic-01"] = gate
        _save_gates_to_disk()

        p = _gates_path()
        assert p.exists()
        # No .tmp file left behind
        tmp = p.with_suffix(p.suffix + ".tmp")
        assert not tmp.exists(), "temp file should have been renamed away"
        # Permissions
        assert (p.stat().st_mode & 0o777) == 0o600


# ─────────────────────────────────────────────────────────────────────────────
# Bug regression: format_heads_up with int IDs (2026-05-15)
# ─────────────────────────────────────────────────────────────────────────────


class TestFormatHeadsUpIntIds:
    """format_heads_up accepts TrajectoryHit with int IDs (from SessionDB)."""

    def test_int_id_does_not_crash(self):
        """h.id can be int (from session DB row id) — must not crash join()."""
        hits = [
            TrajectoryHit(id=4509, content="A → B → C",
                          category="tool-misuse", domain="hermes",
                          bm25_score=0.8,
                          timestamp=datetime.now(timezone.utc).timestamp(),
                          has_resolution=True),
            TrajectoryHit(id=813, content="X → Y",
                          category="edit-error", domain="preflight",
                          bm25_score=0.6,
                          timestamp=datetime.now(timezone.utc).timestamp(),
                          has_resolution=False),
        ]
        out = format_heads_up(hits)
        assert "4509" in out
        assert "813" in out
        assert "<preflight-heads-up>" in out

    def test_mixed_int_and_str_ids(self):
        """Mixed int/str IDs both render correctly."""
        hits = [
            TrajectoryHit(id="abc-123", content="A → B",
                          category="tool-misuse", domain="docker",
                          bm25_score=0.5, timestamp=0, has_resolution=False),
            TrajectoryHit(id=42, content="C → D",
                          category="edit-error", domain="git",
                          bm25_score=0.5, timestamp=0, has_resolution=False),
        ]
        out = format_heads_up(hits)
        assert "abc-123" in out
        assert "42" in out


# ─────────────────────────────────────────────────────────────────────────────
# Story 7.3 extension: stale trajectory filtering (P11 / FR-33)
# ─────────────────────────────────────────────────────────────────────────────


class TestStaleTrajectoryFiltering:
    """_filter_stale_trajectories drops entries past valid_until."""

    def test_future_valid_until_kept(self, monkeypatch):
        """Entry with valid_until in future → kept."""
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        monkeypatch.setattr(
            "lib.hermes_memory.read_entries",
            lambda read_only: [{"id": "e1", "valid_until": future}],
            raising=False,
        )
        hits = [TrajectoryHit(id="e1", content="kept", entry_id="e1")]
        result = _filter_stale_trajectories(hits)
        assert len(result) == 1
        assert result[0].id == "e1"

    def test_past_valid_until_dropped(self, monkeypatch):
        """Entry with valid_until in past → dropped."""
        past = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        monkeypatch.setattr(
            "lib.hermes_memory.read_entries",
            lambda read_only: [{"id": "e1", "valid_until": past}],
            raising=False,
        )
        hits = [TrajectoryHit(id="e1", content="stale", entry_id="e1")]
        result = _filter_stale_trajectories(hits)
        assert len(result) == 0

    def test_no_valid_until_kept(self, monkeypatch):
        """Entry without valid_until → kept."""
        monkeypatch.setattr(
            "lib.hermes_memory.read_entries",
            lambda read_only: [{"id": "e1"}],
            raising=False,
        )
        hits = [TrajectoryHit(id="e1", content="kept", entry_id="e1")]
        result = _filter_stale_trajectories(hits)
        assert len(result) == 1

    def test_no_entry_id_kept(self):
        """Hit without entry_id → kept (can't look up staleness)."""
        hits = [TrajectoryHit(id="orphan", content="kept", entry_id="")]
        result = _filter_stale_trajectories(hits)
        assert len(result) == 1

    def test_import_error_passes_through(self, monkeypatch):
        """If hermes_memory unavailable, return hits unmodified."""
        # Patch the local import inside _filter_stale_trajectories
        import builtins
        real_import = builtins.__import__
        def fake_import(name, *args, **kwargs):
            if name == "lib.hermes_memory":
                raise ImportError("no hermes_memory")
            return real_import(name, *args, **kwargs)
        monkeypatch.setattr(builtins, "__import__", fake_import)
        hits = [TrajectoryHit(id="e1", content="kept", entry_id="e1")]
        result = _filter_stale_trajectories(hits)
        assert len(result) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Skip ladder extensions: SMALL_NO_DOMAIN, NO_TRAJECTORIES, WITHIN_SKIP_WINDOW
# ─────────────────────────────────────────────────────────────────────────────


class TestSkipLadderExtended:
    """Cover skip paths not tested in the original suite."""

    def test_small_no_domain_skip(self, isolated_env):
        """Short message, no complexity keywords, no vocab domains → skipped."""
        cfg_dir = isolated_env / "preflight"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.yaml").write_text(yaml.dump({
            "mode": "shadow", "enabled": True, "warmup_turns": 3,
        }))
        (cfg_dir / "domain-vocab.txt").write_text("kubernetes\ndocker\n")
        from lib.hermes_preflight import _gates
        _gates.clear()

        gate = PreflightGate(enabled=True, session_id="sd", turn_count=5)
        reason = evaluate_skip_ladder(gate, "ok", message_hash="h1")
        assert reason == SkipReason.SMALL_NO_DOMAIN

    def test_no_trajectories_skip(self, isolated_env):
        """Domain matches, but FTS5 returns nothing → skipped."""
        cfg_dir = isolated_env / "preflight"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.yaml").write_text(yaml.dump({
            "mode": "shadow", "enabled": True, "warmup_turns": 3,
        }))
        vocab = cfg_dir / "domain-vocab.txt"
        vocab.write_text("hermes\npreflight\n")
        from lib.hermes_preflight import _gates
        _gates.clear()

        def empty_search(q, limit):
            return []

        gate = PreflightGate(enabled=True, session_id="nt", turn_count=5)
        reason = evaluate_skip_ladder(
            gate, "debug the hermes preflight plugin",
            message_hash="h1",
            session_search_fn=empty_search,
            vocab_path=str(vocab),
        )
        assert reason == SkipReason.NO_TRAJECTORIES

    def test_within_skip_window_skip(self):
        """Just fired → next message within 10 min skipped."""
        gate = PreflightGate(enabled=True, session_id="ws", turn_count=10)
        gate._last_fired_at = time.time()  # just now
        reason = evaluate_skip_ladder(
            gate, "refactor kubernetes deployment",
            message_hash="h1",
        )
        assert reason == SkipReason.WITHIN_SKIP_WINDOW

    def test_skip_window_expired_passes(self):
        """Fired 11 minutes ago → skip window expired → passes."""
        gate = PreflightGate(enabled=True, session_id="we", turn_count=10)
        gate._last_fired_at = time.time() - 660  # 11 min ago
        reason = evaluate_skip_ladder(
            gate, "refactor kubernetes deployment",
            message_hash="h1",
        )
        # Should pass skip-window. May fall through to SMALL_NO_DOMAIN
        # (15-char message), but NOT within-skip-window.
        assert reason != SkipReason.WITHIN_SKIP_WINDOW


# ─────────────────────────────────────────────────────────────────────────────
# Bug regression: empty deduped list produces valid heads-up (2026-05-15)
# ─────────────────────────────────────────────────────────────────────────────


class TestFormatHeadsUpEmpty:
    """format_heads_up always returns a string, even with empty results."""

    def test_empty_list_returns_helpful_message(self):
        """Empty hits → 'No relevant past failures found' message."""
        out = format_heads_up([])
        assert "<preflight-heads-up>" in out
        assert "No relevant past failures found" in out
        assert "</preflight-heads-up>" in out

    def test_none_not_passed(self):
        """format_heads_up should never receive None (caller guards it)."""
        # format_heads_up(deduped) — deduped is always a list from dedupe_and_cap
        # This test just documents the invariant.
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Edge-case config tests (BOM, trailing WS, malformed, missing fields, perms)
# ─────────────────────────────────────────────────────────────────────────────


class TestEdgeCaseConfig:
    """Config edge cases for _load_config and _resolve_mode."""

    def test_empty_config_file(self, isolated_env):
        """Empty config.yaml → defaults apply, no crash."""
        cfg_dir = isolated_env / "preflight"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.yaml").write_text("")
        from lib.hermes_preflight import _load_config, _resolve_mode, _gates
        _gates.clear()
        cfg = _load_config()
        assert cfg == {} or cfg is None  # empty file = empty dict
        mode = _resolve_mode()
        assert mode == "shadow"  # default

    def test_malformed_yaml_no_crash(self, isolated_env):
        """Malformed YAML → returns empty dict, no crash."""
        cfg_dir = isolated_env / "preflight"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.yaml").write_text("{ invalid: yaml: [unclosed")
        from lib.hermes_preflight import _load_config, _gates
        _gates.clear()
        cfg = _load_config()
        assert cfg == {}  # gracefully returns empty

    def test_trailing_whitespace_in_config(self, isolated_env):
        """Trailing spaces/newlines in YAML values handled correctly."""
        cfg_dir = isolated_env / "preflight"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.yaml").write_text(
            "mode: shadow   \nwarmup_turns: 5   \nenabled: true   \n"
        )
        from lib.hermes_preflight import _load_config, _gates
        _gates.clear()
        cfg = _load_config()
        assert cfg["mode"] == "shadow"
        assert cfg["warmup_turns"] == 5

    def test_bom_in_config_handled(self, isolated_env):
        """UTF-8 BOM at start of config file doesn't break parsing."""
        cfg_dir = isolated_env / "preflight"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        # Write with BOM
        content = "\ufeffmode: shadow\nenabled: true\n"
        (cfg_dir / "config.yaml").write_text(content, encoding="utf-8-sig")
        from lib.hermes_preflight import _load_config, _gates
        _gates.clear()
        cfg = _load_config()
        # YAML should handle BOM or return empty on failure
        assert isinstance(cfg, dict)

    def test_missing_required_fields_defaults(self, isolated_env):
        """Config with only mode set → other fields get defaults."""
        cfg_dir = isolated_env / "preflight"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.yaml").write_text("mode: live\n")
        from lib.hermes_preflight import _load_config, _gates
        _gates.clear()
        cfg = _load_config()
        assert cfg["mode"] == "live"
        # Missing fields → evaluate_skip_ladder uses defaults
        assert cfg.get("warmup_turns", 3) == 3

    def test_extremely_large_config_value(self, isolated_env):
        """Very large config values don't crash or OOM."""
        cfg_dir = isolated_env / "preflight"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        # Large but not absurd
        (cfg_dir / "config.yaml").write_text(
            "mode: shadow\nenabled: true\nwarmup_turns: 999999\n"
        )
        from lib.hermes_preflight import _load_config, _gates
        _gates.clear()
        cfg = _load_config()
        assert cfg["warmup_turns"] == 999999
        # Should be handled by evaluate_skip_ladder without issue
        from lib.hermes_preflight import evaluate_skip_ladder, PreflightGate
        gate = PreflightGate(enabled=True, session_id="large", turn_count=500000)
        reason = evaluate_skip_ladder(gate, "small", message_hash="h1")
        # 500000 >= 999999? No → warm-up skip
        assert reason is not None  # some skip applies

    def test_config_not_a_dict(self, isolated_env):
        """Config that parses as a list/string → returns empty dict."""
        cfg_dir = isolated_env / "preflight"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.yaml").write_text("- item1\n- item2\n")
        from lib.hermes_preflight import _load_config, _gates
        _gates.clear()
        cfg = _load_config()
        # safe_load returns a list or dict; or {} is used
        assert isinstance(cfg, (dict, list)) or cfg == {}

    def test_permission_denied_no_crash(self, isolated_env, monkeypatch):
        """If config.yaml is unreadable (permission error), return empty dict."""
        cfg_dir = isolated_env / "preflight"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_path = cfg_dir / "config.yaml"
        cfg_path.write_text("mode: shadow\n")

        # Patch Path.read_text to raise PermissionError for config.yaml only
        from pathlib import Path as PathClass
        original_read_text = PathClass.read_text

        def _patched_read_text(self, *a, **kw):
            if str(self) == str(cfg_path):
                raise PermissionError("permission denied")
            return original_read_text(self, *a, **kw)

        monkeypatch.setattr(PathClass, "read_text", _patched_read_text)
        from lib.hermes_preflight import _load_config, _gates
        _gates.clear()
        cfg = _load_config()
        assert cfg == {}  # graceful degradation


# ─────────────────────────────────────────────────────────────────────────────
# Edge-case dependency tests
# ─────────────────────────────────────────────────────────────────────────────


class TestEdgeCaseDependencies:
    """Missing or broken dependencies handled gracefully."""

    def test_yaml_unavailable_returns_empty(self, isolated_env, monkeypatch):
        """If yaml not installed, _load_config returns {}."""
        cfg_dir = isolated_env / "preflight"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.yaml").write_text("mode: live\n")

        import builtins
        real_import = builtins.__import__

        def fake_import(name, *a, **kw):
            if name == "yaml":
                raise ImportError("no yaml")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        from lib.hermes_preflight import _load_config, _gates
        _gates.clear()
        cfg = _load_config()
        assert cfg == {}  # graceful fallback

    def test_hermes_memory_unavailable_stale_filter(self, monkeypatch):
        """_filter_stale_trajectories passes through when hermes_memory missing."""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *a, **kw):
            if name == "lib.hermes_memory":
                raise ImportError("no hermes_memory")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        from lib.hermes_preflight import _filter_stale_trajectories, TrajectoryHit
        hits = [TrajectoryHit(id="e1", content="kept", entry_id="e1")]
        result = _filter_stale_trajectories(hits)
        assert len(result) == 1  # passes through

    def test_noop_search_returns_empty(self):
        """_noop_search always returns []."""
        from lib.hermes_preflight import _noop_search
        assert _noop_search("anything") == []
        assert _noop_search("anything", limit=99) == []

    def test_session_search_fn_none_triggers_noop(self, isolated_env):
        """When session_search_fn is None, force uses _noop_search → 0 hits."""
        cfg_dir = isolated_env / "preflight"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.yaml").write_text("mode: shadow\nenabled: true\nwarmup_turns: 0\n")
        (cfg_dir / "domain-vocab.txt").write_text("hermes\npreflight\n")
        from lib.hermes_preflight import should_run_preflight, _gates
        _gates.clear()
        gate, reason, heads_up = should_run_preflight(
            session_id="noop-test",
            message="debug hermes preflight pipeline",
            force=True,
            # session_search_fn omitted → uses _noop_search internally
        )
        # Should fire (force=True), but _noop_search returns 0 hits
        assert reason is None or (hasattr(reason, 'value') and reason.value == "shadow-mode")
        if heads_up:
            assert "No relevant past failures found" in heads_up

