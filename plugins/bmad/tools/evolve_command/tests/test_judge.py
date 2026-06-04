"""Tests for judge.py."""

from __future__ import annotations

from pathlib import Path

import pytest

# Ensure the package is importable
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from judge import (
    METRIC_WEIGHTS,
    HardGateResult,
    JudgeScore,
    check_hard_gates,
    compute_composite,
    load_metric_formula,
    _parse_test_pass_rate,
    _estimate_regression_safety,
    _clamp,
)


# ── Hard gate tests ───────────────────────────────────────────────────────

class TestHardGates:
    """Test the 4 hard gates."""

    def test_all_gates_pass(self) -> None:
        """Clean diff with passing tests should pass all gates."""
        result = check_hard_gates(
            diff="--- a/foo.py\n+++ b/foo.py\n-old\n+new",
            test_pass_rate=1.0,
            regression_safety=1.0,
        )
        assert result.passed is True
        assert len(result.failures) == 0

    def test_gate1_test_pass_below_threshold(self) -> None:
        """Gate 1: test_pass_rate must be >= 0.7."""
        result = check_hard_gates(
            diff="clean diff",
            test_pass_rate=0.5,
            regression_safety=1.0,
        )
        assert result.passed is False
        assert any("test_pass_rate" in f for f in result.failures)

    def test_gate1_test_pass_at_boundary(self) -> None:
        """Gate 1: test_pass_rate == 0.7 should pass."""
        result = check_hard_gates(
            diff="clean diff",
            test_pass_rate=0.7,
            regression_safety=1.0,
        )
        assert result.passed is True

    def test_gate2_regression_not_one(self) -> None:
        """Gate 2: regression_safety must be exactly 1.0."""
        result = check_hard_gates(
            diff="clean diff",
            test_pass_rate=1.0,
            regression_safety=0.0,
        )
        assert result.passed is False
        assert any("regression_safety" in f for f in result.failures)

    def test_gate3_deploy_verb(self) -> None:
        """Gate 3: no deploy verbs allowed in diff."""
        result = check_hard_gates(
            diff="kubectl apply -f deployment.yaml",
            test_pass_rate=1.0,
            regression_safety=1.0,
        )
        assert result.passed is False
        assert any("deploy verb" in f for f in result.failures)

    def test_gate3_no_deploy_verb(self) -> None:
        """Clean diff should not trigger deploy gate."""
        result = check_hard_gates(
            diff="--- a/foo.py\n+++ b/foo.py\ndef hello():\n    pass",
            test_pass_rate=1.0,
            regression_safety=1.0,
        )
        assert result.passed is True

    def test_gate4_credential_path(self) -> None:
        """Gate 4: no credential paths allowed."""
        result = check_hard_gates(
            diff="cp ~/.aws/credentials /tmp/backup",
            test_pass_rate=1.0,
            regression_safety=1.0,
        )
        assert result.passed is False
        assert any("credential" in f for f in result.failures)

    def test_gate4_api_key_pattern(self) -> None:
        """Gate 4: api_key= pattern should be caught."""
        result = check_hard_gates(
            diff="api_key=sk-1234567890",
            test_pass_rate=1.0,
            regression_safety=1.0,
        )
        assert result.passed is False

    def test_multiple_failures(self) -> None:
        """Multiple gates can fail simultaneously."""
        result = check_hard_gates(
            diff="kubectl apply -f deploy.yaml && cp ~/.aws/credentials .",
            test_pass_rate=0.3,
            regression_safety=0.0,
        )
        assert result.passed is False
        assert len(result.failures) >= 3


# ── Composite scoring ─────────────────────────────────────────────────────

class TestComposite:
    """Test composite score computation."""

    def test_perfect_score(self) -> None:
        """All 1.0 metrics should give composite 1.0."""
        score = JudgeScore(
            test_pass_rate=1.0,
            scope_discipline=1.0,
            spec_faithfulness=1.0,
            regression_safety=1.0,
            brevity=1.0,
        )
        assert compute_composite(score) == pytest.approx(1.0)

    def test_zero_score(self) -> None:
        """All 0.0 metrics should give composite 0.0."""
        score = JudgeScore(
            test_pass_rate=0.0,
            scope_discipline=0.0,
            spec_faithfulness=0.0,
            regression_safety=0.0,
            brevity=0.0,
        )
        assert compute_composite(score) == pytest.approx(0.0)

    def test_weights_sum_to_one(self) -> None:
        """Weights should sum to 1.0."""
        total = sum(METRIC_WEIGHTS.values())
        assert total == pytest.approx(1.0)

    def test_custom_weights(self) -> None:
        """Custom weights should override defaults."""
        score = JudgeScore(
            test_pass_rate=1.0,
            scope_discipline=0.0,
            spec_faithfulness=0.0,
            regression_safety=0.0,
            brevity=0.0,
        )
        # With all weight on test_pass_rate
        custom = {"test_pass_rate": 1.0, "scope_discipline": 0.0,
                  "spec_faithfulness": 0.0, "regression_safety": 0.0, "brevity": 0.0}
        assert compute_composite(score, custom) == pytest.approx(1.0)

    def test_test_pass_rate_dominates(self) -> None:
        """test_pass_rate has weight 0.4, so it dominates."""
        score = JudgeScore(
            test_pass_rate=1.0,
            scope_discipline=0.0,
            spec_faithfulness=0.0,
            regression_safety=0.0,
            brevity=0.0,
        )
        assert compute_composite(score) == pytest.approx(0.4)


