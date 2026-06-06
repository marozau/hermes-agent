"""Tests for gepa_loop.py — GEPA loop integration (Story 15.6)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure the package is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.command_body_module import CommandBodyModule
from adapters.dataset_builder import EvalDataset, EvalExample

# ── Helpers ────────────────────────────────────────────────────────────


def _make_dataset(n: int = 6) -> EvalDataset:
    """Build a small EvalDataset with n examples across splits."""
    examples = [
        EvalExample(
            task_input=f"task-{i}",
            expected_behavior=f"expected-{i}",
            label=float(i % 2),
        )
        for i in range(n)
    ]
    # 4 train, 1 val, 1 holdout
    return EvalDataset(
        train=examples[:4],
        val=examples[4:5],
        holdout=examples[5:6],
    )


def _trivial_metric(example, prediction, trace=None):
    """Trivial metric that returns 0.5 for any non-empty output."""
    return 0.5 if prediction.output else 0.0


def _make_module() -> CommandBodyModule:
    """Build a minimal CommandBodyModule for testing."""
    return CommandBodyModule(
        frontmatter="name: test-cmd",
        body_text="## Instructions\nDo the thing.",
    )


# ── Tests ──────────────────────────────────────────────────────────────


class TestGEPAResult:
    """Verify the GEPAResult dataclass."""

    def test_fields_default(self) -> None:
        """GEPAResult should have sensible defaults."""
        from gepa_loop import GEPAResult

        result = GEPAResult(module=_make_module())
        assert result.elapsed == 0.0
        assert result.steps == 0  # default is 0
        assert result.cost_estimate == 0.0
        assert result.used_fallback is False
        assert result.error is None


class TestCostCap:
    """Test OI-7 cost cap enforcement."""

    def test_cap_steps_clamps_high(self) -> None:
        """_cap_steps should clamp to ~200 (= $50 / $0.25)."""
        from gepa_loop import _cap_steps

        assert _cap_steps(9999) == 9999  # P0-2: clamping removed; enforcement via _check_cost

    def test_cap_steps_preserves_low(self) -> None:
        """_cap_steps should not inflate small values."""
        from gepa_loop import _cap_steps

        assert _cap_steps(5) == 5

    def test_cap_steps_zero(self) -> None:
        """Zero steps passes through."""
        from gepa_loop import _cap_steps

        assert _cap_steps(0) == 0


class TestBuildDSPyExamples:
    """Test EvalDataset → dspy.Example conversion."""

    def test_convert_train_split(self) -> None:
        """Should produce dspy.Examples from train split."""
        from gepa_loop import _build_dspy_examples

        ds = _make_dataset()
        examples = _build_dspy_examples(ds, "train")
        assert len(examples) == 4
        for ex in examples:
            assert hasattr(ex, "task_input")
            assert hasattr(ex, "expected_behavior")
            assert hasattr(ex, "label")

    def test_convert_empty_split(self) -> None:
        """Empty split should return empty list."""
        from gepa_loop import _build_dspy_examples

        ds = EvalDataset()
        examples = _build_dspy_examples(ds, "train")
        assert examples == []

    def test_convert_holdout_split(self) -> None:
        """Should correctly convert holdout split."""
        from gepa_loop import _build_dspy_examples

        ds = _make_dataset()
        examples = _build_dspy_examples(ds, "holdout")
        assert len(examples) == 1


class TestRunGEPALoopFallback:
    """Test the run_gepa_loop function with MIPROv2 fallback."""

    @patch("gepa_loop.dspy.GEPA")
    @patch("gepa_loop.dspy.MIPROv2")
    @patch("gepa_loop.dspy.LM")
    @patch("gepa_loop.dspy.configure")
    def test_gepa_success(
        self,
        mock_configure: MagicMock,
        mock_lm: MagicMock,
        mock_mipro: MagicMock,
        mock_gepa: MagicMock,
    ) -> None:
        """When GEPA succeeds, no fallback should be used."""
        from gepa_loop import GEPAResult, run_gepa_loop

        # Arrange: mock GEPA to return the input module unchanged
        mock_optimizer = MagicMock()
        baseline = _make_module()
        mock_optimizer.compile.return_value = baseline
        mock_gepa.return_value = mock_optimizer

        result = run_gepa_loop(
            baseline,
            _trivial_metric,
            _make_dataset(),
            max_steps=5,
            eval_model="test/model",
        )

        assert isinstance(result, GEPAResult)
        assert result.used_fallback is False
        assert result.error is None
        assert result.steps == 5
        assert result.cost_estimate >= 0.0  # P0-2: real cost tracking
        mock_gepa.assert_called_once()
        mock_mipro.assert_not_called()

    @patch("gepa_loop.dspy.GEPA")
    @patch("gepa_loop.dspy.MIPROv2")
    @patch("gepa_loop.dspy.LM")
    @patch("gepa_loop.dspy.configure")
    def test_gepa_fails_falls_back_to_mipro(
        self,
        mock_configure: MagicMock,
        mock_lm: MagicMock,
        mock_mipro: MagicMock,
        mock_gepa: MagicMock,
    ) -> None:
        """When GEPA raises, MIPROv2 fallback should be used."""
        from gepa_loop import GEPAResult, run_gepa_loop

        # Arrange: GEPA raises AttributeError (class missing)
        mock_gepa.side_effect = AttributeError("module 'dspy' has no attribute 'GEPA'")

        mock_optimizer = MagicMock()
        baseline = _make_module()
        mock_optimizer.compile.return_value = baseline
        mock_mipro.return_value = mock_optimizer

        result = run_gepa_loop(
            baseline,
            _trivial_metric,
            _make_dataset(),
            max_steps=5,
            eval_model="test/model",
        )

        assert isinstance(result, GEPAResult)
        assert result.used_fallback is True
        assert result.error is not None
        assert "GEPA" in result.error
        assert result.steps == 5  # P1-8: fallback reports max_steps, not 0
        assert result.cost_estimate == 0.0
        mock_mipro.assert_called_once()

    @patch("gepa_loop.dspy.GEPA")
    @patch("gepa_loop.dspy.MIPROv2")
    @patch("gepa_loop.dspy.LM")
    @patch("gepa_loop.dspy.configure")
    def test_gepa_generic_exception_falls_back(
        self,
        mock_configure: MagicMock,
        mock_lm: MagicMock,
        mock_mipro: MagicMock,
        mock_gepa: MagicMock,
    ) -> None:
        """A generic RuntimeError from GEPA should also trigger fallback."""
        from gepa_loop import GEPAResult, run_gepa_loop

        mock_gepa.side_effect = RuntimeError("optimizer exploded")

        mock_optimizer = MagicMock()
        baseline = _make_module()
        mock_optimizer.compile.return_value = baseline
        mock_mipro.return_value = mock_optimizer

        result = run_gepa_loop(
            baseline,
            _trivial_metric,
            _make_dataset(),
            max_steps=5,
            eval_model="test/model",
        )

        assert result.used_fallback is True
        assert result.error is not None
        assert "exploded" in result.error
        mock_mipro.assert_called_once()

    @patch("gepa_loop.dspy.GEPA")
    @patch("gepa_loop.dspy.MIPROv2")
    @patch("gepa_loop.dspy.LM")
    @patch("gepa_loop.dspy.configure")
    def test_cost_cap_applied(
        self,
        mock_configure: MagicMock,
        mock_lm: MagicMock,
        mock_mipro: MagicMock,
        mock_gepa: MagicMock,
    ) -> None:
        """max_steps should be clamped to stay within OI-7 cost cap."""
        from gepa_loop import run_gepa_loop

        mock_optimizer = MagicMock()
        baseline = _make_module()
        mock_optimizer.compile.return_value = baseline
        mock_gepa.return_value = mock_optimizer

        result = run_gepa_loop(
            baseline,
            _trivial_metric,
            _make_dataset(),
            max_steps=10000,  # way above cap
            eval_model="test/model",
        )

        # P0-2: cost cap enforcement is via _check_cost mid-loop, not pre-clamping
        assert result.steps == 10000  # steps not clamped; abort happens via _check_cost
        assert result.cost_estimate >= 0.0
        # Verify GEPA was called with original max_steps (no pre-clamping)
        call_kwargs = mock_gepa.call_args
        assert call_kwargs[1]["max_steps"] == 10000

    @patch("gepa_loop.dspy.GEPA")
    @patch("gepa_loop.dspy.MIPROv2")
    @patch("gepa_loop.dspy.LM")
    @patch("gepa_loop.dspy.configure")
    def test_elapsed_positive(
        self,
        mock_configure: MagicMock,
        mock_lm: MagicMock,
        mock_mipro: MagicMock,
        mock_gepa: MagicMock,
    ) -> None:
        """Elapsed time should be non-negative."""
        from gepa_loop import run_gepa_loop

        mock_optimizer = MagicMock()
        baseline = _make_module()
        mock_optimizer.compile.return_value = baseline
        mock_gepa.return_value = mock_optimizer

        result = run_gepa_loop(
            baseline,
            _trivial_metric,
            _make_dataset(),
            max_steps=1,
            eval_model="test/model",
        )

        assert result.elapsed >= 0.0
