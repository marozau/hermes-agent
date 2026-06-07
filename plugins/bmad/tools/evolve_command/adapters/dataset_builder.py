"""BMADSessionDatasetBuilder — extracts training data from BMAD session traces
and code-review trajectories.

Two sources:
  1. **8-file session traces** (Epic 13 importer.py): each trace directory
     produces (task_input, task_output, score) triples in JSONL format.
  2. **Code-review trajectories** (Epic 14 R1-R6): each round produces
     labeled examples — P0 findings → 0.0, non-P0 findings → 1.0.

Produces EvalDataset with train/val/holdout splits serialized as JSONL.
Consumed by Story 15.6 (GEPA loop), 15.8 (SkillOpt benchmark), and
15.13 (review-trajectory extractor).
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from importer import BMADTrace, parse_test_results


# ── EvalDataset (self-contained; mirrors evolution/core/dataset_builder.py) ──


@dataclass
class EvalExample:
    """A single evaluation example."""

    task_input: str
    expected_behavior: str
    difficulty: str = "medium"
    category: str = "general"
    source: str = "bmad-session"
    label: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_input": self.task_input,
            "expected_behavior": self.expected_behavior,
            "difficulty": self.difficulty,
            "category": self.category,
            "source": self.source,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EvalExample:
        known = {k for k in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class EvalDataset:
    """Train/val/holdout split of evaluation examples."""

    train: list[EvalExample] = field(default_factory=list)
    val: list[EvalExample] = field(default_factory=list)
    holdout: list[EvalExample] = field(default_factory=list)

    @property
    def all_examples(self) -> list[EvalExample]:
        return self.train + self.val + self.holdout

    def save(self, path: Path) -> None:
        """Save dataset splits to JSONL files."""
        path.mkdir(parents=True, exist_ok=True)
        for split_name, split_data in [
            ("train", self.train),
            ("val", self.val),
            ("holdout", self.holdout),
        ]:
            with open(path / f"{split_name}.jsonl", "w") as f:
                for ex in split_data:
                    f.write(json.dumps(ex.to_dict()) + "\n")

    @classmethod
    def load(cls, path: Path) -> EvalDataset:
        """Load dataset splits from JSONL files."""
        dataset = cls()
        for split_name in ("train", "val", "holdout"):
            split_file = path / f"{split_name}.jsonl"
            if split_file.exists():
                examples: list[EvalExample] = []
                with open(split_file) as f:
                    for line in f:
                        if line.strip():
                            examples.append(EvalExample.from_dict(json.loads(line)))
                setattr(dataset, split_name, examples)
        return dataset


# ── Default split ratios ───────────────────────────────────────────────────

_DEFAULT_TRAIN_RATIO = 0.7
_DEFAULT_VAL_RATIO = 0.15
# holdout gets the remainder (0.15)


def _split_examples(
    examples: list[EvalExample],
    train_ratio: float = _DEFAULT_TRAIN_RATIO,
    val_ratio: float = _DEFAULT_VAL_RATIO,
    seed: int = 42,
) -> tuple[list[EvalExample], list[EvalExample], list[EvalExample]]:
    """Shuffle and split examples into train/val/holdout."""
    rng = random.Random(seed)
    shuffled = list(examples)
    rng.shuffle(shuffled)
    n = len(shuffled)
    n_train = max(1, int(n * train_ratio))
    n_val = max(1, int(n * val_ratio))
    return shuffled[:n_train], shuffled[n_train : n_train + n_val], shuffled[n_train + n_val :]


# ── Source 1: 8-file session traces ────────────────────────────────────────


def _build_task_input(trace: BMADTrace) -> str:
    """Compose task_input from story.md + command_body.md + project_context."""
    parts: list[str] = []
    if trace.story_md:
        parts.append(trace.story_md)
    if trace.command_body_md:
        parts.append(f"--- command_body ---\n{trace.command_body_md}")
    if trace.project_context_yaml:
        parts.append(f"--- project_context ---\n{trace.project_context_yaml}")
    return "\n\n".join(parts)


def _build_task_output(trace: BMADTrace) -> str:
    """Compose task_output from diff.patch + status_update + predicates."""
    parts: list[str] = []
    if trace.diff_patch:
        parts.append(trace.diff_patch)
    if trace.status_update_yaml:
        parts.append(f"--- status_update ---\n{trace.status_update_yaml}")
    if trace.success_predicates_yaml:
        parts.append(f"--- predicates ---\n{trace.success_predicates_yaml}")
    return "\n\n".join(parts)


def _score_from_trace(trace: BMADTrace) -> float:
    """Derive a composite score from trace test results (0.0-1.0)."""
    results = parse_test_results(trace.test_results_txt)
    return float(results.get("pass_rate", 0.0))  # type: ignore[arg-type]


def build_dataset_from_bmad_sessions(
    trace_dirs: list[Path],
    train_ratio: float = _DEFAULT_TRAIN_RATIO,
    val_ratio: float = _DEFAULT_VAL_RATIO,
    seed: int = 42,
) -> EvalDataset:
    """Build an EvalDataset from directories of 8-file BMAD session traces.

    Each trace directory must contain the 8 canonical files
    (story.md, command_body.md, project_context.yaml, diff.patch,
    test_results.txt, status_update.yaml, success_predicates.yaml,
    metadata.yaml).  Missing files are treated as empty strings.

    Args:
        trace_dirs: List of directories, each containing an 8-file trace.
        train_ratio: Fraction for train split (default 0.7).
        val_ratio: Fraction for val split (default 0.15; holdout = rest).
        seed: Random seed for reproducible splits.

    Returns:
        EvalDataset with train/val/holdout splits.
    """
    examples: list[EvalExample] = []

    for trace_dir in trace_dirs:
        if not trace_dir.is_dir():
            continue

        trace = BMADTrace.load(trace_dir)
        task_input = _build_task_input(trace)
        task_output = _build_task_output(trace)
        score = _score_from_trace(trace)

        # Skip traces with empty input (no training signal)
        if not task_input.strip():
            continue

        examples.append(
            EvalExample(
                task_input=task_input,
                expected_behavior=task_output,
                category="dev-story",
                source="bmad-session",
                label=score,
            )
        )

    if not examples:
        return EvalDataset()

    train, val, holdout = _split_examples(examples, train_ratio, val_ratio, seed)
    return EvalDataset(train=train, val=val, holdout=holdout)


# ── Source 2: Code-review trajectories (Epic 14 R1-R6) ────────────────────


@dataclass(frozen=True)
class ReviewRound:
    """A single code-review round with finding counts."""

    round_id: str
    p0_count: int
    p1_count: int
    p2_count: int
    fix_commit_sha: str = ""
    notes: str = ""


@dataclass(frozen=True)
class ReviewTrajectory:
    """A sequence of review rounds forming a convergence trajectory."""

    spec_path: str
    rounds: tuple[ReviewRound, ...]

    @property
    def p0_trajectory(self) -> list[int]:
        return [r.p0_count for r in self.rounds]


def _classify_round_label(round_obj: ReviewRound) -> float:
    """Binary label: P0 finding → 0.0 (bad), non-P0 → 1.0 (good)."""
    return 0.0 if round_obj.p0_count > 0 else 1.0


def _is_converging(trajectory: ReviewTrajectory) -> bool:
    """True if P0 trajectory reaches 0 at any point."""
    return any(p == 0 for p in trajectory.p0_trajectory)


def _has_flat_for_2(trajectory: ReviewTrajectory) -> bool:
    """True if P0 count is flat for 2+ consecutive rounds (L-19 halt signal)."""
    p0s = trajectory.p0_trajectory
    for i in range(len(p0s) - 1):
        if p0s[i] > 0 and p0s[i] == p0s[i + 1]:
            return True
    return False


def build_dataset_from_review_trajectories(
    trajectories: list[ReviewTrajectory],
    train_ratio: float = _DEFAULT_TRAIN_RATIO,
    val_ratio: float = _DEFAULT_VAL_RATIO,
    seed: int = 42,
) -> EvalDataset:
    """Build an EvalDataset from code-review trajectories.

    Each trajectory is a sequence of ReviewRound objects (e.g., R1-R6 from
    Epic 14).  Each round produces one labeled example:
      - task_input: round metadata (P0/P1/P2 counts, notes, spec path)
      - expected_behavior: convergence signal description
      - label: 0.0 if P0 > 0 (non-converging), 1.0 if P0 == 0 (converging)

    Per D-43: rounds with 0 corresponding code changes (fix_commit_sha == ""
    AND notes indicate "no changes") are skipped.

    Args:
        trajectories: List of ReviewTrajectory objects.
        train_ratio: Fraction for train split.
        val_ratio: Fraction for val split.
        seed: Random seed for reproducible splits.

    Returns:
        EvalDataset with train/val/holdout splits.
    """
    examples: list[EvalExample] = []

    for traj in trajectories:
        for i, round_obj in enumerate(traj.rounds):
            # Per D-43: skip rounds with no fix commit and no code changes
            if not round_obj.fix_commit_sha and "no changes" in round_obj.notes.lower():
                continue

            # Build task_input from round metadata
            task_input_parts = [
                f"spec: {traj.spec_path}",
                f"round: {round_obj.round_id}",
                f"findings: P0={round_obj.p0_count} P1={round_obj.p1_count} P2={round_obj.p2_count}",
            ]
            if round_obj.fix_commit_sha:
                task_input_parts.append(f"fix_commit: {round_obj.fix_commit_sha}")
            if round_obj.notes:
                task_input_parts.append(f"notes: {round_obj.notes}")

            # Add trajectory context (P0 path so far)
            p0_path = [traj.rounds[j].p0_count for j in range(i + 1)]
            task_input_parts.append(f"p0_trajectory: {'→'.join(map(str, p0_path))}")

            # Build expected_behavior from convergence signal
            label = _classify_round_label(round_obj)
            if label == 0.0:
                behavior = f"P0={round_obj.p0_count} — non-converging; findings must be fixed"
            else:
                behavior = "P0=0 — converging; all critical findings resolved"

            # Add L-19 halt signal annotation
            if _has_flat_for_2(traj):
                behavior += " [L-19: flat-for-2 halt signal detected]"

            examples.append(
                EvalExample(
                    task_input="\n".join(task_input_parts),
                    expected_behavior=behavior,
                    category="code-review",
                    source="review-trajectory",
                    label=label,
                )
            )

    if not examples:
        return EvalDataset()

    train, val, holdout = _split_examples(examples, train_ratio, val_ratio, seed)
    return EvalDataset(train=train, val=val, holdout=holdout)


# ── Epic 14 canonical trajectory (R1-R6) ──────────────────────────────────

EPIC14_TRAJECTORY = ReviewTrajectory(
    spec_path="planning-artifacts/epics-stories-fork-migration-2026-06-04.md",
    rounds=(
        ReviewRound(
            round_id="R1",
            p0_count=2,
            p1_count=7,
            p2_count=6,
            fix_commit_sha="",
            notes="initial round-0 spec review",
        ),
        ReviewRound(
            round_id="R2",
            p0_count=4,
            p1_count=8,
            p2_count=3,
            fix_commit_sha="",
            notes="regression: narrow R1-fix introduced contradictions in unmodified sites",
        ),
        ReviewRound(
            round_id="R3",
            p0_count=1,
            p1_count=7,
            p2_count=4,
            fix_commit_sha="",
            notes="partial convergence — FI-2/FD-3/sprint-plan triad fixed",
        ),
        ReviewRound(
            round_id="R4",
            p0_count=1,
            p1_count=6,
            p2_count=2,
            fix_commit_sha="66bb1d2db",
            notes="initial implementation lands",
        ),
        ReviewRound(
            round_id="R5",
            p0_count=3,
            p1_count=4,
            p2_count=3,
            fix_commit_sha="ee1197566",
            notes="regression: SHA-pin bulk-replace + AC label swap + dropped import test",
        ),
        ReviewRound(
            round_id="R6",
            p0_count=0,
            p1_count=4,
            p2_count=4,
            fix_commit_sha="dd0ef1972",
            notes="CONVERGENCE — all 3 R5 P0s fixed; 4 chronic P1 carry-forwards stable",
        ),
    ),
)