# ── Metric formula loading ────────────────────────────────────────────────

class TestMetricFormula:
    """Test metric formula loading from YAML."""

    def test_default_weights(self) -> None:
        """load_metric_formula with no file should return defaults."""
        weights = load_metric_formula(None)
        assert weights == METRIC_WEIGHTS

    def test_load_from_yaml(self, tmp_path: Path) -> None:
        """Should load weights from YAML file."""
        import yaml

        yaml_path = tmp_path / "test_metrics.yaml"
        yaml_path.write_text(yaml.dump({
            "weights": {
                "test_pass_rate": 0.5,
                "scope_discipline": 0.2,
                "spec_faithfulness": 0.1,
                "regression_safety": 0.1,
                "brevity": 0.1,
            }
        }))
        weights = load_metric_formula(yaml_path)
        assert weights["test_pass_rate"] == 0.5

    def test_missing_yaml_returns_defaults(self, tmp_path: Path) -> None:
        """Non-existent YAML should return defaults."""
        weights = load_metric_formula(tmp_path / "nonexistent.yaml")
        assert weights == METRIC_WEIGHTS


# ── Helper function tests ─────────────────────────────────────────────────

class TestParseTestPassRate:
    """Test _parse_test_pass_rate."""

    def test_pytest_format(self) -> None:
        """Should parse pytest-style output."""
        assert _parse_test_pass_rate("5 passed, 1 failed") == pytest.approx(5 / 6)

    def test_all_passed(self) -> None:
        assert _parse_test_pass_rate("10 passed") == pytest.approx(1.0)

    def test_all_failed(self) -> None:
        assert _parse_test_pass_rate("3 failed") == pytest.approx(0.0)

    def test_pass_fail_lines(self) -> None:
        """Should count PASS/FAIL lines."""
        text = "PASS test_foo\nPASS test_bar\nFAIL test_baz"
        assert _parse_test_pass_rate(text) == pytest.approx(2 / 3)

    def test_empty_input(self) -> None:
        assert _parse_test_pass_rate("") == 0.0

    def test_no_tests(self) -> None:
        assert _parse_test_pass_rate("no test output here") == 0.5


class TestEstimateRegressionSafety:
    """Test _estimate_regression_safety."""

    def test_clean_diff(self) -> None:
        assert _estimate_regression_safety("--- a/foo\n+++ b/foo\n+new line") == 1.0

    def test_deleted_assert(self) -> None:
        assert _estimate_regression_safety("-assert x == 1") == 0.0

    def test_deleted_test_function(self) -> None:
        assert _estimate_regression_safety("-def test_something():") == 0.0

    def test_empty_diff(self) -> None:
        assert _estimate_regression_safety("") == 1.0

    def test_addition_only(self) -> None:
        assert _estimate_regression_safety("+new code here") == 1.0


class TestClamp:
    """Test _clamp."""

    def test_in_range(self) -> None:
        assert _clamp(0.5) == 0.5

    def test_below_range(self) -> None:
        assert _clamp(-0.5) == 0.0

    def test_above_range(self) -> None:
        assert _clamp(1.5) == 1.0

    def test_string_input(self) -> None:
        assert _clamp("0.7") == 0.7

    def test_invalid_string(self) -> None:
        assert _clamp("not a number") == 0.5

    def test_custom_bounds(self) -> None:
        assert _clamp(5, lo=0, hi=10) == 5


# ── Frozen dataclass tests ────────────────────────────────────────────────

class TestFrozenDataclasses:
    """Test that dataclasses are frozen."""

    def test_hard_gate_result_frozen(self) -> None:
        result = HardGateResult(passed=True, failures=())
        with pytest.raises(AttributeError):
            result.passed = False  # type: ignore[misc]

    def test_judge_score_frozen(self) -> None:
        score = JudgeScore()
        with pytest.raises(AttributeError):
            score.composite = 1.0  # type: ignore[misc]
