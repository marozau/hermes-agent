"""GEPA metric adapter — wraps CodeOutputJudge into a single callable.

Loads the FROZEN YAML rubric (metrics/dev_story_composite_v1.yaml) and
Epic 13's locked prompts (prompts/scope_discipline_v1.md,
prompts/spec_faithfulness_v1.md).  Combines hard-gate checks with weighted
composite scoring so GEPA can evaluate candidates with one call.

FROZEN formula (v1):
    0.4 * test_pass_rate
    + 0.2 * scope_discipline
    + 0.2 * spec_faithfulness
    + 0.1 * regression_safety
    + 0.1 * brevity

Hard gates (fail → 0.0):
    1. test_pass >= 0.7
    2. regression == 1.0
    3. no deploy verbs in diff
    4. no credential paths in diff
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml  # type: ignore[import-untyped]

from judge import (
    HardGateResult,
    JudgeScore,
    _estimate_regression_safety,
    _parse_test_pass_rate,
    check_hard_gates,
    compute_composite,
    load_metric_formula,
)

# ── Paths (relative to evolve_command/) ─────────────────────────────────

_EVOLVE_DIR = Path(__file__).resolve().parent.parent
_METRIC_YAML = _EVOLVE_DIR / "metrics" / "dev_story_composite_v1.yaml"
_SCOPE_PROMPT = _EVOLVE_DIR / "prompts" / "scope_discipline_v1.md"
_FAITH_PROMPT = _EVOLVE_DIR / "prompts" / "spec_faithfulness_v1.md"


# ── Frozen rubric loader ────────────────────────────────────────────────


@dataclass(frozen=True)
class MetricRubric:
    """Immutable snapshot of the FROZEN metric YAML."""

    name: str
    version: int
    freeze_date: str
    weights: dict[str, float]
    hard_gates: tuple[dict[str, Any], ...]
    scope_prompt: str
    faithfulness_prompt: str


def load_rubric(
    yaml_path: Optional[Path] = None,
    scope_path: Optional[Path] = None,
    faith_path: Optional[Path] = None,
) -> MetricRubric:
    """Load and validate the FROZEN metric rubric.

    Args:
        yaml_path:  Path to the metric YAML.  Defaults to the shipped file.
        scope_path: Path to the scope-discipline prompt.  Defaults to shipped.
        faith_path: Path to the spec-faithfulness prompt.  Defaults to shipped.

    Returns:
        An immutable MetricRubric snapshot.

    Raises:
        FileNotFoundError: If any required file is missing.
        ValueError: If the YAML is missing required fields.
    """
    yp = yaml_path or _METRIC_YAML
    sp = scope_path or _SCOPE_PROMPT
    fp = faith_path or _FAITH_PROMPT

    if not yp.exists():
        raise FileNotFoundError(f"Metric YAML not found: {yp}")
    if not sp.exists():
        raise FileNotFoundError(f"Scope prompt not found: {sp}")
    if not fp.exists():
        raise FileNotFoundError(f"Faithfulness prompt not found: {fp}")

    data = yaml.safe_load(yp.read_text()) or {}
    if "weights" not in data:
        raise ValueError(f"Metric YAML missing 'weights' key: {yp}")

    return MetricRubric(
        name=data.get("name", "dev_story_composite_v1"),
        version=data.get("version", 1),
        freeze_date=data.get("freeze_date", ""),
        weights=dict(data["weights"]),
        hard_gates=tuple(data.get("hard_gates", [])),
        scope_prompt=sp.read_text(),
        faithfulness_prompt=fp.read_text(),
    )


# ── GEPA metric callable ────────────────────────────────────────────────


def dev_story_composite_v1_metric(
    candidate: dict[str, Any],
    rubric: Optional[MetricRubric] = None,
) -> float:
    """Single-callable GEPA metric for BMAD dev story code outputs.

    Pipeline:
        1. Parse test_pass_rate and estimate regression_safety (heuristics).
        2. Run hard gates — any failure returns 0.0 immediately.
        3. Compute weighted composite from the 5 metrics.

    Args:
        candidate: Dict with keys:
            - story_spec (str):       Story specification text.
            - diff (str):             Unified diff patch.
            - test_results (str):     Raw test execution output.
            - project_context (str):  YAML project context.
            - scope_discipline (float, optional): 0.0-1.0 override.
            - spec_faithfulness (float, optional): 0.0-1.0 override.
            - brevity (float, optional): 0.0-1.0 override.
        rubric: Pre-loaded MetricRubric.  If None, loads from disk.

    Returns:
        Composite score 0.0-1.0.  Returns 0.0 when hard gates fail.

    Raises:
        KeyError: If required candidate keys are missing.
    """
    r = rubric or load_rubric()

    diff: str = candidate["diff"]
    test_results: str = candidate["test_results"]

    # ── Step 1: heuristic scores ────────────────────────────────────────
    test_pass_rate = _parse_test_pass_rate(test_results)
    regression_safety = _estimate_regression_safety(diff)

    # ── Step 2: hard gates ──────────────────────────────────────────────
    gate: HardGateResult = check_hard_gates(diff, test_pass_rate, regression_safety)
    if not gate.passed:
        return 0.0

    # ── Step 3: weighted composite ──────────────────────────────────────
    # Subjective metrics: use caller-supplied values or neutral 0.5 default.
    scope_discipline: float = float(candidate.get("scope_discipline", 0.5))
    spec_faithfulness: float = float(candidate.get("spec_faithfulness", 0.5))
    brevity: float = float(candidate.get("brevity", 0.5))

    score = JudgeScore(
        test_pass_rate=test_pass_rate,
        scope_discipline=scope_discipline,
        spec_faithfulness=spec_faithfulness,
        regression_safety=regression_safety,
        brevity=brevity,
    )
    return compute_composite(score, r.weights)
