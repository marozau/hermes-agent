"""Tests for metrics/code_review_convergence_v1.yaml — FROZEN metric definition.

Validates YAML structure, weight invariants, and hard-gate schemas for the
code-review convergence metric used in L-29/D-44 training-data generation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

# Ensure the package is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Paths ───────────────────────────────────────────────────────────────

_METRIC_YAML = (
    Path(__file__).resolve().parent.parent / "metrics" / "code_review_convergence_v1.yaml"
)


# ── Helpers ─────────────────────────────────────────────────────────────


def _load_metric() -> dict:
    """Load and return the YAML as a plain dict."""
    if not _METRIC_YAML.exists():
        pytest.fail(f"Metric YAML not found: {_METRIC_YAML}")
    data = yaml.safe_load(_METRIC_YAML.read_text())
    assert isinstance(data, dict), "YAML root must be a mapping"
    return data


# ── Tests ───────────────────────────────────────────────────────────────


class TestCodeReviewConvergenceYAML:
    """Structural invariants for the FROZEN metric YAML."""

    def test_yaml_loads_and_has_required_keys(self) -> None:
        """YAML must parse and contain version, name, freeze_date, weights."""
        data = _load_metric()
        assert data["version"] == 1
        assert data["name"] == "code_review_convergence_v1"
        assert data["freeze_date"] == "2026-06-05"
        assert "weights" in data
        assert "hard_gates" in data

    def test_weights_sum_to_one(self) -> None:
        """All weights must sum to 1.0 (within floating-point tolerance)."""
        data = _load_metric()
        weights: dict[str, float] = data["weights"]
        total = sum(weights.values())
        assert total == pytest.approx(1.0), f"Weights sum to {total}, expected 1.0"

    def test_expected_weight_keys(self) -> None:
        """Must have exactly the three expected weight dimensions."""
        data = _load_metric()
        weights: dict[str, float] = data["weights"]
        assert set(weights.keys()) == {"p0_drop_rate", "fix_round_efficiency", "nit_introduction_rate"}

    def test_weight_values_match_spec(self) -> None:
        """Individual weight values must match the FROZEN spec."""
        data = _load_metric()
        weights: dict[str, float] = data["weights"]
        assert weights["p0_drop_rate"] == pytest.approx(0.5)
        assert weights["fix_round_efficiency"] == pytest.approx(0.3)
        assert weights["nit_introduction_rate"] == pytest.approx(0.2)

    def test_hard_gates_non_empty(self) -> None:
        """Must declare at least one hard gate."""
        data = _load_metric()
        gates = data["hard_gates"]
        assert isinstance(gates, list)
        assert len(gates) >= 1

    def test_hard_gates_have_name_and_op(self) -> None:
        """Every hard gate must have 'name' and 'op' keys."""
        data = _load_metric()
        for gate in data["hard_gates"]:
            assert "name" in gate, f"Gate missing 'name': {gate}"
            assert "op" in gate, f"Gate missing 'op': {gate}"

    def test_freeze_date_is_iso_format(self) -> None:
        """Freeze date must be valid ISO-8601 date string."""
        data = _load_metric()
        from datetime import date

        # Should parse without raising
        parsed = date.fromisoformat(data["freeze_date"])
        assert parsed.isoformat() == data["freeze_date"]


class TestCodeReviewConvergenceMetricComputation:
    """Test the weighted-composite scoring logic for code-review convergence."""

    def _compute_score(
        self,
        p0_drop_rate: float = 1.0,
        fix_round_efficiency: float = 1.0,
        nit_introduction_rate: float = 1.0,
    ) -> float:
        """Compute weighted composite using the FROZEN weights."""
        data = _load_metric()
        w = data["weights"]
        return (
            w["p0_drop_rate"] * p0_drop_rate
            + w["fix_round_efficiency"] * fix_round_efficiency
            + w["nit_introduction_rate"] * nit_introduction_rate
        )

    def test_perfect_convergence_scores_one(self) -> None:
        """All metrics at 1.0 → composite score == 1.0."""
        score = self._compute_score(1.0, 1.0, 1.0)
        assert score == pytest.approx(1.0)

    def test_zero_convergence_scores_zero(self) -> None:
        """All metrics at 0.0 → composite score == 0.0."""
        score = self._compute_score(0.0, 0.0, 0.0)
        assert score == pytest.approx(0.0)

    def test_partial_convergence_weighted_correctly(self) -> None:
        """Only p0_drop_rate at 1.0 → score == 0.5 (its weight)."""
        score = self._compute_score(1.0, 0.0, 0.0)
        assert score == pytest.approx(0.5)

    def test_score_bounded_zero_to_one(self) -> None:
        """Score must always be in [0.0, 1.0]."""
        score = self._compute_score(0.3, 0.7, 0.9)
        assert 0.0 <= score <= 1.0
