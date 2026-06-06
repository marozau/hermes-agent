"""Code-review POC smoke run — Story 15.14 (D-38 scaffolding).

Applies the stacked pipeline (Phase 1 SkillOpt → Phase 2 GEPA) to
``commands/code-review.md`` using the Epic 14 R1-R6 review trajectory as
labeled training data.  Loads the ``code_review_convergence_v1`` FROZEN
metric, builds a review-trajectory dataset, runs the pipeline with mock
runners, and verifies it produces signal OR documents kill criteria.

This is a D-38 scaffolding smoke test — not a unit test.  The goal is to
prove that the code-review POC pipeline (metric → dataset → stacked
pipeline) composes correctly end-to-end using the real command file and
the canonical Epic 14 trajectory data.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

# Ensure the package is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.dataset_builder import (
    EPIC14_TRAJECTORY,
    EvalDataset,
    EvalExample,
    ReviewTrajectory,
    build_dataset_from_review_trajectories,
)
from adapters.review_trajectory_extractor import extract_trajectories_from_files
from stacked_pipeline import (
    Phase1Result,
    Phase2Result,
    PhaseRegion,
    StackedPipelineResult,
    run_stacked_pipeline,
)

# ── Paths ──────────────────────────────────────────────────────────────

_EVOLVE_DIR = Path(__file__).resolve().parent.parent
_COMMANDS_DIR = _EVOLVE_DIR.parent.parent / "commands"
_CODE_REVIEW_MD = _COMMANDS_DIR / "code-review.md"
_METRIC_YAML = _EVOLVE_DIR / "metrics" / "code_review_convergence_v1.yaml"


# ── Helpers ────────────────────────────────────────────────────────────


def _load_code_review_command() -> str:
    """Load the code-review.md command file."""
    if not _CODE_REVIEW_MD.exists():
        pytest.skip(f"code-review.md not found at {_CODE_REVIEW_MD}")
    return _CODE_REVIEW_MD.read_text(encoding="utf-8")


def _load_metric() -> dict:
    """Load the FROZEN code_review_convergence_v1 metric YAML."""
    if not _METRIC_YAML.exists():
        pytest.fail(f"FROZEN metric YAML not found: {_METRIC_YAML}")
    data = yaml.safe_load(_METRIC_YAML.read_text())
    assert isinstance(data, dict), "YAML root must be a mapping"
    return data


def _build_epic14_dataset() -> EvalDataset:
    """Build an EvalDataset from the canonical Epic 14 R1-R6 trajectory.

    Uses ``EPIC14_TRAJECTORY`` from dataset_builder (already FROZEN).
    """
    return build_dataset_from_review_trajectories(
        [EPIC14_TRAJECTORY],
        train_ratio=0.7,
        val_ratio=0.15,
        seed=42,
    )


def _mock_phase1_review_runner(
    command_body: str,
    *,
    config: dict | None = None,
) -> Phase1Result:
    """Mock Phase 1 runner that simulates SkillOpt mutations on code-review body.

    Mutates Strategy-like content to prove the pipeline exercises Phase 1.
    """
    mutated = command_body.replace(
        "Review the implementation against acceptance criteria and quality standards:",
        (
            "Perform adversarial code review with focus on convergence signals: "
            "P0 findings must monotonically decrease across rounds."
        ),
    )
    return Phase1Result(
        best_body=mutated,
        mutated_regions=set(),  # code-review.md has no ## Strategy section
        elapsed=0.05,
    )


def _mock_phase2_review_runner(
    command_body: str,
    *,
    config: dict | None = None,
) -> Phase2Result:
    """Mock Phase 2 runner that simulates GEPA mutations on code-review body.

    Adds convergence-aware procedure steps.
    """
    evolved = command_body.replace(
        "Report findings with severity: 🔴 Critical, 🟡 Warning, 🟢 OK.",
        (
            "Report findings with severity: 🔴 Critical, 🟡 Warning, 🟢 OK.\n\n"
            "## Convergence Tracking\n"
            "- Track P0 count per round (must monotonically decrease)\n"
            "- Halt if P0 flat for 2+ rounds (L-19 signal)\n"
            "- Target: P0=0 by round 6 (Epic 14 reference trajectory)"
        ),
    )
    return Phase2Result(
        evolved_body=evolved,
        mutated_regions=set(),  # code-review.md has no standard region headers
        elapsed=0.10,
        cost_estimate=0.25,
    )


# ── Kill criteria documentation ────────────────────────────────────────

_KILL_CRITERIA = {
    "no_dataset_signal": (
        "Epic 14 trajectory produces zero examples — likely all rounds "
        "have 'no changes' in notes AND empty fix_commit_sha. "
        "Kill: cannot train without labeled data."
    ),
    "metric_load_failure": (
        "code_review_convergence_v1.yaml missing or malformed. "
        "Kill: FROZEN metric is the evaluation contract."
    ),
    "pipeline_no_mutation": (
        "Stacked pipeline produced identical body after mock mutation. "
        "Likely: code-review.md has no mutateable sections matching "
        "the mock runner's find/replace targets. "
        "Kill criteria: real runners must target ## headings in command body."
    ),
    "oi2_validation_failure": (
        "OI-2 disjoint-region validation failed. "
        "Kill: Phase 1 and Phase 2 region sets overlap."
    ),
}


# ── Smoke test ─────────────────────────────────────────────────────────


@pytest.mark.smoke
class TestCodeReviewPOCSmokeRun:
    """D-38 smoke: run the code-review POC pipeline end-to-end.

    Validates:
      1. FROZEN metric loads and has expected structure.
      2. Epic 14 R1-R6 trajectory produces a non-empty dataset.
      3. Stacked pipeline on code-review.md with mock runners produces
         a StackedPipelineResult.
      4. Pipeline body differs from baseline OR kill criteria documented.
      5. OI-2 validation passes (no region overlap).
    """

    def test_frozen_metric_loads(self) -> None:
        """AC-1: code_review_convergence_v1 metric YAML loads and has
        expected structure (name, version, weights, hard_gates)."""
        data = _load_metric()
        assert data["name"] == "code_review_convergence_v1"
        assert data["version"] == 1
        assert data["freeze_date"] == "2026-06-05"
        assert "weights" in data
        assert "hard_gates" in data

        weights = data["weights"]
        assert set(weights.keys()) == {
            "p0_drop_rate",
            "fix_round_efficiency",
            "nit_introduction_rate",
        }
        total = sum(weights.values())
        assert total == pytest.approx(1.0)

        gates = data["hard_gates"]
        assert len(gates) >= 1
        for gate in gates:
            assert "name" in gate
            assert "op" in gate

    def test_epic14_trajectory_produces_dataset(self) -> None:
        """AC-2: Epic 14 R1-R6 canonical trajectory produces a non-empty
        EvalDataset with correct label distribution."""
        dataset = _build_epic14_dataset()
        all_examples = dataset.all_examples
        assert len(all_examples) > 0, (
            "Epic 14 trajectory produced zero examples — "
            + _KILL_CRITERIA["no_dataset_signal"]
        )

        # R1-R6: R1(P0=2→0.0), R2(P0=4→0.0), R3(P0=1→0.0),
        # R4(P0=1→0.0), R5(P0=3→0.0), R6(P0=0→1.0)
        labels = [ex.label for ex in all_examples]
        assert 0.0 in labels, "Expected at least one non-converging (P0>0) example"
        assert 1.0 in labels, "Expected at least one converging (P0=0) example"

        # All examples should have category='code-review' and source='review-trajectory'
        for ex in all_examples:
            assert ex.category == "code-review"
            assert ex.source == "review-trajectory"

    def test_epic14_dataset_has_expected_split_sizes(self) -> None:
        """AC-3: Dataset split sizes are reasonable for 6-round trajectory."""
        dataset = _build_epic14_dataset()
        total = len(dataset.all_examples)
        assert total == 6, f"Expected 6 examples (R1-R6), got {total}"

        # With 6 examples: train≈4, val≈1, holdout≈1
        assert len(dataset.train) >= 1, "Train split is empty"
        assert len(dataset.val) >= 1, "Val split is empty"
        # holdout may be 0 or 1 with 6 examples

    def test_stacked_pipeline_on_code_review_command(self) -> None:
        """AC-4: Stacked pipeline runs on code-review.md and produces a
        StackedPipelineResult.  Body may or may not differ (mock runners
        do simple find/replace)."""
        command_text = _load_code_review_command()

        result = run_stacked_pipeline(
            command_text,
            phase1_runner=_mock_phase1_review_runner,
            phase2_runner=_mock_phase2_review_runner,
        )

        assert isinstance(result, StackedPipelineResult)
        assert result.phase1 is not None
        assert result.phase2 is not None
        assert result.total_elapsed >= 0.0

    def test_pipeline_preserves_frontmatter(self) -> None:
        """AC-5: Original frontmatter (spec block) is preserved through
        the stacked pipeline."""
        command_text = _load_code_review_command()

        result = run_stacked_pipeline(
            command_text,
            phase1_runner=_mock_phase1_review_runner,
            phase2_runner=_mock_phase2_review_runner,
        )

        assert result.success is True
        # Frontmatter should contain the persona spec
        assert "persona: QA" in result.frontmatter
        assert "verification:" in result.frontmatter

    def test_pipeline_produces_signal_or_documents_kill_criteria(self) -> None:
        """AC-6: Core smoke assertion — pipeline produces a different body
        OR we document the kill criteria explaining why not.

        This is the D-38 gate: prove the pipeline composes, or kill the POC.
        """
        command_text = _load_code_review_command()
        baseline_body = command_text  # full text for comparison

        result = run_stacked_pipeline(
            command_text,
            phase1_runner=_mock_phase1_review_runner,
            phase2_runner=_mock_phase2_review_runner,
        )

        # ── Check OI-2 ─────────────────────────────────────────────────
        if result.oi2_validation and not result.oi2_validation.passed:
            pytest.fail(
                "OI-2 validation failed: " + result.oi2_validation.message
                + " — " + _KILL_CRITERIA["oi2_validation_failure"]
            )

        # ── Check pipeline success ─────────────────────────────────────
        assert result.success is True, (
            f"Pipeline failed: phase1.error={result.phase1.error if result.phase1 else 'N/A'}, "
            f"phase2.error={result.phase2.error if result.phase2 else 'N/A'}"
        )

        # ── Check body mutation ────────────────────────────────────────
        body_changed = result.command_text.strip() != baseline_body.strip()
        if not body_changed:
            # Document kill criteria — the pipeline ran but no mutation occurred
            # This is acceptable for D-38 scaffolding with mock runners
            pytest.xfail(
                "Pipeline produced identical body — "
                + _KILL_CRITERIA["pipeline_no_mutation"]
            )

        # ── Signal produced ────────────────────────────────────────────
        # Body was mutated → pipeline produces signal
        assert result.command_text != baseline_body

        # The mock Phase 2 added convergence tracking section
        assert "Convergence Tracking" in result.command_text or \
            "convergence" in result.command_text.lower()

    def test_metric_weights_compose_with_trajectory(self) -> None:
        """AC-7: Metric weights can be applied to trajectory data.

        Verifies the FROZEN metric dimensions (p0_drop_rate,
        fix_round_efficiency, nit_introduction_rate) can be computed
        from the Epic 14 trajectory data.
        """
        data = _load_metric()
        weights = data["weights"]
        trajectory = EPIC14_TRAJECTORY

        # p0_drop_rate: fraction of rounds where P0 decreased
        p0s = trajectory.p0_trajectory
        drops = sum(1 for i in range(1, len(p0s)) if p0s[i] < p0s[i - 1])
        total_transitions = len(p0s) - 1
        p0_drop_rate = drops / total_transitions if total_transitions > 0 else 0.0

        # fix_round_efficiency: fraction of rounds with a fix commit
        rounds_with_fix = sum(1 for r in trajectory.rounds if r.fix_commit_sha)
        fix_round_efficiency = rounds_with_fix / len(trajectory.rounds)

        # nit_introduction_rate: inverse of P2 stability
        p2s = [r.p2_count for r in trajectory.rounds]
        p2_increases = sum(1 for i in range(1, len(p2s)) if p2s[i] > p2s[i - 1])
        nit_introduction_rate = 1.0 - (p2_increases / total_transitions) if total_transitions > 0 else 1.0

        # Weighted composite
        composite = (
            weights["p0_drop_rate"] * p0_drop_rate
            + weights["fix_round_efficiency"] * fix_round_efficiency
            + weights["nit_introduction_rate"] * nit_introduction_rate
        )

        assert 0.0 <= composite <= 1.0, (
            f"Composite score {composite} out of bounds [0.0, 1.0]"
        )
        # Epic 14 converged (R6 P0=0), so composite should be > 0
        assert composite > 0.0, (
            "Epic 14 converged but composite is zero — metric computation error"
        )

    def test_dataset_examples_contain_trajectory_metadata(self) -> None:
        """AC-8: Dataset examples from Epic 14 contain expected metadata
        (spec_path, round_id, p0_trajectory)."""
        dataset = _build_epic14_dataset()

        for ex in dataset.all_examples:
            assert "spec:" in ex.task_input
            assert "round:" in ex.task_input
            assert "P0=" in ex.task_input
            assert "p0_trajectory:" in ex.task_input
            # Converging examples should say so in expected_behavior
            if ex.label == 1.0:
                assert "converging" in ex.expected_behavior.lower()
            else:
                assert "non-converging" in ex.expected_behavior.lower() or \
                    "findings must be fixed" in ex.expected_behavior.lower()
