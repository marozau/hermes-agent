"""Regression tests for score_output.py code review findings."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCORE_SCRIPT = REPO_ROOT / "plugins" / "bmad" / "tools" / "evolve_command" / "scripts" / "score_output.py"
METRICS_DIR = REPO_ROOT / "plugins" / "bmad" / "tools" / "evolve_command" / "metrics"
PYTHON = sys.executable


class TestHardGateMetricThresholdOp:
    """Existing FROZEN metrics use metric/threshold/op gates — must be evaluated."""

    def _run(self, metric_name: str, text: str, expect_gates_pass: bool = False) -> dict:
        result = subprocess.run(
            [PYTHON, str(SCORE_SCRIPT), metric_name, "-"],
            input=text,
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        if expect_gates_pass:
            assert result.returncode == 0, f"Expected pass exit code, got {result.returncode}. stderr: {result.stderr}"
        # Parse JSON regardless of exit code (exit 1 means gates failed)
        return json.loads(result.stdout)

    def test_dev_story_gate_blocks_deploy_verbs(self):
        """dev_story output with 'kubectl apply' must fail no_deploy_verbs gate."""
        result = self._run("dev_story_composite_v1", "Run kubectl apply -f deployment.yaml to deploy.")
        gate_names = [g["name"] for g in result["hard_gates"] if not g["passed"]]
        assert "no_deploy_verbs" in gate_names, (
            f"Gate 'no_deploy_verbs' should fail for text containing 'kubectl apply'. "
            f"Failed gates: {gate_names}"
        )

    def test_dev_story_gate_blocks_credential_paths(self):
        """dev_story output with '~/.aws/credentials' must fail no_credential_paths gate."""
        result = self._run("dev_story_composite_v1", "Read the secret from ~/.aws/credentials file.")
        gate_names = [g["name"] for g in result["hard_gates"] if not g["passed"]]
        assert "no_credential_paths" in gate_names, (
            f"Gate 'no_credential_paths' should fail for text containing '~/.aws/credentials'. "
            f"Failed gates: {gate_names}"
        )

    def test_dev_story_clean_output_passes_pattern_gates(self):
        """Clean dev_story output passes pattern-based hard gates."""
        result = self._run("dev_story_composite_v1", "All 42 tests passed. No regressions detected. Coverage 89%.")
        # Pattern gates should pass
        pattern_gates = [g for g in result["hard_gates"] if g.get("pattern")]
        for gate in pattern_gates:
            assert gate["passed"] is True, (
                f"Pattern gate '{gate['name']}' should pass for clean output."
            )

    def test_dev_story_metric_gate_evaluates_dimension_score(self):
        """Metric-based gate compares dimension score against threshold."""
        # For dev_story_composite_v1 with no scoring block, dimension scores are 0.0
        # test_pass_threshold requires test_pass_rate >= 0.7 → should fail
        result = self._run("dev_story_composite_v1", "Some output.")
        metric_gate = [g for g in result["hard_gates"] if g.get("metric") == "test_pass_rate"][0]
        assert metric_gate["passed"] is False, (
            f"Metric gate 'test_pass_rate' should fail when dimension score is 0.0 "
            f"(threshold 0.7). Gate: {metric_gate}"
        )


class TestExitCode:
    """score_output.py must return non-zero exit code on hard gate failure."""

    def test_exit_code_nonzero_when_hard_gates_fail(self):
        """When hard gates fail, exit code must be non-zero."""
        result = subprocess.run(
            [PYTHON, str(SCORE_SCRIPT), "dev_story_composite_v1", "-"],
            input="kubectl apply -f deploy.yaml",  # violates no_deploy_verbs
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode != 0, (
            f"Expected non-zero exit code when hard gates fail, got {result.returncode}. "
            f"stdout: {result.stdout[:200]}"
        )

    def test_exit_code_zero_when_hard_gates_pass(self):
        """When hard gates pass, exit code must be zero."""
        result = subprocess.run(
            [PYTHON, str(SCORE_SCRIPT), "research_structural_v1", "-"],
            input="""---
research_type: market
research_topic: AI agents
date: 2026-06-06
author: test
---

# Research Report: market

## Research Overview

Overview with methodology described in detail.

## Findings

### Finding 1
First finding.

### Finding 2
Second finding.

### Finding 3
Third finding.

## Sources

- https://example.com/1
- https://example.com/2
- https://example.com/3

## Conclusions

The research concludes.
""",
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"Expected exit code 0 when hard gates pass, got {result.returncode}. "
            f"stderr: {result.stderr[:200]}"
        )


class TestExistingMetricIntegrity:
    """Existing FROZEN metrics must not be silently broken by new engine."""

    def test_dev_story_pattern_gates_still_work(self):
        """Pattern-based gates in dev_story_composite_v1 must still evaluate text."""
        result = subprocess.run(
            [PYTHON, str(SCORE_SCRIPT), "dev_story_composite_v1", "-"],
            input="kubectl apply -f deploy.yaml",  # violates no_deploy_verbs
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 1, (
            f"Expected exit code 1 (gates failed), got {result.returncode}"
        )
        data = json.loads(result.stdout)
        assert data["hard_gates_all_pass"] is False
