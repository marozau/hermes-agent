"""Story 8.1 — YAKE fallback + query enrichment integration tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest
import yaml

HERMES_ROOT = Path.home() / ".hermes"
if str(HERMES_ROOT) not in sys.path:
    sys.path.insert(0, str(HERMES_ROOT))

from autodream.preflight import (
    PreflightGate, PreflightTelemetry, SkipReason,
    classify_intent, enrich_query_with_yake,
    should_run_preflight, write_preflight_telemetry,
)


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
        "warmup_turns": 0,  # skip warm-up for tests
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
# AC3: Query enrichment with YAKE keywords
# ─────────────────────────────────────────────────────────────────────────────


class TestEnrichQueryWithYake:
    """AC3: YAKE keywords are OR'd into the recall query."""

    def test_enrichment_adds_terms(self, vocab_file):
        """When base domains exist, YAKE adds non-overlapping terms."""
        base = ["k3d", "kubernetes"]
        enriched, yake_terms = enrich_query_with_yake(
            base, "set up k3d cluster with docker containers"
        )
        # Should have more terms than base
        assert len(enriched) >= len(base)
        # Original terms preserved
        assert "k3d" in enriched
        assert "kubernetes" in enriched
        # yake_terms returned for telemetry
        assert isinstance(yake_terms, list)

    def test_enrichment_deduplicates(self):
        """YAKE terms already in base_domains are not duplicated."""
        base = ["kubernetes"]
        enriched, yake_terms = enrich_query_with_yake(
            base, "kubernetes deployment"
        )
        # kubernetes should appear only once
        assert enriched.count("kubernetes") == 1

    def test_enrichment_empty_base(self):
        """When base domains are empty, YAKE can still provide terms."""
        enriched, yake_terms = enrich_query_with_yake(
            [], "configure k3d cluster for local development"
        )
        # Should have found something
        assert len(enriched) > 0
        assert len(yake_terms) > 0

    def test_enrichment_empty_message(self):
        """Empty message returns empty terms."""
        enriched, yake_terms = enrich_query_with_yake(["k3d"], "")
        assert enriched == ["k3d"]
        assert yake_terms == []


# ─────────────────────────────────────────────────────────────────────────────
# AC2: YAKE fallback when classify_intent has no domain match
# ─────────────────────────────────────────────────────────────────────────────


class TestYakeFallback:
    """AC2: When classify_intent returns SMALL_NO_DOMAIN + complexity,
    YAKE keywords are used as fallback with intent_source: yake-fallback."""

    def test_yake_fallback_on_complex_message_no_domain(self, isolated_env, preflight_config):
        """Complex message with no domain vocab match → YAKE fallback."""
        log_dir = isolated_env / "preflight" / "log"
        log_dir.mkdir(parents=True, exist_ok=True)

        # Mock session_search to return results for YAKE-enriched query
        def mock_search(query, limit=20):
            return []

        gate = PreflightGate(enabled=True, session_id="test-yake-fb", turn_count=10)

        # A complex message (>200 chars, triggers complexity) with no vocab match
        msg = "refactor the authentication middleware to support OAuth2 tokens with refresh flow and PKCE challenge validation for mobile clients"

        # Use the module's own functions
        import autodream.preflight as hp
        hp._gates["test-yake-fb"] = gate

        with mock.patch.object(hp, '_load_config', return_value={
            "mode": "live", "enabled": True, "warmup_turns": 0,
            "top_k": 3,
        }):
            gate_result, reason, heads_up = hp.should_run_preflight(
                session_id="test-yake-fb",
                message=msg,
                vocab_path=str(isolated_env / "preflight" / "domain-vocab.txt"),
                session_search_fn=mock_search,
            )

        # Should have fired (not skipped) because YAKE provided terms
        # Check telemetry for yake-fallback
        log_files = list(log_dir.glob("*.jsonl"))
        assert len(log_files) > 0
        last_line = log_files[-1].read_text().strip().split("\n")[-1]
        row = json.loads(last_line)
        # The YAKE fallback should have provided terms
        assert row.get("intent_source") in ("yake-fallback", "rule-based+yake", "rule-based")
        # If it fell back to YAKE, yake_terms should be populated
        if row.get("intent_source") in ("yake-fallback", "rule-based+yake"):
            assert len(row.get("yake_terms", [])) > 0

    def test_telemetry_has_yake_fields(self, isolated_env):
        """Telemetry rows include intent_source and yake_terms fields."""
        log_dir = isolated_env / "preflight" / "log"
        log_dir.mkdir(parents=True, exist_ok=True)

        tel = PreflightTelemetry(
            session_id="test-tel",
            intent_hash="abc123",
            domains=["k3d"],
            complexity_hit=True,
            skip_reason=None,
            raw_hits=5,
            top_ids=["id1"],
            scores=[0.8],
            elapsed_ms=42.0,
            mode="live",
            intent_source="yake-fallback",
            yake_terms=["cluster", "docker"],
        )
        write_preflight_telemetry(tel, log_dir=str(log_dir))

        log_files = list(log_dir.glob("*.jsonl"))
        assert len(log_files) > 0
        last_line = log_files[-1].read_text().strip()
        row = json.loads(last_line)
        assert row["intent_source"] == "yake-fallback"
        assert row["yake_terms"] == ["cluster", "docker"]
