"""Tests for V7 (re.DOTALL on multi-line ACs) and V8 (substring matches)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCORE_SCRIPT = REPO_ROOT / "plugins" / "bmad" / "tools" / "evolve_command" / "scripts" / "score_output.py"
PYTHON = sys.executable


class TestMultiLineAcceptanceCriteria:
    """V7: Multi-line Given/When/Then ACs must be detected."""

    def _run(self, metric_name: str, text: str) -> dict:
        result = subprocess.run(
            [PYTHON, str(SCORE_SCRIPT), metric_name, "-"],
            input=text,
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        return json.loads(result.stdout)

    def test_epics_stories_metric_multiline_ac(self):
        """Standard BMAD multi-line AC format must score > 0 on criteria dimension."""
        text = """# Epics

## Epic 1

### Story 1.1

> As a user, I want login, so that I'm secure

**Acceptance Criteria:**
- [ ] Given I am on the login page
  When I enter valid credentials
  Then I am redirected to dashboard
- [ ] Given I am on the login page
  When I enter invalid credentials
  Then I see an error message

**Priority:** P0
"""
        result = self._run("epics_stories_structural_v1", text)
        assert result["dimension_scores"]["acceptance_criteria"] > 0.0, (
            f"Multi-line ACs should score > 0. Got {result['dimension_scores']['acceptance_criteria']}"
        )

    def test_epics_stories_metric_singleline_ac(self):
        """Single-line AC format should also work."""
        text = """# Epics

## Epic 1

### Story 1.1

> As a user, I want login, so that I'm secure

**Acceptance Criteria:**
- [ ] Given I am on the login page When I enter valid credentials Then I am redirected to dashboard

**Priority:** P0
"""
        result = self._run("epics_stories_structural_v1", text)
        assert result["dimension_scores"]["acceptance_criteria"] > 0.0, (
            f"Single-line ACs should score > 0. Got {result['dimension_scores']['acceptance_criteria']}"
        )


class TestCitationSubstringMatches:
    """V8: 'Sources' pattern must not match 'outsources', 'preferences', 'recitations'."""

    def _run(self, metric_name: str, text: str) -> dict:
        result = subprocess.run(
            [PYTHON, str(SCORE_SCRIPT), metric_name, "-"],
            input=text,
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        return json.loads(result.stdout)

    def test_no_false_citations_from_outsources(self):
        """Text with 'outsources' must NOT pass citations gate."""
        text = """---
research_type: market
research_topic: AI agents
date: 2026-06-06
---

# Research Report: market

## Research Overview

Overview.

## Findings

Finding 1.

## Conclusions

The company outsources its development.
"""
        result = self._run("research_structural_v1", text)
        # citations_present dimension should be 0.0 (no actual citations)
        assert result["dimension_scores"]["citations_present"] == 0.0, (
            f"'outsources' should NOT count as citation. Got {result['dimension_scores']['citations_present']}"
        )

    def test_no_false_citations_from_preferences(self):
        """Text with 'preferences' must NOT pass citations gate."""
        text = """---
research_type: market
research_topic: AI agents
date: 2026-06-06
---

# Research Report: market

## Research Overview

Overview.

## Findings

Finding 1.

## Conclusions

User preferences were analyzed.
"""
        result = self._run("research_structural_v1", text)
        assert result["dimension_scores"]["citations_present"] == 0.0, (
            f"'preferences' should NOT count as citation. Got {result['dimension_scores']['citations_present']}"
        )

    def test_actual_citations_score_high(self):
        """Text with real citations (URLs, Sources section, bracket links) should score > 0."""
        text = """---
research_type: market
research_topic: AI agents
date: 2026-06-06
---

# Research Report: market

## Research Overview

Overview.

## Findings

Finding 1.

## Sources

- https://example.com/1
- https://example.com/2

## Conclusions

Done.
"""
        result = self._run("research_structural_v1", text)
        assert result["dimension_scores"]["citations_present"] > 0.0, (
            f"Real citations should score > 0. Got {result['dimension_scores']['citations_present']}"
        )
