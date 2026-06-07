"""Tests for cli_v2 — Story 15.5 stacked pipeline CLI.

Covers:
  1. Dry-run prints plan and exits without executing.
  2. Invalid dataset path is rejected with a clear error.
  3. Phase resolution maps 'both' → ('gepa', 'skillopt').
  4. Invalid JSONL line is rejected with line number.
  5. Valid dry-run with real JSONL dataset succeeds.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from evolve_command.cli_v2 import (
    PipelinePlan,
    _resolve_phases,
    _validate_dataset,
    main,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def sample_jsonl(tmp_path: Path) -> Path:
    """Create a minimal JSONL dataset file."""
    p = tmp_path / "dataset.jsonl"
    lines = [
        json.dumps({"task_input": "story A", "expected_behavior": "pass", "label": 1.0}),
        json.dumps({"task_input": "story B", "expected_behavior": "fail", "label": 0.0}),
        json.dumps({"task_input": "story C", "expected_behavior": "pass", "label": 0.8}),
    ]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


@pytest.fixture()
def empty_jsonl(tmp_path: Path) -> Path:
    """Create an empty JSONL file."""
    p = tmp_path / "empty.jsonl"
    p.write_text("", encoding="utf-8")
    return p


@pytest.fixture()
def bad_jsonl(tmp_path: Path) -> Path:
    """Create a JSONL file with one bad line."""
    p = tmp_path / "bad.jsonl"
    p.write_text(
        '{"task_input": "ok"}\nnot-json-at-all\n{"task_input": "also ok"}\n',
        encoding="utf-8",
    )
    return p


# ── Tests ───────────────────────────────────────────────────────────────────


class TestDryRun:
    """Test 1: --dry-run prints plan and does not execute the pipeline."""

    def test_dry_run_prints_plan(
        self, runner: CliRunner, sample_jsonl: Path,
    ) -> None:
        result = runner.invoke(main, [
            "--command", "dev-story",
            "--phase", "both",
            "--cost-cap", "50",
            "--dataset", str(sample_jsonl),
            "--dry-run",
        ])
        assert result.exit_code == 0, f"Exit code {result.exit_code}: {result.output}"
        assert "Pipeline Plan" in result.output
        assert "dev-story" in result.output
        assert "gepa" in result.output
        assert "skillopt" in result.output
        assert "dry-run" in result.output.lower()
        assert "3" in result.output  # example count

    def test_dry_run_gepa_only(
        self, runner: CliRunner, sample_jsonl: Path,
    ) -> None:
        result = runner.invoke(main, [
            "--command", "dev-story",
            "--phase", "gepa",
            "--dataset", str(sample_jsonl),
            "--dry-run",
        ])
        assert result.exit_code == 0
        assert "gepa" in result.output
        # skillopt should NOT appear in the phases list
        lines = result.output.splitlines()
        phase_lines = [l for l in lines if "Phase" in l and "skillopt" in l.lower()]
        assert len(phase_lines) == 0


class TestDatasetValidation:
    """Test 2 & 4: Dataset validation rejects bad inputs."""

    def test_missing_dataset_rejected(
        self, runner: CliRunner,
    ) -> None:
        result = runner.invoke(main, [
            "--command", "dev-story",
            "--dataset", "/nonexistent/path/data.jsonl",
            "--dry-run",
        ])
        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "error" in result.output.lower()

    def test_empty_dataset_rejected(
        self, runner: CliRunner, empty_jsonl: Path,
    ) -> None:
        result = runner.invoke(main, [
            "--command", "dev-story",
            "--dataset", str(empty_jsonl),
            "--dry-run",
        ])
        assert result.exit_code != 0
        assert "empty" in result.output.lower()

    def test_bad_jsonl_rejected_with_lineno(
        self, runner: CliRunner, bad_jsonl: Path,
    ) -> None:
        result = runner.invoke(main, [
            "--command", "dev-story",
            "--dataset", str(bad_jsonl),
            "--dry-run",
        ])
        assert result.exit_code != 0
        assert "line 2" in result.output.lower() or "invalid json" in result.output.lower()


class TestPhaseResolution:
    """Test 3: Phase resolution maps correctly."""

    def test_both_resolves_to_gepa_and_skillopt(self) -> None:
        assert _resolve_phases("both") == ("gepa", "skillopt")

    def test_gepa_only(self) -> None:
        assert _resolve_phases("gepa") == ("gepa",)

    def test_skillopt_only(self) -> None:
        assert _resolve_phases("skillopt") == ("skillopt",)


class TestPipelinePlan:
    """Test PipelinePlan dataclass formatting."""

    def test_format_plan_includes_all_fields(self) -> None:
        plan = PipelinePlan(
            command_name="test-cmd",
            command_body_path=None,
            command_body_text="body text here",
            phase="both",
            cost_cap=42.50,
            dataset_path=Path("/tmp/test.jsonl"),
            dataset_example_count=10,
            dry_run=True,
            phases_to_run=("gepa", "skillopt"),
        )
        output = plan.format_plan()
        assert "test-cmd" in output
        assert "42.50" in output
        assert "10" in output
        assert "gepa" in output
        assert "skillopt" in output


class TestValidateDataset:
    """Unit tests for _validate_dataset helper."""

    def test_valid_jsonl_returns_count(self, sample_jsonl: Path) -> None:
        count = _validate_dataset(sample_jsonl)
        assert count == 3

    def test_nonexistent_raises(self) -> None:
        with pytest.raises(Exception):
            _validate_dataset(Path("/nonexistent/file.jsonl"))
