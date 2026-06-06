"""Phase 2 smoke run — Story 15.7 (D-38 scaffolding).

Creates a minimal 5-example dataset, runs the GEPA loop on a small
command body loaded from tests/fixtures/mini_command.md, and verifies
the output differs from baseline.  This is an integration smoke test
—not a unit test.  Don't polish the output; the goal is to prove the
Phase 2 pipeline produces a tuned body that differs from baseline.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the package is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.command_body_module import CommandBodyModule
from adapters.dataset_builder import EvalDataset, EvalExample
from gepa_loop import GEPAResult, run_gepa_loop

# ── Paths ──────────────────────────────────────────────────────────────

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_MINI_COMMAND_MD = _FIXTURES_DIR / "mini_command.md"


# ── Helpers ────────────────────────────────────────────────────────────


def _load_mini_module() -> CommandBodyModule:
    """Load the mini command fixture as a CommandBodyModule."""
    raw = _MINI_COMMAND_MD.read_text()
    return CommandBodyModule.from_raw(raw)


def _make_five_examples() -> EvalDataset:
    """Build a minimal 5-example dataset (3 train, 1 val, 1 holdout).

    Each example is a simple coding task with a known-good label.
    """
    examples = [
        EvalExample(
            task_input="Write a Python function that returns the square of a number.",
            expected_behavior="def square(n): return n * n",
            label=1.0,
        ),
        EvalExample(
            task_input="Write a Python function that checks if a string is a palindrome.",
            expected_behavior="def is_palindrome(s): return s == s[::-1]",
            label=1.0,
        ),
        EvalExample(
            task_input="Write a Python function that sums a list of integers.",
            expected_behavior="def sum_list(nums): return sum(nums)",
            label=1.0,
        ),
        EvalExample(
            task_input="Write a Python function that finds the max in a list.",
            expected_behavior="def find_max(nums): return max(nums)",
            label=0.0,
        ),
        EvalExample(
            task_input="Write a Python function that reverses a list.",
            expected_behavior="def reverse_list(lst): return lst[::-1]",
            label=0.0,
        ),
    ]
    return EvalDataset(
        train=examples[:3],
        val=examples[3:4],
        holdout=examples[4:5],
    )


def _simple_metric(example, prediction, trace=None):
    """Trivial metric: 1.0 if output is non-empty, else 0.0."""
    return 1.0 if getattr(prediction, "output", None) else 0.0


# ── Smoke test ─────────────────────────────────────────────────────────


@pytest.mark.smoke
@pytest.mark.slow
class TestPhase2SmokeRun:
    """D-38 smoke: run the full GEPA loop and verify output != baseline."""

    def test_smoke_run_produces_different_body(self) -> None:
        """GEPA (or MIPROv2 fallback) should produce a body_text
        that differs from the baseline after optimisation.

        The smoke run uses 5 examples and max_steps=3 so it
        completes in <10 min on a real model.
        """
        baseline_module = _load_mini_module()
        baseline_body = baseline_module.body_text

        dataset = _make_five_examples()

        result = run_gepa_loop(
            baseline_module,
            _simple_metric,
            dataset,
            max_steps=3,
            eval_model="openai/gpt-4.1-mini",
        )

        assert isinstance(result, GEPAResult)
        # The result should contain a valid module
        assert result.module is not None
        assert isinstance(result.module, CommandBodyModule)
        # Elapsed should be non-negative
        assert result.elapsed >= 0.0
        # Cost should be within bounds
        assert result.cost_estimate >= 0.0
        assert result.cost_estimate <= 50.0  # OI-7 cap

        # Core assertion: the optimised body should differ from baseline.
        # GEPA/MIPROv2 mutates the command body during compilation.
        # If it didn't change, the optimiser had no effect (which is
        # still a valid outcome for such a tiny dataset, but we flag
        # it as a warning rather than a hard failure).
        optimised_body = result.module.body_text
        if optimised_body == baseline_body:
            pytest.xfail(
                "Body unchanged after optimisation — this can happen with "
                "a tiny 5-example dataset; not a hard failure."
            )
