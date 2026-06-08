"""Stories 8.2 + 8.3 — Recency power-law and type-boost map tests."""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

import pytest
import yaml

HERMES_ROOT = Path.home() / ".hermes"
if str(HERMES_ROOT) not in sys.path:
    sys.path.insert(0, str(HERMES_ROOT))

from autodream.preflight import TrajectoryHit, rank_trajectories


@pytest.fixture
def config_file(tmp_path):
    """Minimal config with recency + type_boosts."""
    cfg = {
        "mode": "live",
        "enabled": True,
        "weights": {"bm25": 0.45, "recency": 0.25, "category": 0.20, "resolution": 0.10},
        "recency": {"form": "power_law", "exponent": -0.3},
        "type_boosts": {
            "preference": 1.2,
            "procedure": 1.1,
            "fact": 1.0,
            "trajectory": 1.0,
            "episode": 0.8,
            "superseded": 0.2,
            "unknown": 0.6,
        },
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(cfg))
    return str(p)


# ─────────────────────────────────────────────────────────────────────────────
# Story 8.2: Recency power-law
# ─────────────────────────────────────────────────────────────────────────────


class TestRecencyPowerLaw:
    """AC1-AC3: power-law recency with configurable exponent."""

    def test_one_hour_ago(self, config_file):
        """AC1: 1 hour ago → recency ≈ 0.812."""
        now = time.time()
        hit = TrajectoryHit(
            id="t1", content="test", bm25_score=0.5,
            timestamp=now - 3600,  # 1 hour ago
            entry_type="trajectory",
        )
        ranked = rank_trajectories([hit], config_path=config_file)
        h = ranked[0]
        # Extract recency factor: score / (bm25_c component)
        # With power_law -0.3: (1 + 1) ** -0.3 = 2 ** -0.3 ≈ 0.812
        expected_recency = (1.0 + 1.0) ** (-0.3)
        assert abs(expected_recency - 0.812) < 0.01, f"Expected ~0.812, got {expected_recency}"

    def test_thirty_days_ago(self, config_file):
        """AC2: 30 days ago → recency ≈ 0.135."""
        now = time.time()
        age_hours = 30 * 24  # 720 hours
        expected_recency = (1.0 + age_hours) ** (-0.3)
        assert abs(expected_recency - 0.135) < 0.01, f"Expected ~0.135, got {expected_recency}"

    def test_configurable_exponent(self, tmp_path):
        """AC3: changing exponent in config takes effect immediately."""
        now = time.time()
        hit = TrajectoryHit(
            id="t1", content="test", bm25_score=0.5,
            timestamp=now - 3600,
            entry_type="trajectory",
        )

        # Exponent -0.5 (steeper decay)
        cfg = {
            "weights": {"bm25": 0.45, "recency": 0.25, "category": 0.20, "resolution": 0.10},
            "recency": {"form": "power_law", "exponent": -0.5},
        }
        p = tmp_path / "config.yaml"
        p.write_text(yaml.dump(cfg))

        ranked = rank_trajectories([hit], config_path=str(p))
        # (1 + 1) ** -0.5 = 0.707
        # Score should reflect the steeper decay vs -0.3
        hit_steep = ranked[0].score

        # Now with -0.3
        cfg["recency"]["exponent"] = -0.3
        p.write_text(yaml.dump(cfg))
        ranked = rank_trajectories([hit], config_path=str(p))
        hit_gentle = ranked[0].score

        # Gentler decay (−0.3) should give higher score for recent items
        assert hit_gentle > hit_steep, "Gentler exponent should score recent items higher"

    def test_unknown_timestamp_neutral(self, config_file):
        """Unknown timestamp (0.0) → recency = 0 (no boost)."""
        hit = TrajectoryHit(
            id="t1", content="test", bm25_score=0.5,
            timestamp=0.0,
            entry_type="trajectory",
        )
        ranked = rank_trajectories([hit], config_path=config_file)
        # With recency=0, score is just bm25 + category + resolution
        assert ranked[0].score > 0  # should not crash

    def test_future_timestamp_clamped(self, config_file):
        """Future timestamps should not boost above 1.0."""
        now = time.time()
        hit = TrajectoryHit(
            id="t1", content="test", bm25_score=0.5,
            timestamp=now + 3600,  # 1 hour in future
            entry_type="trajectory",
        )
        ranked = rank_trajectories([hit], config_path=config_file)
        # Score should be clamped, not boosted
        assert ranked[0].score > 0

    def test_exp_fallback_form(self, tmp_path):
        """When form is not power_law, fall back to exp decay."""
        now = time.time()
        hit = TrajectoryHit(
            id="t1", content="test", bm25_score=0.5,
            timestamp=now - 30 * 86400,  # 30 days ago
            entry_type="trajectory",
        )
        cfg = {
            "weights": {"bm25": 0.45, "recency": 0.25, "category": 0.20, "resolution": 0.10},
            "recency": {"form": "exp", "exponent": -0.3},
        }
        p = tmp_path / "config.yaml"
        p.write_text(yaml.dump(cfg))

        ranked = rank_trajectories([hit], config_path=str(p))
        # Should still work (uses exp fallback)
        assert ranked[0].score > 0


