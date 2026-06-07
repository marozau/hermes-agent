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
    """Legacy metrics with metric/threshold/op gates are rejected by schema guard."""

    def test_dev_story_schema_guard_rejects_legacy(self):
        """Step 3: Legacy metric/threshold/op schema → exit 3 with error."""
        result = subprocess.run(
            [PYTHON, str(SCORE_SCRIPT), "dev_story_composite_v1", "-"],
            input="Run kubectl apply -f deployment.yaml to deploy.",
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 3, (
            f"Expected exit 3 (schema mismatch), got {result.returncode}. stderr: {result.stderr[:200]}"
        )
        assert "legacy" in result.stderr.lower() or "schema" in result.stderr.lower(), (
            f"Error should mention 'legacy' or 'schema'. stderr: {result.stderr[:200]}"
        )


class TestExitCode:
    """score_output.py must return non-zero exit code on hard gate failure."""

    def test_exit_code_nonzero_when_hard_gates_fail(self):
        """When hard gates fail, exit code must be non-zero."""
        result = subprocess.run(
            [PYTHON, str(SCORE_SCRIPT), "research_structural_v1", "-"],
            input="# Title\n\nNo frontmatter, no overview, no findings.",  # violates all gates
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

    def test_dev_story_rejected_by_schema_guard(self):
        """Step 3: Legacy metric is rejected, not silently false-green."""
        result = subprocess.run(
            [PYTHON, str(SCORE_SCRIPT), "dev_story_composite_v1", "-"],
            input="kubectl apply -f deploy.yaml",  # would violate no_deploy_verbs if scored
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 3, (
            f"Expected exit 3 (schema mismatch), got {result.returncode}"
        )
        assert "legacy" in result.stderr.lower() or "schema" in result.stderr.lower(), (
            f"Error should mention 'legacy' or 'schema'. stderr: {result.stderr[:200]}"
        )
