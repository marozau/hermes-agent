"""Tests for skillopt_benchmark.py — BMADDevStoryEnv SkillOpt benchmark (Story 15.8).

Tests ≥2 critical paths:
1. Reward function (composite metric) — hard gates + weighted scoring.
2. Environment adapter lifecycle — build_train_env, rollout, get_task_types.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure the package is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from skillopt_benchmark import (
    BMADDevStoryDataLoader,
    BMADDevStoryEnv,
    _check_hard_gates,
    _estimate_regression_safety,
    _parse_test_pass_rate,
    compute_reward,
)


# ── Helpers ─────────────────────────────────────────────────────────────

def _make_items(n: int = 4) -> list[dict]:
    """Build n synthetic dev-story task items."""
    return [
        {
            "id": f"task-{i}",
            "story_spec": f"## Story {i}\nImplement feature {i}.",
            "project_context": f"name: project-{i}\nlang: python",
            "test_results": "5 passed, 0 failed" if i % 2 == 0 else "3 passed, 2 failed",
            "task_type": "dev-story",
        }
        for i in range(n)
    ]


# ── Tests: Reward function ──────────────────────────────────────────────

class TestParseTestPassRate:
    """Verify pytest output parsing."""

    def test_all_passing(self) -> None:
        assert _parse_test_pass_rate("10 passed, 0 failed") == 1.0

    def test_all_failing(self) -> None:
        assert _parse_test_pass_rate("0 passed, 5 failed") == 0.0

    def test_mixed(self) -> None:
        assert _parse_test_pass_rate("3 passed, 2 failed") == pytest.approx(0.6)

    def test_empty(self) -> None:
        assert _parse_test_pass_rate("") == 0.0

    def test_pass_fail_lines(self) -> None:
        output = "PASS test_foo\nPASS test_bar\nFAIL test_baz"
        assert _parse_test_pass_rate(output) == pytest.approx(2 / 3)


class TestEstimateRegressionSafety:
    """Verify diff-based regression detection."""

    def test_empty_diff_is_safe(self) -> None:
        assert _estimate_regression_safety("") == 1.0

    def test_no_deletions_is_safe(self) -> None:
        diff = "+added line\n+another added line"
        assert _estimate_regression_safety(diff) == 1.0

    def test_deleted_assert_is_unsafe(self) -> None:
        diff = "-assert result == expected\n+assert result == new_expected"
        assert _estimate_regression_safety(diff) == 0.0

    def test_deleted_test_keyword_is_unsafe(self) -> None:
        diff = "-def test_something():\n+def test_replacement():"
        assert _estimate_regression_safety(diff) == 0.0

    def test_non_assert_deletion_is_safe(self) -> None:
        diff = "-old_function()\n+new_function()"
        assert _estimate_regression_safety(diff) == 1.0


class TestCheckHardGates:
    """Verify the 4 hard gates."""

    def test_all_gates_pass(self) -> None:
        passed, failures = _check_hard_gates(
            diff="+new line",
            test_pass_rate=1.0,
            regression_safety=1.0,
        )
        assert passed is True
        assert failures == ()

    def test_low_test_rate_fails(self) -> None:
        passed, failures = _check_hard_gates(
            diff="+new line",
            test_pass_rate=0.5,
            regression_safety=1.0,
        )
        assert passed is False
        assert any("test_pass_rate" in f for f in failures)

    def test_regression_fails(self) -> None:
        passed, failures = _check_hard_gates(
            diff="+new line",
            test_pass_rate=1.0,
            regression_safety=0.0,
        )
        assert passed is False
        assert any("regression_safety" in f for f in failures)

    def test_deploy_verb_fails(self) -> None:
        passed, failures = _check_hard_gates(
            diff="+kubectl apply -f deploy.yaml",
            test_pass_rate=1.0,
            regression_safety=1.0,
        )
        assert passed is False
        assert any("deploy verb" in f for f in failures)

    def test_credential_path_fails(self) -> None:
        passed, failures = _check_hard_gates(
            diff="+cp ~/.aws/credentials /tmp",
            test_pass_rate=1.0,
            regression_safety=1.0,
        )
        assert passed is False
        assert any("credential path" in f for f in failures)

    def test_multiple_failures_collected(self) -> None:
        passed, failures = _check_hard_gates(
            diff="+kubectl apply -f deploy.yaml",
            test_pass_rate=0.3,
            regression_safety=0.0,
        )
        assert passed is False
        assert len(failures) >= 3  # test_rate + regression + deploy_verb


class TestComputeReward:
    """Verify the composite reward function."""

    def test_perfect_candidate(self) -> None:
        """All metrics 1.0 → composite = 1.0."""
        candidate = {
            "diff": "+new line",
            "test_results": "10 passed, 0 failed",
            "scope_discipline": 1.0,
            "spec_faithfulness": 1.0,
            "brevity": 1.0,
        }
        score = compute_reward(candidate)
        assert score == pytest.approx(1.0)

    def test_hard_gate_failure_returns_zero(self) -> None:
        """Deploy verb in diff → 0.0 even with perfect metrics."""
        candidate = {
            "diff": "+kubectl apply -f deploy.yaml",
            "test_results": "10 passed, 0 failed",
            "scope_discipline": 1.0,
            "spec_faithfulness": 1.0,
            "brevity": 1.0,
        }
        assert compute_reward(candidate) == 0.0

    def test_default_subjective_metrics(self) -> None:
        """When subjective metrics not provided, default to 0.5."""
        candidate = {
            "diff": "+new line",
            "test_results": "10 passed, 0 failed",
        }
        score = compute_reward(candidate)
        expected = 0.4 * 1.0 + 0.2 * 0.5 + 0.2 * 0.5 + 0.1 * 1.0 + 0.1 * 0.5
        assert score == pytest.approx(expected)

    def test_empty_candidate_gates_fail(self) -> None:
        """Empty diff + empty test results → gate failure → 0.0."""
        assert compute_reward({}) == 0.0

    def test_frozen_weights(self) -> None:
        """Verify the weights match the FROZEN v1 YAML."""
        from skillopt_benchmark import _METRIC_WEIGHTS
        assert _METRIC_WEIGHTS == {
            "test_pass_rate": 0.4,
            "scope_discipline": 0.2,
            "spec_faithfulness": 0.2,
            "regression_safety": 0.1,
            "brevity": 0.1,
        }


# ── Tests: Environment adapter ──────────────────────────────────────────

class TestBMADDevStoryEnv:
    """Verify the adapter lifecycle."""

    def _make_env(self) -> BMADDevStoryEnv:
        """Build a BMADDevStoryEnv with in-memory items."""
        env = BMADDevStoryEnv(split_dir="/nonexistent")
        # Inject mock dataloader to avoid filesystem access
        env.dataloader = MagicMock(spec=BMADDevStoryDataLoader)
        env.dataloader.train_items = _make_items(4)
        env.dataloader.val_items = _make_items(1)
        env.dataloader.test_items = _make_items(1)
        return env

    def test_get_task_types_default(self) -> None:
        """Should return ['dev-story'] when items have task_type."""
        env = self._make_env()
        assert env.get_task_types() == ["dev-story"]

    def test_get_task_types_empty(self) -> None:
        """Should return ['dev-story'] as fallback when no items."""
        env = self._make_env()
        env.dataloader.train_items = []
        env.dataloader.val_items = []
        env.dataloader.test_items = []
        assert env.get_task_types() == ["dev-story"]

    def test_build_train_env_returns_list(self) -> None:
        """build_train_env should return a list of items."""
        env = self._make_env()
        batch = MagicMock()
        batch.payload = _make_items(2)
        env.dataloader.build_train_batch.return_value = batch

        result = env.build_train_env(batch_size=2, seed=42)
        assert isinstance(result, list)
        assert len(result) == 2

    def test_build_eval_env_returns_list(self) -> None:
        """build_eval_env should return a list of items."""
        env = self._make_env()
        batch = MagicMock()
        batch.payload = _make_items(1)
        env.dataloader.build_eval_batch.return_value = batch

        result = env.build_eval_env(env_num=1, split="val", seed=42)
        assert isinstance(result, list)
        assert len(result) == 1

    @patch("skillopt_benchmark.run_batch")
    def test_rollout_delegates_to_run_batch(self, mock_run_batch: MagicMock) -> None:
        """rollout should delegate to run_batch with correct args."""
        env = self._make_env()
        items = _make_items(2)
        mock_run_batch.return_value = [
            {"id": "task-0", "hard": 1, "soft": 0.85},
            {"id": "task-1", "hard": 0, "soft": 0.3},
        ]

        results = env.rollout(items, "## Instructions\nDo things.", "/tmp/out")

        assert len(results) == 2
        mock_run_batch.assert_called_once_with(
            items=items,
            skill_content="## Instructions\nDo things.",
            out_root="/tmp/out",
            workers=env.workers,
            max_completion_tokens=env.max_completion_tokens,
        )


class TestBMADDevStoryEnvRollout:
    """Test the rollout_one function with mocked chat_target."""

    @patch("skillopt_benchmark._lazy_skillopt_imports")
    def test_rollout_one_success(self, mock_imports: MagicMock) -> None:
        """Successful rollout should produce valid hard/soft scores."""
        mock_chat = MagicMock()
        mock_chat.return_value = ("+new_function()\n+assert new_function() == True", {})
        mock_imports.return_value = (None, None, None, None, mock_chat)

        from skillopt_benchmark import rollout_one

        item = {
            "id": "test-1",
            "story_spec": "Implement X.",
            "project_context": "lang: python",
            "test_results": "5 passed, 0 failed",
            "scope_discipline": 0.9,
            "spec_faithfulness": 0.9,
            "brevity": 0.9,
        }
        result = rollout_one(item, "## Instructions\nDo X.")

        assert result["id"] == "test-1"
        assert result["hard"] in (0, 1)
        assert 0.0 <= result["soft"] <= 1.0
        assert result["task_type"] == "dev-story"

    @patch("skillopt_benchmark._lazy_skillopt_imports")
    def test_rollout_one_error_returns_zero(self, mock_imports: MagicMock) -> None:
        """Chat failure should produce hard=0, soft=0."""
        mock_chat = MagicMock()
        mock_chat.side_effect = RuntimeError("API error")
        mock_imports.return_value = (None, None, None, None, mock_chat)

        from skillopt_benchmark import rollout_one

        item = {"id": "test-err", "story_spec": "Fail.", "project_context": ""}
        result = rollout_one(item, "body")

        assert result["hard"] == 0
        assert result["soft"] == 0.0
        assert "error" in result["fail_reason"]


# ── Tests: Run batch ────────────────────────────────────────────────────

class TestRunBatch:
    """Test the run_batch helper."""

    @patch("skillopt_benchmark.rollout_one")
    def test_run_batch_returns_results(self, mock_rollout: MagicMock) -> None:
        """run_batch should return one result per item."""
        from skillopt_benchmark import run_batch

        mock_rollout.side_effect = [
            {"id": "task-0", "hard": 1, "soft": 0.9},
            {"id": "task-1", "hard": 0, "soft": 0.3},
        ]
        items = _make_items(2)

        with patch("builtins.open", MagicMock()):
            with patch("os.makedirs"):
                with patch.object(Path, "write_text"):
                    results = run_batch(items, "skill text", "/tmp/out")

        assert len(results) == 2
        assert results[0]["id"] == "task-0"
        assert results[1]["id"] == "task-1"