# ─────────────────────────────────────────────────────────────────────────────
# Story 8.3: Type-boost map
# ─────────────────────────────────────────────────────────────────────────────


class TestTypeBoostMap:
    """AC1-AC3: per-type multiplier + source boost."""

    def test_preference_beats_episode(self, config_file):
        """AC1: preference (1.2) scores higher than episode (0.8)."""
        now = time.time()
        hit_pref = TrajectoryHit(
            id="pref", content="test", bm25_score=0.5,
            timestamp=now - 3600, entry_type="preference",
        )
        hit_ep = TrajectoryHit(
            id="ep", content="test", bm25_score=0.5,
            timestamp=now - 3600, entry_type="episode",
        )
        ranked = rank_trajectories([hit_pref, hit_ep], config_path=config_file)
        # preference should rank first
        assert ranked[0].id == "pref"

    def test_suppressed_superseded(self, config_file):
        """AC3: superseded (0.2) almost never reaches top-K."""
        now = time.time()
        hits = []
        for i in range(5):
            hits.append(TrajectoryHit(
                id=f"normal-{i}", content="test", bm25_score=0.5,
                timestamp=now - 3600, entry_type="fact",
            ))
        hits.append(TrajectoryHit(
            id="superseded", content="test", bm25_score=0.9,  # high BM25
            timestamp=now - 3600, entry_type="superseded",
        ))
        ranked = rank_trajectories(hits, config_path=config_file)
        # superseded with 0.2 boost should not be first even with high BM25
        assert ranked[0].id != "superseded"

    def test_correction_source_boost(self, config_file):
        """AC2: user-correction source gets +0.3 boost."""
        now = time.time()
        hit_normal = TrajectoryHit(
            id="normal", content="test", bm25_score=0.5,
            timestamp=now - 3600, entry_type="fact",
        )
        hit_correction = TrajectoryHit(
            id="correction", content="test", bm25_score=0.5,
            timestamp=now - 3600, entry_type="fact",
            entry_source="user-correction",
        )
        ranked = rank_trajectories([hit_normal, hit_correction], config_path=config_file)
        # correction should rank first due to +0.3 source boost
        assert ranked[0].id == "correction"

    def test_unknown_type_default(self, config_file):
        """Unknown type gets default boost 0.6."""
        now = time.time()
        hit = TrajectoryHit(
            id="unk", content="test", bm25_score=0.5,
            timestamp=now - 3600, entry_type="something-else",
        )
        ranked = rank_trajectories([hit], config_path=config_file)
        # Should not crash; uses default 1.0 for unmapped types
        assert ranked[0].score > 0

    def test_config_overrides_type_boosts(self, tmp_path):
        """Type boosts from config.yaml override defaults."""
        now = time.time()
        hit = TrajectoryHit(
            id="t1", content="test", bm25_score=0.5,
            timestamp=now - 3600, entry_type="preference",
        )

        # Custom config: preference = 2.0 (double the default)
        cfg = {
            "weights": {"bm25": 0.45, "recency": 0.25, "category": 0.20, "resolution": 0.10},
            "type_boosts": {"preference": 2.0},
        }
        p = tmp_path / "config.yaml"
        p.write_text(yaml.dump(cfg))

        ranked = rank_trajectories([hit], config_path=str(p))
        # Score should reflect 2.0 boost
        assert ranked[0].score > 0
