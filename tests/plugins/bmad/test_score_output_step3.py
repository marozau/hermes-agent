"""Step 3: Engine schema compatibility — legacy metrics must be rejected."""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCORE_SCRIPT = REPO_ROOT / "plugins" / "bmad" / "tools" / "evolve_command" / "scripts" / "score_output.py"
PYTHON = sys.executable


class TestLegacySchemaRejection:
    """V1+V2: Legacy metric/threshold/op metrics must NOT be silently false-green."""

    def test_dev_story_composite_schema_error(self):
        """Legacy metric with metric/threshold/op gates → exit 3 + error."""
        result = subprocess.run(
            [PYTHON, str(SCORE_SCRIPT), "dev_story_composite_v1", "-"],
            input="any text",
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 3, (
            f"Expected exit 3 (schema mismatch), got {result.returncode}. stdout: {result.stdout[:200]} stderr: {result.stderr[:200]}"
        )
        assert "legacy" in result.stderr.lower() or "schema" in result.stderr.lower(), (
            f"Error message should mention 'legacy' or 'schema'. stderr: {result.stderr[:200]}"
        )

    def test_modern_metric_still_works(self):
        """Modern metric (weights + scoring) should score normally."""
        result = subprocess.run(
            [PYTHON, str(SCORE_SCRIPT), "research_structural_v1", "-"],
            input="""---
research_type: market
research_topic: AI agents
date: 2026-06-06
---

# Research Report: market

## Research Overview

Overview with methodology described.

## Findings

### Finding 1
First finding.

## Sources

- https://example.com/1
- https://example.com/2

## Conclusions

Done.
""",
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"Modern metric should score normally, got exit {result.returncode}. stderr: {result.stderr[:200]}"
        )
        data = __import__("json").loads(result.stdout)
        assert data["composite_score"] > 0.0
