"""Tests for adapters/metric_adapter.py — GEPA metric adapter."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the package is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.metric_adapter import (
    MetricRubric,
    dev_story_composite_v1_metric,
    load_rubric,
)

# ── Shared fixtures ─────────────────────────────────────────────────────

_CLEAN_DIFF = (
    "--- a/foo.py\n"
    "+++ b/foo.py\n"
    "@@ -1 +1,2 @@\n"
    " def hello():\n"
    "-    pass\n"
    "+    return 'world'\n"
)

_PASSING_TESTS = "5 passed, 0 failed"

# Load the real rubric once for the module
_RUBRIC = load_rubric()


def _make_candidate(
    diff: str = _CLEAN_DIFF,
    test_results: str = _PASSING_TESTS,
    scope_discipline: float = 0.8,
    spec_faithfulness: float = 0.9,
    brevity: float = 0.7,
    story_spec: str = "Implement hello world",
    project_context: str = "lang: python",
) -> dict:
    """Helper to build a candidate dict with sensible defaults."""
    return {
        "story_spec": story_spec,
        "diff": diff,
        "test_results": test_results,
        "project_context": project_context,
        "scope_discipline": scope_discipline,
        "spec_faithfulness": spec_faithfulness,
        "brevity": brevity,
    }


# ── Tests ───────────────────────────────────────────────────────────────


class TestLoadRubric:
    """Test rubric loading from FROZEN YAML + locked prompts."""

    def test_loads_default_rubric(self) -> None:
        """Should load rubric from shipped files without error."""
        rubric = load_rubric()
        assert rubric.name == "dev_story_composite_v1"
        assert rubric.version == 1
        assert rubric.freeze_date == "2026-06-05"
        assert len(rubric.weights) == 5
        assert sum(rubric.weights.values()) == pytest.approx(1.0)

    def test_scope_prompt_loaded(self) -> None:
        """Scope discipline prompt should be non-empty and locked."""
        rubric = load_rubric()
        assert "scope_discipline" in rubric.scope_prompt.lower()
        assert "FROZEN" in rubric.scope_prompt

    def test_faithfulness_prompt_loaded(self) -> None:
        """Spec faithfulness prompt should be non-empty and locked."""
        rubric = load_rubric()
        assert "spec_faithfulness" in rubric.faithfulness_prompt.lower()
        assert "FROZEN" in rubric.faithfulness_prompt

    def test_hard_gates_tuple(self) -> None:
        """Hard gates should be a tuple with at least 4 entries."""
        rubric = load_rubric()
        assert len(rubric.hard_gates) >= 4

    def test_missing_yaml_raises(self, tmp_path: Path) -> None:
        """Missing YAML file should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_rubric(yaml_path=tmp_path / "nonexistent.yaml")


class TestMetricCallable:
    """Test the dev_story_composite_v1_metric GEPA callable."""

    def test_passing_candidate_returns_positive(self) -> None:
        """A clean candidate with passing tests should score > 0."""
        score = dev_story_composite_v1_metric(_make_candidate(), _RUBRIC)
        assert 0.0 < score <= 1.0

    def test_hard_gate_fail_returns_zero(self) -> None:
        """Deploy verb in diff should trigger hard gate → 0.0."""
        candidate = _make_candidate(
            diff="kubectl apply -f deployment.yaml",
        )
        score = dev_story_composite_v1_metric(candidate, _RUBRIC)
        assert score == 0.0

    def test_low_test_pass_returns_zero(self) -> None:
        """test_pass_rate < 0.7 triggers hard gate → 0.0."""
        candidate = _make_candidate(test_results="1 passed, 9 failed")
        score = dev_story_composite_v1_metric(candidate, _RUBRIC)
        assert score == 0.0

    def test_credential_path_returns_zero(self) -> None:
        """Credential path in diff triggers hard gate → 0.0."""
        candidate = _make_candidate(
            diff="cat ~/.aws/credentials > /tmp/creds",
        )
        score = dev_story_composite_v1_metric(candidate, _RUBRIC)
        assert score == 0.0

    def test_perfect_candidate_scores_one(self) -> None:
        """All-1.0 subjective scores + clean diff + all tests pass → 1.0."""
        candidate = _make_candidate(
            diff="--- a/foo.py\n+++ b/foo.py\n+new line\n",
            test_results="10 passed",
            scope_discipline=1.0,
            spec_faithfulness=1.0,
            brevity=1.0,
        )
        score = dev_story_composite_v1_metric(candidate, _RUBRIC)
        assert score == pytest.approx(1.0)

    def test_defaults_without_subjective_keys(self) -> None:
        """Missing subjective keys should use 0.5 defaults."""
        candidate: dict = {
            "story_spec": "spec",
            "diff": _CLEAN_DIFF,
            "test_results": _PASSING_TESTS,
            "project_context": "ctx",
        }
        score = dev_story_composite_v1_metric(candidate, _RUBRIC)
        # 0.4*1.0 + 0.2*0.5 + 0.2*0.5 + 0.1*1.0 + 0.1*0.5 = 0.75
        assert score == pytest.approx(0.75)

    def test_returns_float(self) -> None:
        """Return type must be float."""
        score = dev_story_composite_v1_metric(_make_candidate(), _RUBRIC)
        assert isinstance(score, float)


class TestMetricRubricFrozen:
    """Verify MetricRubric is frozen/immutable."""

    def test_cannot_mutate_weights(self) -> None:
        """Frozen dataclass should reject attribute assignment."""
        rubric = load_rubric()
        with pytest.raises(AttributeError):
            rubric.weights = {}  # type: ignore[misc]

    def test_cannot_mutate_name(self) -> None:
        """Frozen dataclass should reject attribute assignment."""
        rubric = load_rubric()
        with pytest.raises(AttributeError):
            rubric.name = "changed"  # type: ignore[misc]
