"""Tests for Story 9.2 — Manifest-based dedup + Story 9.3 — Hit-rate report."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

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


def _add_trajectory(memory_dirs, body="docker build fails with exit code 137"):
    from autodream.memory import add_entry
    return add_entry(type="trajectory", body=body, source="test")


# ─────────────────────────────────────────────────────────────────────────────
# Story 9.2: build_manifest
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildManifest:

    def test_empty_when_no_entries(self, memory_dirs):
        from autodream.memory import build_manifest
        result = build_manifest()
        assert "(none)" in result

    def test_lists_trajectory_entries(self, memory_dirs):
        from autodream.memory import build_manifest
        eid = _add_trajectory(memory_dirs)
        result = build_manifest()
        assert eid in result
        assert "docker build fails" in result

    def test_caps_at_max_entries(self, memory_dirs):
        from autodream.memory import build_manifest
        # Add 55 trajectory entries
        for i in range(55):
            _add_trajectory(memory_dirs, body=f"trajectory-{i}")
        result = build_manifest(max_entries=50)
        # Count manifest lines (header + 50 entries)
        lines = [l for l in result.strip().split("\n") if l.startswith("[")]
        assert len(lines) == 50

    def test_only_includes_trajectory_type(self, memory_dirs):
        from autodream.memory import build_manifest, add_entry
        add_entry(type="fact", body="not a trajectory", source="test")
        eid = _add_trajectory(memory_dirs)
        result = build_manifest()
        assert eid in result
        assert "not a trajectory" not in result

    def test_sorted_by_last_used_at_desc(self, memory_dirs):
        from autodream.memory import build_manifest, _write_entry_file, _read_entry_file, _resolve_memory_dir
        e1 = _add_trajectory(memory_dirs, body="first entry")
        e2 = _add_trajectory(memory_dirs, body="second entry")
        # Make e1 more recently used
        mem_path = _resolve_memory_dir()
        fm, body = _read_entry_file(e1, mem_path)
        fm["last_used_at"] = datetime(2026, 6, 1, tzinfo=timezone.utc).isoformat()
        _write_entry_file(e1, fm, body, mem_path)
        result = build_manifest()
        # e1 should appear before e2
        idx_e1 = result.index(e1)
        idx_e2 = result.index(e2)
        assert idx_e1 < idx_e2

    def test_summary_truncated_to_80_chars(self, memory_dirs):
        from autodream.memory import build_manifest
        long_body = "x" * 200
        _add_trajectory(memory_dirs, body=long_body)
        result = build_manifest()
        # Each manifest line is [<id>] + summary
        lines = [l for l in result.strip().split("\n") if l.startswith("[")]
        assert len(lines) == 1
        summary_part = lines[0].split("] ", 1)[1]
        assert len(summary_part) <= 80


# ─────────────────────────────────────────────────────────────────────────────
# Story 9.2: classify_trajectory_with_manifest
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifyTrajectory:

    def test_reinforce_action(self, memory_dirs):
        from autodream.memory import classify_trajectory_with_manifest
        mock_result = {"content": '{"action": "reinforce", "id": "01ABC123"}'}
        with mock.patch("autodream.llm.llm_call", return_value=mock_result):
            result = classify_trajectory_with_manifest(
                "docker build fails", "MANIFEST:\n[01ABC123] docker build\n"
            )
        assert result["action"] == "reinforce"
        assert result["id"] == "01ABC123"

    def test_new_entry_action(self, memory_dirs):
        from autodream.memory import classify_trajectory_with_manifest
        mock_result = {"content": '{"action": "new", "type": "trajectory", "body": "k3d cluster timeout"}'}
        with mock.patch("autodream.llm.llm_call", return_value=mock_result):
            result = classify_trajectory_with_manifest(
                "k3d cluster timeout after 300s", "MANIFEST:\n(none)\n"
            )
        assert result["action"] == "new"
        assert result["type"] == "trajectory"
        assert result["body"] == "k3d cluster timeout"

    def test_malformed_json_returns_error(self, memory_dirs):
        from autodream.memory import classify_trajectory_with_manifest
        mock_result = {"content": "this is not json at all"}
        with mock.patch("autodream.llm.llm_call", return_value=mock_result):
            result = classify_trajectory_with_manifest(
                "test pattern", "MANIFEST:\n(none)\n"
            )
        assert result["action"] == "error"

    def test_empty_response_returns_error(self, memory_dirs):
        from autodream.memory import classify_trajectory_with_manifest
        mock_result = {"content": ""}
        with mock.patch("autodream.llm.llm_call", return_value=mock_result):
            result = classify_trajectory_with_manifest(
                "test pattern", "MANIFEST:\n(none)\n"
            )
        assert result["action"] == "error"

    def test_llm_failure_returns_error(self, memory_dirs):
        from autodream.memory import classify_trajectory_with_manifest
        with mock.patch("autodream.llm.llm_call", side_effect=RuntimeError("provider down")):
            result = classify_trajectory_with_manifest(
                "test pattern", "MANIFEST:\n(none)\n"
            )
        assert result["action"] == "error"

    def test_json_in_markdown_fences(self, memory_dirs):
        from autodream.memory import classify_trajectory_with_manifest
        mock_result = {"content": '```json\n{"action": "reinforce", "id": "01XYZ"}\n```'}
        with mock.patch("autodream.llm.llm_call", return_value=mock_result):
            result = classify_trajectory_with_manifest(
                "test", "MANIFEST:\n[01XYZ] some docker entry\n"
            )
        assert result["action"] == "reinforce"
        assert result["id"] == "01XYZ"

    def test_pydantic_rejects_invalid_action(self, memory_dirs):
        from autodream.memory import classify_trajectory_with_manifest
        mock_result = {"content": '{"action": "delete", "id": "01ABC"}'}
        with mock.patch("autodream.llm.llm_call", return_value=mock_result):
            result = classify_trajectory_with_manifest(
                "test", "MANIFEST:\n"
            )
        assert result["action"] == "error"

    def test_pydantic_rejects_missing_fields(self, memory_dirs):
        from autodream.memory import classify_trajectory_with_manifest
        mock_result = {"content": '{"action": "new"}'}
        with mock.patch("autodream.llm.llm_call", return_value=mock_result):
            result = classify_trajectory_with_manifest(
                "test", "MANIFEST:\n"
            )
        assert result["action"] == "error"


# ─────────────────────────────────────────────────────────────────────────────
# Story 9.3: build_hit_rate_report
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def preflight_log_dir(tmp_path):
    """Create a temporary preflight log dir with sample data."""
    log_dir = tmp_path / "preflight" / "log"
    log_dir.mkdir(parents=True)
    return log_dir


def _write_preflight_row(log_dir, date, session_id, intent_hash, category):
    """Write a preflight telemetry row."""
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "intent_hash": intent_hash,
        "category": category,
        "mode": "live",
    }
    path = log_dir / f"{date}.jsonl"
    line = json.dumps(row, sort_keys=True) + "\n"
    with open(path, "a") as f:
        f.write(line)


def _write_citation_row(log_dir, date, session_id, intent_hash, cited_ids):
    """Write a verify_citation event row."""
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "verify_citation",
        "session_id": session_id,
        "intent_hash": intent_hash,
        "cited_ids": cited_ids,
    }
    path = log_dir / f"{date}.jsonl"
    line = json.dumps(row, sort_keys=True) + "\n"
    with open(path, "a") as f:
        f.write(line)


class TestBuildHitRateReport:

    def test_empty_when_no_logs(self, preflight_log_dir):
        from autodream.memory import build_hit_rate_report
        result = build_hit_rate_report(preflight_log_dir=str(preflight_log_dir))
        assert result == []

    def test_filters_by_min_fires(self, preflight_log_dir):
        from autodream.memory import build_hit_rate_report
        # Only 5 fires for category "docker" — below default min_fires=20
        for i in range(5):
            _write_preflight_row(preflight_log_dir, "2026-05-25", f"s{i}", f"ih{i}", "docker")
        result = build_hit_rate_report(preflight_log_dir=str(preflight_log_dir))
        assert result == []

    def test_computes_hit_rate(self, preflight_log_dir):
        from autodream.memory import build_hit_rate_report
        # 25 fires for "k8s", 10 of which have citation hits
        for i in range(25):
            _write_preflight_row(preflight_log_dir, "2026-05-25", f"s{i}", f"ih{i}", "k8s")
        for i in range(10):
            _write_citation_row(preflight_log_dir, "2026-05-25", f"s{i}", f"ih{i}", [f"entry-{i}"])

        result = build_hit_rate_report(
            preflight_log_dir=str(preflight_log_dir), min_fires=20
        )
        assert len(result) == 1
        assert result[0]["category"] == "k8s"
        assert result[0]["n_fired"] == 25
        assert result[0]["n_matched_hit"] == 10
        assert result[0]["n_matched_miss"] == 15
        assert abs(result[0]["hit_rate"] - 0.4) < 0.01

    def test_sorted_by_hit_rate_ascending(self, preflight_log_dir):
        from autodream.memory import build_hit_rate_report
        # "good" category: high hit rate
        for i in range(25):
            _write_preflight_row(preflight_log_dir, "2026-05-25", f"sg{i}", f"ihg{i}", "good")
            if i < 20:
                _write_citation_row(preflight_log_dir, "2026-05-25", f"sg{i}", f"ihg{i}", ["e1"])
        # "bad" category: low hit rate
        for i in range(25):
            _write_preflight_row(preflight_log_dir, "2026-05-25", f"sb{i}", f"ihb{i}", "bad")

        result = build_hit_rate_report(
            preflight_log_dir=str(preflight_log_dir), min_fires=20
        )
        assert len(result) == 2
        assert result[0]["category"] == "bad"  # worst first
        assert result[1]["category"] == "good"

    def test_multi_day_aggregation(self, preflight_log_dir):
        from autodream.memory import build_hit_rate_report
        # Spread across 3 days
        for day in ["2026-05-25", "2026-05-26", "2026-05-27"]:
            for i in range(10):
                _write_preflight_row(preflight_log_dir, day, f"s{day}-{i}", f"ih{i}", "multi")
        # 30 total fires, all with hits on day 1
        for i in range(10):
            _write_citation_row(preflight_log_dir, "2026-05-25", f"s2026-05-25-{i}", f"ih{i}", ["e1"])

        result = build_hit_rate_report(
            preflight_log_dir=str(preflight_log_dir), min_fires=20
        )
        assert len(result) == 1
        assert result[0]["n_fired"] == 30
        assert result[0]["n_matched_hit"] == 10


# ─────────────────────────────────────────────────────────────────────────────
# Story 9.3: propose_category_weight_nudges
# ─────────────────────────────────────────────────────────────────────────────

class TestProposeCategoryWeightNudges:

    def test_low_hit_rate_nudge_down(self):
        from autodream.memory import propose_category_weight_nudges
        report = [{"category": "bad", "n_fired": 30, "n_matched_hit": 2, "n_matched_miss": 28, "hit_rate": 0.0667}]
        result = propose_category_weight_nudges(report)
        assert len(result["low_hit_rate"]) == 1
        assert result["low_hit_rate"][0]["category"] == "bad"
        assert result["low_hit_rate"][0]["action"] == "nudge_down"
        assert len(result["high_hit_rate"]) == 0
        assert len(result["blind_spots"]) == 0

    def test_high_hit_rate_nudge_up(self):
        from autodream.memory import propose_category_weight_nudges
        report = [{"category": "good", "n_fired": 30, "n_matched_hit": 20, "n_matched_miss": 10, "hit_rate": 0.6667}]
        result = propose_category_weight_nudges(report)
        assert len(result["high_hit_rate"]) == 1
        assert result["high_hit_rate"][0]["category"] == "good"
        assert result["high_hit_rate"][0]["action"] == "nudge_up"

    def test_blind_spot_detection(self):
        from autodream.memory import propose_category_weight_nudges
        report = [{
            "category": "unknown-domain",
            "n_fired": 30,
            "n_matched_hit": 0,
            "n_matched_miss": 25,
            "hit_rate": 0.0,
        }]
        result = propose_category_weight_nudges(report)
        assert len(result["blind_spots"]) == 1
        assert result["blind_spots"][0]["category"] == "unknown-domain"
        assert result["blind_spots"][0]["action"] == "add_vocab_candidate"

    def test_normal_category_no_nudge(self):
        from autodream.memory import propose_category_weight_nudges
        report = [{"category": "ok", "n_fired": 30, "n_matched_hit": 8, "n_matched_miss": 22, "hit_rate": 0.2667}]
        result = propose_category_weight_nudges(report)
        assert len(result["low_hit_rate"]) == 0
        assert len(result["high_hit_rate"]) == 0
        assert len(result["blind_spots"]) == 0

    def test_custom_thresholds(self):
        from autodream.memory import propose_category_weight_nudges
        report = [{"category": "marginal", "n_fired": 30, "n_matched_hit": 5, "n_matched_miss": 25, "hit_rate": 0.1667}]
        # With higher low_threshold, this should trigger nudge_down
        result = propose_category_weight_nudges(report, low_threshold=0.2)
        assert len(result["low_hit_rate"]) == 1

    def test_blind_spot_overrides_low(self):
        """Blind spot check happens first; if both conditions met, it's a blind spot, not low."""
        from autodream.memory import propose_category_weight_nudges
        report = [{
            "category": "very-bad",
            "n_fired": 30,
            "n_matched_hit": 0,
            "n_matched_miss": 25,
            "hit_rate": 0.0,
        }]
        result = propose_category_weight_nudges(report)
        # Should be blind spot, not low_hit_rate
        assert len(result["blind_spots"]) == 1
        assert len(result["low_hit_rate"]) == 0
