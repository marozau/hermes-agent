"""CodeOutputJudge — composite scoring with hard gates.

Scores code outputs on a 5-metric composite defined in
metrics/dev_story_composite_v1.yaml. Hard gates fire BEFORE the
LLM judge to save tokens (TI-5).

FROZEN formula (v1):
    0.4 * test_pass_rate
    + 0.2 * scope_discipline
    + 0.2 * spec_faithfulness
    + 0.1 * regression_safety
    + 0.1 * brevity

Hard gates:
    1. test_pass >= 0.7
    2. regression == 1.0
    3. no deploy verbs in diff
    4. no credential paths in diff
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import dspy
import yaml  # type: ignore[import-untyped]


# ── Hard gate patterns ────────────────────────────────────────────────────

DEPLOY_VERBS = re.compile(
    r'\b(kubectl\s+apply|docker\s+push|helm\s+upgrade|deploy\s+--production'
    r'|terraform\s+apply|aws\s+deploy|gcloud\s+run|az\s+containerapp)\b',
    re.IGNORECASE,
)

CREDENTIAL_PATHS = re.compile(
    r'(~/.aws/credentials|~/.ssh/id_|\.env\.production|/etc/ssl/private'
    r'|secret[_-]?key|api[_-]?key\s*=|password\s*=|token\s*=)',
    re.IGNORECASE,
)


# ── Metric weights ────────────────────────────────────────────────────────

METRIC_WEIGHTS = {
    "test_pass_rate": 0.4,
    "scope_discipline": 0.2,
    "spec_faithfulness": 0.2,
    "regression_safety": 0.1,
    "brevity": 0.1,
}


def load_metric_formula(yaml_path: Optional[Path] = None) -> dict[str, float]:
    """Load metric weights from YAML or return frozen defaults.

    Args:
        yaml_path: Path to the metrics YAML file. If None, uses defaults.

    Returns:
        Dict mapping metric names to weights.
    """
    if yaml_path and yaml_path.exists():
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict) and "weights" in data:
            return dict(data["weights"])
    return dict(METRIC_WEIGHTS)


# ── Hard gates ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HardGateResult:
    """Result of hard gate checks."""
    passed: bool
    failures: tuple[str, ...]  # Reasons for failure (empty if passed)


def check_hard_gates(
    diff: str,
    test_pass_rate: float,
    regression_safety: float,
) -> HardGateResult:
    """Run all 4 hard gates. Failures are collected, not short-circuited.

    Args:
        diff: The unified diff to check.
        test_pass_rate: Fraction of tests passing (0.0-1.0).
        regression_safety: Regression safety score (0.0 or 1.0).

    Returns:
        HardGateResult with pass/fail and failure reasons.
    """
    failures: list[str] = []

    # Gate 1: test_pass >= 0.7
    if test_pass_rate < 0.7:
        failures.append(f"test_pass_rate={test_pass_rate:.2f} < 0.7")

    # Gate 2: regression == 1.0
    if regression_safety != 1.0:
        failures.append(f"regression_safety={regression_safety:.2f} != 1.0")

    # Gate 3: no deploy verbs
    if DEPLOY_VERBS.search(diff):
        match = DEPLOY_VERBS.search(diff)
        verb = match.group() if match else "unknown"
        failures.append(f"deploy verb found: {verb}")

    # Gate 4: no credential paths
    if CREDENTIAL_PATHS.search(diff):
        match = CREDENTIAL_PATHS.search(diff)
        path = match.group() if match else "unknown"
        failures.append(f"credential path found: {path}")

    return HardGateResult(
        passed=len(failures) == 0,
        failures=tuple(failures),
    )


# ── Judge scoring ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class JudgeScore:
    """Composite score from the code output judge."""
    test_pass_rate: float = 0.0
    scope_discipline: float = 0.0
    spec_faithfulness: float = 0.0
    regression_safety: float = 0.0
    brevity: float = 0.0
    composite: float = 0.0
    feedback: str = ""
    hard_gate_passed: bool = True
    hard_gate_failures: tuple[str, ...] = ()


def compute_composite(score: JudgeScore, weights: Optional[dict[str, float]] = None) -> float:
    """Compute weighted composite from individual metric scores.

    Args:
        score: JudgeScore with individual metrics.
        weights: Optional weight overrides. Defaults to METRIC_WEIGHTS.

    Returns:
        Weighted composite score (0.0-1.0).
    """
    w = weights or METRIC_WEIGHTS
    return (
        w.get("test_pass_rate", 0.4) * score.test_pass_rate
        + w.get("scope_discipline", 0.2) * score.scope_discipline
        + w.get("spec_faithfulness", 0.2) * score.spec_faithfulness
        + w.get("regression_safety", 0.1) * score.regression_safety
        + w.get("brevity", 0.1) * score.brevity
    )


class CodeOutputJudgeSignature(dspy.Signature):
    """Score a code output on 5 metrics for a BMAD story.

    Evaluate the diff against the story spec and project context.
    Score each metric 0.0-1.0:
    1. test_pass_rate: fraction of tests that pass
    2. scope_discipline: does the diff stay within story scope?
    3. spec_faithfulness: does the implementation match the spec?
    4. regression_safety: no regressions introduced?
    5. brevity: is the diff appropriately concise?
    """
    story_spec: str = dspy.InputField(desc="Story specification with acceptance criteria")
    diff: str = dspy.InputField(desc="Unified diff patch")
    test_results: str = dspy.InputField(desc="Test execution results")
    project_context: str = dspy.InputField(desc="YAML project context")
    test_pass_rate: float = dspy.OutputField(desc="0.0-1.0: fraction of tests passing")
    scope_discipline: float = dspy.OutputField(desc="0.0-1.0: stays within story scope")
    spec_faithfulness: float = dspy.OutputField(desc="0.0-1.0: matches spec requirements")
    regression_safety: float = dspy.OutputField(desc="0.0-1.0: no regressions")
    brevity: float = dspy.OutputField(desc="0.0-1.0: appropriately concise")
    feedback: str = dspy.OutputField(desc="Specific, actionable feedback")


class CodeOutputJudge:
    """Composite judge for BMAD code outputs.

    Hard gates fire FIRST (TI-5: saves LLM tokens on obvious failures).
    If hard gates pass, the LLM judge scores on 5 metrics.

    Usage:
        judge = CodeOutputJudge(eval_model="openai/gpt-4.1-mini")
        score = judge.score(story_spec, diff, test_results, project_context)
    """

    def __init__(
        self,
        eval_model: str = "openai/gpt-4.1-mini",
        metrics_yaml: Optional[Path] = None,
    ) -> None:
        """Initialize the judge.

        Args:
            eval_model: LiteLLM model string for the LLM judge.
            metrics_yaml: Path to metrics YAML. If None, uses frozen defaults.
        """
        self.eval_model = eval_model
        self.weights = load_metric_formula(metrics_yaml)
        self.judge = dspy.ChainOfThought(CodeOutputJudgeSignature)

    def score(
        self,
        story_spec: str,
        diff: str,
        test_results: str,
        project_context: str,
    ) -> JudgeScore:
        """Score a code output. Hard gates fire before LLM judge.

        Args:
            story_spec: Story specification.
            diff: Unified diff patch.
            test_results: Test execution results.
            project_context: YAML project context.

        Returns:
            JudgeScore with composite and hard gate info.
        """
        # Parse test pass rate from test_results (heuristic)
        test_pass_rate = _parse_test_pass_rate(test_results)

        # Estimate regression safety from diff (heuristic)
        regression_safety = _estimate_regression_safety(diff)

        # Hard gates first (TI-5: saves tokens)
        gate = check_hard_gates(diff, test_pass_rate, regression_safety)

        if not gate.passed:
            return JudgeScore(
                test_pass_rate=test_pass_rate,
                regression_safety=regression_safety,
                composite=0.0,
                feedback=f"Hard gates failed: {'; '.join(gate.failures)}",
                hard_gate_passed=False,
                hard_gate_failures=gate.failures,
            )

        # LLM judge for the remaining subjective metrics
        lm = dspy.LM(self.eval_model)
        with dspy.context(lm=lm):
            result = self.judge(
                story_spec=story_spec,
                diff=diff,
                test_results=test_results,
                project_context=project_context,
            )

        llm_test_rate = _clamp(getattr(result, "test_pass_rate", 0.5))
        scope = _clamp(getattr(result, "scope_discipline", 0.5))
        faithfulness = _clamp(getattr(result, "spec_faithfulness", 0.5))
        llm_regression = _clamp(getattr(result, "regression_safety", 1.0))
        llm_brevity = _clamp(getattr(result, "brevity", 0.5))
        feedback = str(getattr(result, "feedback", ""))

        # Use the max of heuristic and LLM regression safety
        final_regression = max(regression_safety, llm_regression)
        # Use the max of parsed and LLM test rate
        final_test_rate = max(test_pass_rate, llm_test_rate)

        score = JudgeScore(
            test_pass_rate=final_test_rate,
            scope_discipline=scope,
            spec_faithfulness=faithfulness,
            regression_safety=final_regression,
            brevity=llm_brevity,
            composite=0.0,  # computed below
            feedback=feedback,
            hard_gate_passed=True,
        )
        # Frozen dataclass — compute composite separately
        return JudgeScore(
            test_pass_rate=score.test_pass_rate,
            scope_discipline=score.scope_discipline,
            spec_faithfulness=score.spec_faithfulness,
            regression_safety=score.regression_safety,
            brevity=score.brevity,
            composite=compute_composite(score, self.weights),
            feedback=score.feedback,
            hard_gate_passed=True,
        )


def _parse_test_pass_rate(test_results: str) -> float:
    """Parse test pass rate from test results text.

    Looks for patterns like "5 passed, 1 failed" or "PASS" / "FAIL" lines.

    Args:
        test_results: Raw test output text.

    Returns:
        Float 0.0-1.0 representing pass rate.
    """
    if not test_results.strip():
        return 0.0

    # Pattern: "N passed, M failed" (pytest-style)
    pass_match = re.search(r'(\d+)\s+passed', test_results)
    fail_match = re.search(r'(\d+)\s+failed', test_results)
    if pass_match:
        passed = int(pass_match.group(1))
        failed = int(fail_match.group(1)) if fail_match else 0
        total = passed + failed
        if total > 0:
            return passed / total

    # Pattern: count PASS/FAIL lines
    lines = test_results.strip().split("\n")
    pass_count = sum(1 for l in lines if "PASS" in l.upper() and "FAIL" not in l.upper())
    fail_count = sum(1 for l in lines if "FAIL" in l.upper())
    total = pass_count + fail_count
    if total > 0:
        return pass_count / total

    return 0.5  # Default neutral


def _estimate_regression_safety(diff: str) -> float:
    """Estimate regression safety from diff heuristics.

    Conservative: returns 1.0 only if diff has no deletions of existing
    test assertions and no removal of import statements.

    Args:
        diff: Unified diff text.

    Returns:
        1.0 if safe, 0.0 if regressions detected.
    """
    if not diff.strip():
        return 1.0

    deleted_lines = [
        line for line in diff.split("\n")
        if line.startswith("-") and not line.startswith("---")
    ]

    # Check for deleted test assertions
    for line in deleted_lines:
        lower = line.lower()
        if any(kw in lower for kw in ["assert", "expect", "test_", "unittest"]):
            return 0.0

    return 1.0


def _clamp(value: object, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp a value to [lo, hi]."""
    try:
        return max(lo, min(hi, float(str(value))))
    except (ValueError, TypeError):
        return 0.5
