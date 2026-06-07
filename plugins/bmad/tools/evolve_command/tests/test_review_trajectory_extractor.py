"""Tests for adapters/review_trajectory_extractor.py (Story 15.13).

Covers:
  - AC-1: JSON findings block → correct P0/P1/P2 counts
  - AC-2: File discovery + trajectory assembly from multiple rounds
  - AC-3: p0-monotonic-drop invariant enforcement
  - AC-4: End-to-end: files → EvalDataset with correct labels
  - AC-5: Edge cases (missing JSON, scope parsing, filename patterns)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.review_trajectory_extractor import (
    RoundParseResult,
    _build_round,
    _check_p0_monotonic_drop,
    _classify_finding_severity,
    _count_findings_from_json,
    discover_round_files,
    extract_dataset_from_files,
    extract_trajectories_from_files,
    parse_round_file,
)
from adapters.dataset_builder import ReviewRound, ReviewTrajectory


# ── Helpers ────────────────────────────────────────────────────────────────


def _write_round_file(
    tmp_path: Path,
    filename: str,
    heading: str = "",
    scope_line: str = "",
    findings_json: list[dict] | None = None,
    findings_summary: str = "",
    extra_body: str = "",
) -> Path:
    """Create a synthetic code-review round markdown file."""
    parts: list[str] = []
    if heading:
        parts.append(heading)
    parts.append("")
    if scope_line:
        parts.append(scope_line)
        parts.append("")
    if extra_body:
        parts.append(extra_body)
        parts.append("")
    if findings_json is not None:
        parts.append("```json")
        parts.append(json.dumps(findings_json, indent=2))
        parts.append("```")
    elif findings_summary:
        parts.append(findings_summary)
    path = tmp_path / filename
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


def _make_blocker_finding(file: str = "mod.py", line: int = 42) -> dict:
    """Create a JSON finding classified as P0 (blocker)."""
    return {
        "file": file,
        "line": line,
        "summary": "ImportError at module load — BLOCKER: module crashes on import",
        "failure_scenario": "Any import of this module fails.",
    }


def _make_major_finding(file: str = "mod.py", line: int = 10) -> dict:
    """Create a JSON finding classified as P1 (major)."""
    return {
        "file": file,
        "line": line,
        "summary": "MAJOR — parallel dispatch is actually sequential",
        "failure_scenario": "No parallelism benefit.",
    }


def _make_minor_finding(file: str = "mod.py", line: int = 20) -> dict:
    """Create a JSON finding classified as P2 (minor/nit)."""
    return {
        "file": file,
        "line": line,
        "summary": "Unused import — cosmetic issue",
        "failure_scenario": "Dead code.",
    }


# ── AC-1: JSON findings block → correct P0/P1/P2 counts ───────────────────


class TestClassifyFindingSeverity:
    """Test severity classification from summary text."""

    def test_blocker_keyword_classified_as_p0(self) -> None:
        """Summary containing 'BLOCKER' → P0."""
        assert _classify_finding_severity("BLOCKER: ImportError at load") == "P0"

    def test_importerror_classified_as_p0(self) -> None:
        """Summary containing 'ImportError' → P0."""
        assert _classify_finding_severity("ImportError at module load") == "P0"

    def test_major_keyword_classified_as_p1(self) -> None:
        """Summary containing 'MAJOR' → P1."""
        assert _classify_finding_severity("MAJOR — parallel is serial") == "P1"

    def test_minor_default_classified_as_p2(self) -> None:
        """Summary with no P0/P1 keywords → P2."""
        assert _classify_finding_severity("Unused import") == "P2"

    def test_shell_injection_classified_as_p0(self) -> None:
        """Security-related summary → P0."""
        assert _classify_finding_severity("shell injection vulnerability") == "P0"

    def test_fails_open_classified_as_p0(self) -> None:
        """'fails open' → P0."""
        assert _classify_finding_severity("gate fails open on error") == "P0"


class TestCountFindingsFromJSON:
    """Test counting P0/P1/P2 from JSON findings list."""

    def test_mixed_severities(self) -> None:
        findings = [
            _make_blocker_finding(),
            _make_major_finding(),
            _make_minor_finding(),
            _make_minor_finding(),
        ]
        p0, p1, p2 = _count_findings_from_json(findings)
        assert p0 == 1
        assert p1 == 1
        assert p2 == 2

    def test_all_blockers(self) -> None:
        findings = [_make_blocker_finding() for _ in range(5)]
        p0, p1, p2 = _count_findings_from_json(findings)
        assert p0 == 5
        assert p1 == 0
        assert p2 == 0

    def test_empty_findings(self) -> None:
        p0, p1, p2 = _count_findings_from_json([])
        assert p0 == 0
        assert p1 == 0
        assert p2 == 0


class TestParseRoundFileJSON:
    """Test parsing round files with JSON findings blocks."""

    def test_json_block_parsed_correctly(self, tmp_path: Path) -> None:
        """JSON block → correct P0/P1/P2 counts."""
        findings = [
            _make_blocker_finding(),
            _make_blocker_finding(),
            _make_major_finding(),
            _make_minor_finding(),
        ]
        path = _write_round_file(
            tmp_path,
            "code-review-epic-8-round2-2026-05-31.md",
            heading="# Code Review Round 2 — Epic 8 (post-fix)",
            scope_line=(
                "**Scope:** Commit `e9fb2ad4b fix(epic8): address P0 findings` "
                "on branch `feat/main`. 3 files changed."
            ),
            findings_json=findings,
        )
        result = parse_round_file(path)
        assert result.ok
        assert result.epic_id == 8
        assert result.round_number == 2
        assert result.fix_commit_sha == "e9fb2ad4b"
        assert result.p0_count == 2
        assert result.p1_count == 1
        assert result.p2_count == 1

    def test_round1_no_round_number_in_filename(self, tmp_path: Path) -> None:
        """Filename without 'roundN' → round 1."""
        path = _write_round_file(
            tmp_path,
            "code-review-epic-7-2026-05-31.md",
            heading="# Code Review — Epic 7",
            scope_line="**Scope:** 3 commits, 3557 LOC.",
            findings_json=[_make_blocker_finding()],
        )
        result = parse_round_file(path)
        assert result.ok
        assert result.epic_id == 7
        assert result.round_number == 1

    def test_findings_summary_fallback(self, tmp_path: Path) -> None:
        """Without JSON block, parse findings summary line."""
        path = _write_round_file(
            tmp_path,
            "code-review-epic-6-2026-05-31.md",
            heading="# Code Review — Epic 6",
            scope_line="**Scope:** Planning artifacts.",
            findings_summary=(
                "**Findings:** 3 BLOCKER, 1 ANOMALY, 12 MAJOR, 9 MINOR, 3 NIT. "
                "Verified against actual files."
            ),
        )
        result = parse_round_file(path)
        assert result.ok
        assert result.epic_id == 6
        assert result.round_number == 1
        assert result.p0_count == 3
        assert result.p1_count == 12
        assert result.p2_count == 12  # 9 MINOR + 3 NIT

    def test_uncommitted_scope_no_sha(self, tmp_path: Path) -> None:
        """Scope with 'Uncommitted fixes' → empty SHA."""
        path = _write_round_file(
            tmp_path,
            "code-review-epic-7-round2-2026-05-31.md",
            heading="# Code Review Round 2 — Epic 7 (post-fix)",
            scope_line="**Scope:** Uncommitted fixes applied after round-1.",
            findings_json=[_make_major_finding()],
        )
        result = parse_round_file(path)
        assert result.ok
        assert result.fix_commit_sha == ""


# ── AC-2: File discovery + trajectory assembly ────────────────────────────


class TestExtractTrajectories:
    """Test grouping files into trajectories by epic."""

    def test_two_epics_two_trajectories(self, tmp_path: Path) -> None:
        """Files for 2 epics → 2 trajectories."""
        f1 = _write_round_file(
            tmp_path,
            "code-review-epic-6-2026-05-31.md",
            heading="# Code Review — Epic 6",
            scope_line="**Scope:** artifacts.",
            findings_json=[_make_blocker_finding()],
        )
        f2 = _write_round_file(
            tmp_path,
            "code-review-epic-7-2026-05-31.md",
            heading="# Code Review — Epic 7",
            scope_line="**Scope:** code.",
            findings_json=[_make_minor_finding()],
        )
        trajs, results = extract_trajectories_from_files([f1, f2])
        assert len(trajs) == 2
        assert trajs[0].spec_path == "planning-artifacts/code-review-epic-6"
        assert trajs[1].spec_path == "planning-artifacts/code-review-epic-7"

    def test_multiple_rounds_sorted(self, tmp_path: Path) -> None:
        """Rounds for same epic sorted by round number."""
        r1 = _write_round_file(
            tmp_path,
            "code-review-epic-8-2026-05-31.md",
            heading="# Code Review Round 1 — Epic 8",
            scope_line="**Scope:** Commit `aaa111`.",
            findings_json=[_make_blocker_finding()],
        )
        r3 = _write_round_file(
            tmp_path,
            "code-review-epic-8-round3-2026-05-31.md",
            heading="# Code Review Round 3 — Epic 8",
            scope_line="**Scope:** Commit `ccc333`.",
            findings_json=[_make_minor_finding()],
        )
        r2 = _write_round_file(
            tmp_path,
            "code-review-epic-8-round2-2026-05-31.md",
            heading="# Code Review Round 2 — Epic 8",
            scope_line="**Scope:** Commit `bbb222`.",
            findings_json=[_make_major_finding()],
        )
        # Pass in wrong order — should still sort
        trajs, _ = extract_trajectories_from_files([r3, r1, r2])
        assert len(trajs) == 1
        round_ids = [r.round_id for r in trajs[0].rounds]
        assert round_ids == ["R1", "R2", "R3"]
        assert trajs[0].rounds[0].fix_commit_sha == "aaa111"
        assert trajs[0].rounds[1].fix_commit_sha == "bbb222"
        assert trajs[0].rounds[2].fix_commit_sha == "ccc333"

    def test_rounds_with_p0_trajectory(self, tmp_path: Path) -> None:
        """P0 trajectory should be correct across rounds."""
        r1 = _write_round_file(
            tmp_path,
            "code-review-epic-8-2026-05-31.md",
            heading="# Code Review Round 1 — Epic 8",
            scope_line="**Scope:** Commit `aaa`.",
            findings_json=[_make_blocker_finding(), _make_blocker_finding()],
        )
        r2 = _write_round_file(
            tmp_path,
            "code-review-epic-8-round2-2026-05-31.md",
            heading="# Code Review Round 2 — Epic 8",
            scope_line="**Scope:** Commit `bbb`.",
            findings_json=[_make_minor_finding()],
        )
        trajs, _ = extract_trajectories_from_files([r1, r2])
        assert trajs[0].p0_trajectory == [2, 0]


# ── AC-3: p0-monotonic-drop invariant ─────────────────────────────────────


class TestP0MonotonicDrop:
    """Test p0-monotonic-drop invariant enforcement."""

    def test_no_violation_on_monotonic_drop(self) -> None:
        """P0 drops from 3 → 1 → 0 → no violation."""
        rounds = [
            ReviewRound("R1", p0_count=3, p1_count=0, p2_count=0),
            ReviewRound("R2", p0_count=1, p1_count=0, p2_count=0),
            ReviewRound("R3", p0_count=0, p1_count=0, p2_count=0),
        ]
        results = [RoundParseResult(Path(f"r{i}.md")) for i in range(3)]
        _check_p0_monotonic_drop(rounds, results)
        assert not any(r.p0_monotonic_violation for r in results)

    def test_violation_on_increase_after_zero(self) -> None:
        """P0 goes 2 → 0 → 1 → violation on R3."""
        rounds = [
            ReviewRound("R1", p0_count=2, p1_count=0, p2_count=0),
            ReviewRound("R2", p0_count=0, p1_count=0, p2_count=0),
            ReviewRound("R3", p0_count=1, p1_count=0, p2_count=0),
        ]
        results = [RoundParseResult(Path(f"r{i}.md")) for i in range(3)]
        _check_p0_monotonic_drop(rounds, results)
        assert results[0].p0_monotonic_violation is False
        assert results[1].p0_monotonic_violation is False
        assert results[2].p0_monotonic_violation is True

    def test_no_violation_when_never_reaches_zero(self) -> None:
        """P0 stays positive throughout → no violation."""
        rounds = [
            ReviewRound("R1", p0_count=5, p1_count=0, p2_count=0),
            ReviewRound("R2", p0_count=3, p1_count=0, p2_count=0),
            ReviewRound("R3", p0_count=2, p1_count=0, p2_count=0),
        ]
        results = [RoundParseResult(Path(f"r{i}.md")) for i in range(3)]
        _check_p0_monotonic_drop(rounds, results)
        assert not any(r.p0_monotonic_violation for r in results)

    def test_round4_regression_flags_violation(self) -> None:
        """Simulates Epic 8 R4-like regression: P0=5→1→0→1."""
        rounds = [
            ReviewRound("R1", p0_count=5, p1_count=4, p2_count=6),
            ReviewRound("R2", p0_count=1, p1_count=1, p2_count=1),
            ReviewRound("R3", p0_count=0, p1_count=4, p2_count=4),
            ReviewRound("R4", p0_count=1, p1_count=4, p2_count=4),
        ]
        results = [RoundParseResult(Path(f"r{i}.md")) for i in range(4)]
        _check_p0_monotonic_drop(rounds, results)
        assert results[2].p0_monotonic_violation is False  # R3 first zero
        assert results[3].p0_monotonic_violation is True   # R4 regression


# ── AC-4: End-to-end → EvalDataset with correct labels ────────────────────


class TestExtractDatasetEndToEnd:
    """Test full pipeline: files → EvalDataset with correct labels."""

    def test_converging_round_labeled_1_0(self, tmp_path: Path) -> None:
        """Round with P0=0 → label 1.0 in dataset."""
        r1 = _write_round_file(
            tmp_path,
            "code-review-epic-8-round4-2026-05-31.md",
            heading="# Code Review Round 4 — Epic 8",
            scope_line="**Scope:** Commit `ddd444`.",
            findings_json=[_make_minor_finding()],
        )
        dataset, results = extract_dataset_from_files([r1])
        assert len(results) == 1
        assert results[0].ok
        assert len(dataset.all_examples) == 1
        assert dataset.all_examples[0].label == 1.0

    def test_non_converging_round_labeled_0_0(self, tmp_path: Path) -> None:
        """Round with P0 > 0 → label 0.0 in dataset."""
        r1 = _write_round_file(
            tmp_path,
            "code-review-epic-8-2026-05-31.md",
            heading="# Code Review Round 1 — Epic 8",
            scope_line="**Scope:** Commit `2a85442a0`.",
            findings_json=[_make_blocker_finding(), _make_blocker_finding()],
        )
        dataset, results = extract_dataset_from_files([r1])
        assert len(dataset.all_examples) == 1
        assert dataset.all_examples[0].label == 0.0
        assert "P0=" in dataset.all_examples[0].task_input

    def test_multi_round_trajectory_produces_multiple_examples(
        self, tmp_path: Path,
    ) -> None:
        """3 rounds → 3 examples in dataset."""
        for i, (p0, findings) in enumerate([
            (2, [_make_blocker_finding(), _make_blocker_finding()]),
            (1, [_make_blocker_finding()]),
            (0, [_make_minor_finding()]),
        ], start=1):
            _write_round_file(
                tmp_path,
                f"code-review-epic-8{'-round' + str(i) if i > 1 else ''}-2026-05-31.md",
                heading=f"# Code Review Round {i} — Epic 8",
                scope_line=f"**Scope:** Commit `sha{i}`.",
                findings_json=findings,
            )
        files = sorted(tmp_path.glob("code-review-epic-8-*.md"))
        dataset, _ = extract_dataset_from_files(files)
        assert len(dataset.all_examples) == 3
        labels = sorted(ex.label for ex in dataset.all_examples)
        assert labels == [0.0, 0.0, 1.0]


# ── AC-5: Edge cases ──────────────────────────────────────────────────────


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_missing_file_returns_error(self, tmp_path: Path) -> None:
        """Non-existent file → parse error."""
        result = parse_round_file(tmp_path / "nonexistent.md")
        assert not result.ok
        assert "not found" in result.parse_errors[0].lower()

    def test_malformed_json_block_falls_back(self, tmp_path: Path) -> None:
        """Invalid JSON in code block → falls back to section counting."""
        path = tmp_path / "code-review-epic-8-round2-2026-05-31.md"
        path.write_text(
            "# Code Review Round 2 — Epic 8\n"
            "**Scope:** Commit `abc123`.\n"
            "```json\n"
            "[invalid json\n"
            "```\n"
            "### B-1 — Blocker finding\n"
            "Some blocker text.\n",
            encoding="utf-8",
        )
        result = parse_round_file(path)
        assert result.epic_id == 8
        assert result.round_number == 2
        assert result.fix_commit_sha == "abc123"
        # Falls back to counting ### B- sections
        assert result.p0_count >= 1

    def test_discover_round_files(self, tmp_path: Path) -> None:
        """discover_round_files finds matching files."""
        _write_round_file(
            tmp_path,
            "code-review-epic-8-2026-05-31.md",
            findings_json=[],
        )
        _write_round_file(
            tmp_path,
            "code-review-epic-8-round2-2026-05-31.md",
            findings_json=[],
        )
        # Non-matching file
        (tmp_path / "other-file.md").write_text("not a review")
        found = discover_round_files(tmp_path)
        assert len(found) == 2

    def test_discover_round_files_empty_dir(self, tmp_path: Path) -> None:
        """Empty directory → empty list."""
        assert discover_round_files(tmp_path) == []

    def test_discover_round_files_nonexistent_dir(self, tmp_path: Path) -> None:
        """Non-existent directory → empty list."""
        assert discover_round_files(tmp_path / "nope") == []

    def test_real_epic8_round2_file_integration(self) -> None:
        """Integration test against the actual Epic 8 round-2 file."""
        real_file = Path(
            "/Users/im/usr-local/hermes-bmad/planning-artifacts/"
            "code-review-epic-8-round2-2026-05-31.md"
        )
        if not real_file.is_file():
            pytest.skip("Real review file not available")
        result = parse_round_file(real_file)
        assert result.ok
        assert result.epic_id == 8
        assert result.round_number == 2
        assert result.fix_commit_sha == "e9fb2ad4b"
        # The JSON block at end has 4 findings
        assert len(result.findings_json) == 4
        assert result.p0_count + result.p1_count + result.p2_count == 4

    def test_real_epic8_round4_file_integration(self) -> None:
        """Integration test against the actual Epic 8 round-4 file."""
        real_file = Path(
            "/Users/im/usr-local/hermes-bmad/planning-artifacts/"
            "code-review-epic-8-round4-2026-05-31.md"
        )
        if not real_file.is_file():
            pytest.skip("Real review file not available")
        result = parse_round_file(real_file)
        assert result.ok
        assert result.epic_id == 8
        assert result.round_number == 4
        assert result.fix_commit_sha == "cb7f41478"
        # The JSON block at end has 1 finding
        assert len(result.findings_json) == 1

    def test_real_discover_integration(self) -> None:
        """Integration test: discover real review files."""
        base = Path("/Users/im/usr-local/hermes-bmad/planning-artifacts")
        if not base.is_dir():
            pytest.skip("Planning artifacts directory not available")
        files = discover_round_files(base)
        assert len(files) >= 6  # We know there are at least 7 files
        # All should be parseable
        for f in files:
            result = parse_round_file(f)
            assert result.epic_id > 0, f"Failed to parse epic ID from {f.name}"
