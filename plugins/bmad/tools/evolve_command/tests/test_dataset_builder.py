"""Tests for adapters/dataset_builder.py.

Covers:
  - AC-1: 8-file trace → EvalDataset shape
  - AC-2: Review trajectory → labeled examples (P0=0.0, non-P0=1.0)
  - AC-4: flat-for-2 detection + convergence labeling
  - AC-5: JSONL round-trip serialize/deserialize
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.dataset_builder import (
    EPIC14_TRAJECTORY,
    EvalDataset,
    EvalExample,
    ReviewRound,
    ReviewTrajectory,
    _classify_round_label,
    _has_flat_for_2,
    _is_converging,
    build_dataset_from_bmad_sessions,
    build_dataset_from_review_trajectories,
)
from importer import BMADTrace


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_trace_dir(
    tmp_path: Path,
    name: str,
    story: str = "# Story\nAcceptance: foo",
    command_body: str = "# Body\nimplement",
    project_ctx: str = "repo: test\nbranch: main",
    diff: str = "--- a/foo\n+++ b/foo\n+new",
    test_results: str = "5 passed in 0.1s",
    status: str = "status: done",
    predicates: str = "predicates:\n  - foo works",
    metadata: str = "trace_id: t1\nstory_id: S-001",
) -> Path:
    """Create a synthetic 8-file trace directory."""
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "story.md").write_text(story)
    (d / "command_body.md").write_text(command_body)
    (d / "project_context.yaml").write_text(project_ctx)
    (d / "diff.patch").write_text(diff)
    (d / "test_results.txt").write_text(test_results)
    (d / "status_update.yaml").write_text(status)
    (d / "success_predicates.yaml").write_text(predicates)
    (d / "metadata.yaml").write_text(metadata)
    return d


# ── AC-1: 8-file trace → EvalDataset shape ────────────────────────────────


class TestBuildFromBMADSessions:
    """Test build_dataset_from_bmad_sessions with synthetic 8-file traces."""

    def test_single_trace_produces_one_example(self, tmp_path: Path) -> None:
        """One trace dir → one example in the dataset."""
        trace_dir = _make_trace_dir(tmp_path, "trace_001")
        ds = build_dataset_from_bmad_sessions([trace_dir])
        assert len(ds.all_examples) == 1
        ex = ds.all_examples[0]
        assert "Story" in ex.task_input
        assert "foo" in ex.expected_behavior
        assert ex.source == "bmad-session"

    def test_multiple_traces_produce_multiple_examples(self, tmp_path: Path) -> None:
        """Three trace dirs → three examples with train/val/holdout splits."""
        dirs = [_make_trace_dir(tmp_path, f"trace_{i:03d}") for i in range(3)]
        ds = build_dataset_from_bmad_sessions(dirs)
        assert len(ds.all_examples) == 3
        # All 3 should be in train (70% of 3 ≥ 1, val 15% of 3 ≥ 1, holdout = rest)
        assert len(ds.train) >= 1

    def test_score_from_test_results(self, tmp_path: Path) -> None:
        """Score should reflect test pass rate."""
        trace_dir = _make_trace_dir(tmp_path, "trace_score", test_results="8 passed, 2 failed in 1.0s")
        ds = build_dataset_from_bmad_sessions([trace_dir])
        ex = ds.all_examples[0]
        assert ex.label == pytest.approx(0.8)

    def test_empty_trace_skipped(self, tmp_path: Path) -> None:
        """Traces with no story.md should be skipped."""
        d = tmp_path / "empty_trace"
        d.mkdir()
        # Only metadata — no story.md, so task_input is empty
        (d / "metadata.yaml").write_text("trace_id: empty\n")
        ds = build_dataset_from_bmad_sessions([d])
        assert len(ds.all_examples) == 0

    def test_missing_dir_skipped(self, tmp_path: Path) -> None:
        """Non-existent directories should be skipped without error."""
        ds = build_dataset_from_bmad_sessions([tmp_path / "nonexistent"])
        assert len(ds.all_examples) == 0

    def test_all_pass_score(self, tmp_path: Path) -> None:
        """100% pass rate → label = 1.0."""
        trace_dir = _make_trace_dir(tmp_path, "perfect", test_results="10 passed in 0.5s")
        ds = build_dataset_from_bmad_sessions([trace_dir])
        assert ds.all_examples[0].label == pytest.approx(1.0)


# ── AC-2: Review trajectory → labeled examples ────────────────────────────


class TestBuildFromReviewTrajectories:
    """Test build_dataset_from_review_trajectories with synthetic + real data."""

    def test_single_round_trajectory(self) -> None:
        """One round with P0 > 0 → one example with label 0.0."""
        traj = ReviewTrajectory(
            spec_path="test-spec.md",
            rounds=(ReviewRound("R1", p0_count=2, p1_count=5, p2_count=3),),
        )
        ds = build_dataset_from_review_trajectories([traj])
        assert len(ds.all_examples) == 1
        ex = ds.all_examples[0]
        assert ex.label == 0.0  # P0 > 0 → non-converging
        assert "P0=2" in ex.task_input
        assert ex.category == "code-review"
        assert ex.source == "review-trajectory"

    def test_epic14_trajectory_produces_six_examples(self) -> None:
        """Epic 14 R1-R6 trajectory → 6 labeled examples."""
        ds = build_dataset_from_review_trajectories([EPIC14_TRAJECTORY])
        assert len(ds.all_examples) == 6

    def test_epic14_r6_converging_label(self) -> None:
        """R6 has P0=0 → label 1.0 (converging)."""
        ds = build_dataset_from_review_trajectories([EPIC14_TRAJECTORY])
        r6_example = [e for e in ds.all_examples if "R6" in e.task_input][0]
        assert r6_example.label == 1.0
        assert "converging" in r6_example.expected_behavior.lower()

    def test_epic14_r1_non_converging_label(self) -> None:
        """R1 has P0=2 → label 0.0 (non-converging)."""
        ds = build_dataset_from_review_trajectories([EPIC14_TRAJECTORY])
        r1_example = [e for e in ds.all_examples if "round: R1" in e.task_input][0]
        assert r1_example.label == 0.0
        assert "non-converging" in r1_example.expected_behavior.lower()

    def test_skip_no_fix_commit_rounds(self) -> None:
        """Rounds with empty fix_commit_sha AND 'no changes' notes → skipped."""
        traj = ReviewTrajectory(
            spec_path="test.md",
            rounds=(
                ReviewRound("R1", p0_count=1, p1_count=2, p2_count=0, fix_commit_sha="", notes="no changes"),
                ReviewRound("R2", p0_count=0, p1_count=1, p2_count=0, fix_commit_sha="abc123"),
            ),
        )
        ds = build_dataset_from_review_trajectories([traj])
        # R1 should be skipped (no fix commit + "no changes")
        assert len(ds.all_examples) == 1
        assert "R2" in ds.all_examples[0].task_input

    def test_p0_trajectory_in_input(self) -> None:
        """Each example should include cumulative p0_trajectory."""
        traj = ReviewTrajectory(
            spec_path="test.md",
            rounds=(
                ReviewRound("R1", p0_count=3, p1_count=1, p2_count=0),
                ReviewRound("R2", p0_count=1, p1_count=2, p2_count=0, fix_commit_sha="abc"),
            ),
        )
        ds = build_dataset_from_review_trajectories([traj])
        r2_input = [e.task_input for e in ds.all_examples if "round: R2" in e.task_input][0]
        assert "p0_trajectory: 3→1" in r2_input


# ── AC-4: flat-for-2 + convergence classification ─────────────────────────


class TestConvergenceLabels:
    """Test binary label logic and L-19 halt signal detection."""

    def test_classify_p0_as_zero(self) -> None:
        """P0 > 0 → label 0.0."""
        r = ReviewRound("R1", p0_count=2, p1_count=0, p2_count=0)
        assert _classify_round_label(r) == 0.0

    def test_classify_non_p0_as_one(self) -> None:
        """P0 == 0 → label 1.0."""
        r = ReviewRound("R6", p0_count=0, p1_count=4, p2_count=4)
        assert _classify_round_label(r) == 1.0

    def test_converging_trajectory(self) -> None:
        """Monotonic P0 drop to 0 → converging."""
        traj = ReviewTrajectory(
            spec_path="test.md",
            rounds=(
                ReviewRound("R1", p0_count=3, p1_count=0, p2_count=0),
                ReviewRound("R2", p0_count=1, p1_count=0, p2_count=0),
                ReviewRound("R3", p0_count=0, p1_count=0, p2_count=0),
            ),
        )
        assert _is_converging(traj) is True

    def test_non_converging_trajectory(self) -> None:
        """P0 never reaches 0 → not converging."""
        traj = ReviewTrajectory(
            spec_path="test.md",
            rounds=(
                ReviewRound("R1", p0_count=2, p1_count=0, p2_count=0),
                ReviewRound("R2", p0_count=1, p1_count=0, p2_count=0),
                ReviewRound("R3", p0_count=2, p1_count=0, p2_count=0),
            ),
        )
        assert _is_converging(traj) is False

    def test_flat_for_2_detected(self) -> None:
        """P0=1 for R2 and R3 → flat-for-2 halt signal."""
        traj = ReviewTrajectory(
            spec_path="test.md",
            rounds=(
                ReviewRound("R1", p0_count=2, p1_count=0, p2_count=0),
                ReviewRound("R2", p0_count=1, p1_count=0, p2_count=0),
                ReviewRound("R3", p0_count=1, p1_count=0, p2_count=0),
            ),
        )
        assert _has_flat_for_2(traj) is True

    def test_monotonic_drop_no_flat(self) -> None:
        """Monotonic P0 drop → no flat-for-2."""
        traj = ReviewTrajectory(
            spec_path="test.md",
            rounds=(
                ReviewRound("R1", p0_count=3, p1_count=0, p2_count=0),
                ReviewRound("R2", p0_count=2, p1_count=0, p2_count=0),
                ReviewRound("R3", p0_count=1, p1_count=0, p2_count=0),
            ),
        )
        assert _has_flat_for_2(traj) is False

    def test_epic14_has_flat_for_2(self) -> None:
        """Epic 14 P0 trajectory (2→4→1→1→3→0) has flat-for-2 at R3-R4."""
        assert _has_flat_for_2(EPIC14_TRAJECTORY) is True

    def test_epic14_non_monotonic_but_converges(self) -> None:
        """Epic 14 eventually converges (R6 P0=0) despite non-monotonic path."""
        assert _is_converging(EPIC14_TRAJECTORY) is True


# ── AC-5: JSONL round-trip serialize/deserialize ───────────────────────────


class TestJSONLRoundTrip:
    """Test EvalDataset save/load round-trip."""

    def test_save_creates_jsonl_files(self, tmp_path: Path) -> None:
        """save() should create train.jsonl, val.jsonl, holdout.jsonl."""
        ds = EvalDataset(
            train=[EvalExample("input1", "output1", label=1.0)],
            val=[EvalExample("input2", "output2", label=0.0)],
            holdout=[],
        )
        ds.save(tmp_path)
        assert (tmp_path / "train.jsonl").exists()
        assert (tmp_path / "val.jsonl").exists()
        assert (tmp_path / "holdout.jsonl").exists()

    def test_load_restores_examples(self, tmp_path: Path) -> None:
        """load() should restore the same examples saved by save()."""
        original = EvalDataset(
            train=[
                EvalExample("task A", "behavior A", label=1.0, source="test"),
                EvalExample("task B", "behavior B", label=0.0, source="test"),
            ],
            val=[EvalExample("task C", "behavior C", label=0.5)],
            holdout=[],
        )
        original.save(tmp_path)
        loaded = EvalDataset.load(tmp_path)
        assert len(loaded.train) == 2
        assert len(loaded.val) == 1
        assert len(loaded.holdout) == 0
        assert loaded.train[0].task_input == "task A"
        assert loaded.train[0].label == 1.0
        assert loaded.train[1].label == 0.0

    def test_jsonl_format_is_one_per_line(self, tmp_path: Path) -> None:
        """Each line in the JSONL file should be valid JSON."""
        ds = EvalDataset(
            train=[EvalExample(f"input_{i}", f"output_{i}") for i in range(5)],
            val=[],
            holdout=[],
        )
        ds.save(tmp_path)
        with open(tmp_path / "train.jsonl") as f:
            lines = [line.strip() for line in f if line.strip()]
        assert len(lines) == 5
        for line in lines:
            obj = json.loads(line)
            assert "task_input" in obj
            assert "expected_behavior" in obj
            assert "label" in obj

    def test_full_pipeline_trace_to_jsonl(self, tmp_path: Path) -> None:
        """End-to-end: 8-file traces → dataset → save → load → verify."""
        trace_dirs = [
            _make_trace_dir(tmp_path, f"trace_{i}", test_results=f"{i+3} passed in 0.1s")
            for i in range(5)
        ]
        ds = build_dataset_from_bmad_sessions(trace_dirs)
        out_dir = tmp_path / "dataset_output"
        ds.save(out_dir)
        loaded = EvalDataset.load(out_dir)
        assert len(loaded.all_examples) == 5
        for ex in loaded.all_examples:
            assert "Story" in ex.task_input
            assert ex.label >= 0.0
            assert ex.label <= 1.0

    def test_full_pipeline_trajectory_to_jsonl(self, tmp_path: Path) -> None:
        """End-to-end: review trajectories → dataset → save → load → verify."""
        ds = build_dataset_from_review_trajectories([EPIC14_TRAJECTORY])
        out_dir = tmp_path / "trajectory_output"
        ds.save(out_dir)
        loaded = EvalDataset.load(out_dir)
        assert len(loaded.all_examples) == 6
        labels = [ex.label for ex in loaded.all_examples]
        assert 0.0 in labels  # R1-R5 have P0 > 0
        assert 1.0 in labels  # R6 has P0 = 0
