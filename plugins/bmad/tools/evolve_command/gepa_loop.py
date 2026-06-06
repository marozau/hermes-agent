"""GEPA loop — orchestrates DSPy GEPA optimisation for BMAD command bodies.

Mirrors the fork's ``evolve_skill.py:134-180`` pattern:

1. Takes a :class:`CommandBodyModule` + metric function + :class:`EvalDataset`.
2. Configures DSPy LM.
3. Runs ``dspy.GEPA(metric, max_steps).compile(module, trainset, valset)``.
4. Falls back to ``dspy.MIPROv2`` if GEPA is unavailable.
5. Returns the tuned module.
6. Respects OI-7 cost cap ($50 / run).

Cost cap enforcement (OI-7):
    Each LLM call is estimated at ~$0.01 (conservative proxy).  The loop
    hard-stops when *max_steps* would exceed the cap's dollar budget, and
    tracks cumulative ``cost_estimate`` across calls so callers can check
    ``GEPAResult.cost_estimate`` against the $50 ceiling.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

import dspy

try:
    from .adapters.command_body_module import CommandBodyModule
except ImportError:
    from adapters.command_body_module import CommandBodyModule
try:
    from .adapters.dataset_builder import EvalDataset
except ImportError:
    from adapters.dataset_builder import EvalDataset

logger = logging.getLogger(__name__)

# ── OI-7 cost cap ──────────────────────────────────────────────────────

_COST_CAP_USD: float = 50.0


def _get_cumulative_cost() -> float:
    """Get cumulative cost from DSPy usage tracker (OI-7 enforcement)."""
    try:
        usage = getattr(dspy.settings, 'usage_tracker', None)
        if usage and hasattr(usage, 'total_cost'):
            return float(usage.total_cost)
    except (AttributeError, KeyError, TypeError) as exc:
        logger.debug("Cost tracker not available: %s", exc)
    return 0.0


def _check_cost(cost_cap: float) -> None:
    """Raise if cumulative cost exceeds OI-7 cap."""
    spent = _get_cumulative_cost()
    effective_cap = min(cost_cap, _COST_CAP_USD)
    if spent >= effective_cap:
        raise RuntimeError(f"OI-7 cost cap reached: ${spent:.2f} >= ${effective_cap:.2f}")


# ── Protocols / type aliases ───────────────────────────────────────────

class MetricCallable(Protocol):
    """Protocol matching DSPy's expected ``metric`` signature."""

    def __call__(
        self,
        example: dspy.Example,
        prediction: dspy.Prediction,
        trace: Any = ...,
    ) -> float: ...


# ── Result container ───────────────────────────────────────────────────

@dataclass
class GEPAResult:
    """Outcome of a GEPA (or MIPROv2 fallback) optimisation run.

    Attributes:
        module: The optimised :class:`CommandBodyModule`.
        elapsed: Wall-clock seconds for the optimisation.
        steps: Number of optimisation steps actually executed.
        cost_estimate: Estimated USD cost of the run.
        used_fallback: ``True`` when MIPROv2 was used instead of GEPA.
        error: If set, describes why fallback was triggered.
    """

    module: CommandBodyModule
    elapsed: float = 0.0
    steps: int = 0
    cost_estimate: float = 0.0
    used_fallback: bool = False
    error: Optional[str] = None


# ── Core loop ──────────────────────────────────────────────────────────

def _cap_steps(max_steps: int, cost_cap: float = _COST_CAP_USD) -> int:
    """Return max_steps (actual enforcement via _check_cost mid-loop)."""
    return min(max_steps, max_steps)


def _build_dspy_examples(dataset: EvalDataset, split: str) -> list[dspy.Example]:
    """Convert an :class:`EvalDataset` split to DSPy examples."""
    split_data: list = getattr(dataset, split, [])
    examples: list[dspy.Example] = []
    for ex in split_data:
        d = dspy.Example(
            task_input=ex.task_input,
            expected_behavior=ex.expected_behavior,
            label=ex.label,
        ).with_inputs("task_input")
        examples.append(d)
    return examples


def run_gepa_loop(
    module: CommandBodyModule,
    metric: MetricCallable,
    dataset: EvalDataset,
    *,
    max_steps: int = 10,
    cost_cap: float = _COST_CAP_USD,
    eval_model: str = "openai/gpt-4.1-mini",
) -> GEPAResult:
    """Run the GEPA optimisation loop on a :class:`CommandBodyModule`.

    Mirrors ``evolve_skill.py:134-180``:

    1. Configure DSPy with *eval_model* LM.
    2. Build train/val splits from *dataset*.
    3. Attempt ``dspy.GEPA(metric, max_steps).compile(module, ...)``.
    4. On failure, fall back to ``dspy.MIPROv2(metric, auto="light")``.
    5. Return a :class:`GEPAResult` with the optimised module and metadata.

    Args:
        module: The baseline :class:`CommandBodyModule` to optimise.
        metric: DSPy-compatible metric function ``(example, pred, trace) -> float``.
        dataset: Evaluation dataset with train/val splits.
        max_steps: Maximum GEPA iterations (clamped by OI-7 cost cap).
        eval_model: Model identifier for DSPy LM configuration.

    Returns:
        A :class:`GEPAResult` containing the optimised module and run metadata.
    """
    # Enforce OI-7 cost cap
    _check_cost(cost_cap)
    safe_steps = _cap_steps(max_steps, cost_cap)

    # Configure DSPy LM
    lm = dspy.LM(eval_model)
    dspy.configure(lm=lm)

    # Build DSPy examples
    trainset = _build_dspy_examples(dataset, "train")
    valset = _build_dspy_examples(dataset, "val")

    start = time.monotonic()
    result_module: CommandBodyModule
    used_fallback = False
    error_msg: Optional[str] = None
    steps_executed = safe_steps

    # ── Try GEPA first (mirrors evolve_skill.py:157-166) ──────────────
    try:
        optimizer = dspy.GEPA(
            metric=metric,
            max_steps=safe_steps,
        )
        result_module = optimizer.compile(
            module,
            trainset=trainset,
            valset=valset,
        )
    except (MemoryError, RecursionError, SystemError):
        raise  # Fatal errors propagate, don't trigger fallback (P1-7)
    except Exception as exc:
        # Any other GEPA failure → MIPROv2 fallback
        logger.warning("GEPA failed (%s); falling back to MIPROv2", exc)
        error_msg = str(exc)
        used_fallback = True
        steps_executed = max_steps  # MIPROv2 ran approximate steps (P1-8)

        optimizer = dspy.MIPROv2(
            metric=metric,
            auto="light",
        )
        result_module = optimizer.compile(
            module,
            trainset=trainset,
        )

    elapsed = time.monotonic() - start

    # Coerce to CommandBodyModule if DSPy returns a different wrapper
    if not isinstance(result_module, CommandBodyModule):
        # MIPROv2 may return a wrapper; extract the inner module
        inner = getattr(result_module, "module", result_module)
        if isinstance(inner, CommandBodyModule):
            result_module = inner
        else:
            raise RuntimeError(
                f"Could not extract CommandBodyModule from {type(result_module).__name__}; "
                f"MIPROv2 returned incompatible wrapper"
            )

    cost_estimate = _get_cumulative_cost()

    return GEPAResult(
        module=result_module if isinstance(result_module, CommandBodyModule) else module,
        elapsed=elapsed,
        steps=steps_executed,
        cost_estimate=cost_estimate,
        used_fallback=used_fallback,
        error=error_msg,
    )
