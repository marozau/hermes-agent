"""Tests for structural metric scoring engine."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]  # tests/plugins/bmad -> hermes root
SCORE_SCRIPT = REPO_ROOT / "plugins" / "bmad" / "tools" / "evolve_command" / "scripts" / "score_output.py"
METRICS_DIR = REPO_ROOT / "plugins" / "bmad" / "tools" / "evolve_command" / "metrics"
PYTHON = sys.executable


class TestScoreOutputScript:
    """End-to-end tests for score_output.py"""

    def _run(self, metric_name: str, text: str) -> dict:
        result = subprocess.run(
            [PYTHON, str(SCORE_SCRIPT), metric_name, "-"],
            input=text,
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        return json.loads(result.stdout)

    def test_research_metric_with_perfect_output(self):
        """A research output with all required sections should score high."""
        text = """---
research_type: market
research_topic: AI agents
date: 2026-06-06
author: test
---

# Research Report: market

## Research Overview

This is a comprehensive overview. It has multiple sentences describing
scope and methodology in detail.

## Findings

### Finding 1
First finding with details.

### Finding 2
Second finding with details.

### Finding 3
Third finding with details.

## Sources

- https://example.com/1 (accessed 2026-06-06)
- https://example.com/2 (accessed 2026-06-06)
- https://example.com/3 (accessed 2026-06-06)

## Conclusions

The research concludes that AI agents are important.
"""
        result = self._run("research_structural_v1", text)
        assert result["hard_gates_all_pass"] is True
        assert result["hard_gates_passed"] == 4
        assert result["composite_score"] > 0.6

    def test_research_metric_with_minimal_output_fails_gates(self):
        """A minimal output missing required sections should fail hard gates."""
        text = "This is just some text without structure."
        result = self._run("research_structural_v1", text)
        assert result["hard_gates_all_pass"] is False
        assert result["hard_gates_passed"] < 4
        assert result["composite_score"] < 0.3

    def test_research_metric_missing_citations(self):
        """Missing citations should score low on that dimension."""
        text = """---
research_type: market
research_topic: AI agents
date: 2026-06-06
author: test
---

# Research Report: market

## Research Overview

Overview with methodology described.

## Findings

Some findings here.

## Conclusions

Conclusions present.
"""
        result = self._run("research_structural_v1", text)
        assert result["dimensions"]["citations_present"]["score"] <= 0.3

    def test_create_prd_metric_with_perfect_output(self):
        """A PRD with all required sections should score high."""
        text = """# Product Requirements Document: Test Project

**Date:** 2026-06-06
**Author:** test
**Version:** 1.0
**Status:** Draft

---

## Executive Summary

This PRD defines requirements for Test Project. It covers functional
and non-functional needs with clear success metrics.

## Product Goals

### Business Objectives

- Increase user engagement by 20%
- Reduce support tickets by 30%

### Success Metrics

- MAU > 1000
- NPS > 50
- Uptime > 99.9%

## Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-1 | Users can login | P0 |
| FR-2 | Users can search | P0 |
| FR-3 | Users can export | P1 |
| FR-4 | Users can share | P1 |
| FR-5 | Users can delete | P1 |

## Non-Functional Requirements

| Category | Requirement | Target |
|----------|-------------|--------|
| Performance | p95 latency | < 200ms |
| Security | OAuth2 | Required |

## Out of Scope

- Mobile app
- Offline mode

## Open Questions

- Which auth provider?
"""
        result = self._run("create_prd_structural_v1", text)
        assert result["hard_gates_all_pass"] is True
        assert result["composite_score"] > 0.2

    def test_create_architecture_metric_with_perfect_output(self):
        """An architecture doc with all sections should score high."""
        text = """# System Architecture: Test Project

**Date:** 2026-06-06
**Architect:** test
**Version:** 1.0
**Status:** Draft

---

## Architectural Drivers

1. High availability
2. Low latency
3. Security compliance

## System Overview

High-level description with components.

## Component Model

| Component | Responsibility | Interface |
|-----------|---------------|-----------|
| API | HTTP endpoints | REST |
| DB | Data storage | SQL |
| Worker | Background jobs | Queue |

## Data Model

Users, Projects, Tasks with relationships.

## API Design

| Endpoint | Method | Purpose |
|----------|--------|---------|
| /api/v1/users | GET | List users |

## Security Model

OAuth2 + RBAC.

## Deployment Architecture

Kubernetes with 3 replicas.
"""
        result = self._run("create_architecture_structural_v1", text)
        assert result["hard_gates_all_pass"] is True
        assert result["composite_score"] > 0.2

    def test_epics_stories_metric_with_perfect_output(self):
        """Epics and stories with acceptance criteria should score high."""
        text = """# Epics & User Stories: Test Project

**Date:** 2026-06-06
**Author:** test
**Version:** 1.0

---

## Epic 1: Authentication

**Business Value:** Users need to login
**Priority:** P0

### User Stories

#### Story 1.1

> As a user, I want to login, so that I can access my account

**Acceptance Criteria:**
- [ ] Given valid credentials, when I submit, then I am logged in
- [ ] Given invalid credentials, when I submit, then I see an error

**Priority:** P0
**Estimate:** M

## Epic 2: Search

**Business Value:** Users need to find content
**Priority:** P1

### User Stories

#### Story 2.1

> As a user, I want to search, so that I can find content

**Acceptance Criteria:**
- [ ] Given a query, when I search, then results appear

**Priority:** P1
**Estimate:** S
"""
        result = self._run("epics_stories_structural_v1", text)
        assert result["hard_gates_all_pass"] is True
        assert result["composite_score"] > 0.2

    def test_all_metrics_have_valid_yaml(self):
        """Every metric YAML file must be parseable and have required fields."""
        for path in METRICS_DIR.glob("*.yaml"):
            with open(path, "r") as f:
                import yaml
                data = yaml.safe_load(f)
            assert "name" in data, f"{path.name}: missing 'name'"
            assert "version" in data, f"{path.name}: missing 'version'"
            assert "freeze_date" in data, f"{path.name}: missing 'freeze_date'"
            assert "weights" in data or "hard_gates" in data, f"{path.name}: missing 'weights' and 'hard_gates'"
            # Weights must sum to ~1.0 (if present)
            if "weights" in data:
                total = sum(data["weights"].values())
                assert 0.95 <= total <= 1.05, f"{path.name}: weights sum {total}, expected ~1.0"

    def test_metric_not_found(self):
        """Non-existent metric should return error."""
        result = subprocess.run(
            [PYTHON, str(SCORE_SCRIPT), "nonexistent_metric", "-"],
            input="test",
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 1
        assert "not found" in result.stderr.lower() or "not found" in result.stdout

    def test_file_not_found(self):
        """Missing output file should return error."""
        result = subprocess.run(
            [PYTHON, str(SCORE_SCRIPT), "research_structural_v1", "/nonexistent/path"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 2
        assert "not found" in result.stderr.lower() or "not found" in result.stdout

    def test_composite_score_range(self):
        """Composite score must be in [0, 1]."""
        for metric_file in METRICS_DIR.glob("*.yaml"):
            metric_name = metric_file.stem
            # Test with empty text
            result = self._run(metric_name, "")
            assert 0.0 <= result["composite_score"] <= 1.0
            # Test with perfect text
            perfect = "---\n" + "# Title\n" * 10 + "## Overview\n" * 5 + "## Findings\n" * 3 + "https://example.com\n" * 5 + "## Conclusions\n"
            result = self._run(metric_name, perfect)
            assert 0.0 <= result["composite_score"] <= 1.0
