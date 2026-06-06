"""Regression tests for Epic 15.2 code review findings."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
SCORE_SCRIPT = REPO_ROOT / "plugins" / "bmad" / "tools" / "evolve_command" / "scripts" / "score_output.py"
CHECK_FROZEN_SCRIPT = REPO_ROOT / "plugins" / "bmad" / "tools" / "evolve_command" / "scripts" / "check_metric_frozen.py"
METRICS_DIR = REPO_ROOT / "plugins" / "bmad" / "tools" / "evolve_command" / "metrics"
PYTHON = sys.executable


class TestMetricFreezeDates:
    """FROZEN metrics must have freeze_date > last commit date."""

    def test_all_metrics_have_future_or_past_freeze_date(self):
        """freeze_date must not equal today's date (would trip >= check)."""
        from datetime import date
        today = date.today()

        for path in METRICS_DIR.glob("*.yaml"):
            with open(path, "r") as f:
                data = yaml.safe_load(f)
            freeze_date_str = data.get("freeze_date")
            if not freeze_date_str:
                continue
            freeze_date = date.fromisoformat(str(freeze_date_str))
            # freeze_date should NOT be today (commits on same day fail)
            assert freeze_date != today, (
                f"{path.name}: freeze_date {freeze_date} == today. "
                f"Commits on same date trip last_modified >= freeze_date. "
                f"Set to tomorrow or a past date."
            )


class TestScoreOutputFrontmatterTautology:
    """Frontmatter detection must not match markdown horizontal rules."""

    def _run(self, metric_name: str, text: str) -> dict:
        result = subprocess.run(
            [PYTHON, str(SCORE_SCRIPT), metric_name, "-"],
            input=text,
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        # Parse JSON regardless of exit code (exit 1 means gates failed)
        return json.loads(result.stdout)

    def test_no_false_frontmatter_from_horizontal_rule(self):
        """Text with --- as horizontal rule should score frontmatter_complete=0.0."""
        text = """# Title

Some text.

---

More text after horizontal rule.

## Overview

Overview section.

## Findings

Findings here.

https://example.com

## Conclusions

Done.
"""
        result = self._run("research_structural_v1", text)
        # frontmatter_complete should be 0.0 because --- is a horizontal rule,
        # not YAML frontmatter at the start of the document
        assert result["dimensions"]["frontmatter_complete"]["score"] == 0.0, (
            f"frontmatter_complete scored {result['dimensions']['frontmatter_complete']['score']} "
            f"but text has no actual YAML frontmatter --- is a horizontal rule"
        )
        # Hard gate should also fail (no research_type:|research_topic:|date:)
        frontmatter_gate = [g for g in result["hard_gates"] if g["name"] == "frontmatter_required_fields"][0]
        assert frontmatter_gate["passed"] is False

    def test_real_frontmatter_scores_high(self):
        """Text with actual YAML frontmatter at start should score frontmatter_complete high."""
        text = """---
research_type: market
research_topic: AI agents
date: 2026-06-06
author: test
---

# Research Report: market

## Research Overview

Overview with methodology.

## Findings

Finding one.

## Sources

https://example.com

## Conclusions

Done.
"""
        result = self._run("research_structural_v1", text)
        assert result["dimensions"]["frontmatter_complete"]["score"] >= 0.7


class TestSmokeTestSkillPathResolution:
    """smoke_test_skill.py must resolve skill paths correctly."""

    def test_research_skill_path_exists(self):
        """bmad:research skill file must exist at expected path."""
        # The skill is in bmm/ subdirectory
        skill_path = REPO_ROOT / "skills" / "bmad" / "bmm" / "research" / "SKILL.md"
        assert skill_path.exists(), (
            f"Skill file not found at {skill_path}. "
            f"smoke_test_skill.py resolves bmad:research -> skills/bmad/research/ "
            f"but actual path is skills/bmad/bmm/research/"
        )

    def test_create_prd_skill_path_exists(self):
        """bmad:create-prd skill file must exist at expected path."""
        skill_path = REPO_ROOT / "skills" / "bmad" / "bmm" / "create-prd" / "SKILL.md"
        assert skill_path.exists()

    def test_create_architecture_skill_path_exists(self):
        """bmad:create-architecture skill file must exist at expected path."""
        skill_path = REPO_ROOT / "skills" / "bmad" / "bmm" / "create-architecture" / "SKILL.md"
        assert skill_path.exists()

    def test_epics_stories_skill_path_exists(self):
        """bmad:epics-stories skill file must exist at expected path."""
        skill_path = REPO_ROOT / "skills" / "bmad" / "bmm" / "epics-stories" / "SKILL.md"
        assert skill_path.exists()
