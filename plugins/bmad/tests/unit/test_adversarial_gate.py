"""Tests for the adversarial gate (Story 7.8)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from plugins.bmad.lib.adversarial_gate import (
    _build_review_goal,
    _extract_findings,
    _parse_review_result,
    run_adversarial_gate,
)
from plugins.bmad.lib.epic_anchor import StorySpec


@pytest.fixture
def story() -> StorySpec:
    return StorySpec(
        id="7.3",
        title="Implement feature X",
        description="Build the core feature",
        success_predicates=[
            "file_exists:lib/feature_x.py",
            "tests_pass:tests/test_feature_x.py",
        ],
    )


# ── Goal construction ────────────────────────────────────────────────────────


class TestBuildReviewGoal:
    def test_goal_includes_story_info(self, story):
        goal = _build_review_goal(story, Path("/tmp"))
        assert "Story 7.3" in goal
        assert "Implement feature X" in goal

    def test_goal_includes_predicates(self, story):
        goal = _build_review_goal(story, Path("/tmp"))
        assert "file_exists:lib/feature_x.py" in goal
        assert "tests_pass:tests/test_feature_x.py" in goal

    def test_goal_includes_adversarial_instructions(self, story):
        goal = _build_review_goal(story, Path("/tmp"))
        assert "adversarial" in goal.lower()
        assert "VERDICT" in goal


# ── Result parsing ────────────────────────────────────────────────────────────


class TestParseReviewResult:
    def test_parse_pass(self):
        text = "VERDICT: PASS\nAll criteria verified."
        passed, findings = _parse_review_result(text)
        assert passed is True

    def test_parse_fail_with_findings(self):
        text = (
            "VERDICT: FAIL\n"
            "FINDINGS:\n"
            "- Missing error handling in auth\n"
            "- No input validation"
        )
        passed, findings = _parse_review_result(text)
        assert passed is False
        assert "Missing error handling" in findings
        assert "No input validation" in findings

    def test_parse_empty_text(self):
        passed, findings = _parse_review_result("")
        assert passed is False
        assert "No review output" in findings

    def test_parse_heuristic_fail(self):
        text = "Multiple bugs and errors were found in the code. Broken logic."
        passed, findings = _parse_review_result(text)
        assert passed is False

    def test_parse_heuristic_pass(self):
        text = "All criteria are correct and satisfied"
        passed, findings = _parse_review_result(text)
        assert passed is True

    def test_parse_pass_case_insensitive(self):
        text = "verdict: pass"
        passed, _ = _parse_review_result(text)
        assert passed is True


# ── Findings extraction ───────────────────────────────────────────────────────


class TestExtractFindings:
    def test_extract_bullet_points(self):
        text = "FINDINGS:\n- Issue 1\n- Issue 2\n- Issue 3"
        findings = _extract_findings(text)
        assert "Issue 1" in findings
        assert "Issue 2" in findings

    def test_no_findings_section(self):
        findings = _extract_findings("No problems found.")
        assert findings == ""

    def test_mixed_format(self):
        text = "FINDINGS:\n- Bug in auth\nSome detail\n- Missing tests"
        findings = _extract_findings(text)
        assert "Bug in auth" in findings


# ── Full adversarial gate invocation ─────────────────────────────────────────


class TestRunAdversarialGate:
    @patch("plugins.bmad.lib.delegation.delegate_one")
    def test_pass_returns_true(self, mock_delegate, story):
        mock_delegate.return_value = {
            "status": "success",
            "summary": "VERDICT: PASS\nAll criteria met.",
        }
        ctx = MagicMock()
        passed, findings = run_adversarial_gate(ctx, story, Path("/tmp"))
        assert passed is True
        mock_delegate.assert_called_once()

    @patch("plugins.bmad.lib.delegation.delegate_one")
    def test_fail_returns_false(self, mock_delegate, story):
        mock_delegate.return_value = {
            "status": "success",
            "summary": "VERDICT: FAIL\nFINDINGS:\n- Missing tests",
        }
        ctx = MagicMock()
        passed, findings = run_adversarial_gate(ctx, story, Path("/tmp"))
        assert passed is False
        assert "Missing tests" in findings

    @patch("plugins.bmad.lib.delegation.delegate_one")
    def test_delegation_failure_returns_false(self, mock_delegate, story):
        mock_delegate.side_effect = RuntimeError("delegation exploded")
        ctx = MagicMock()
        passed, findings = run_adversarial_gate(ctx, story, Path("/tmp"))
        assert passed is False
        assert "delegation exploded" in findings

    @patch("plugins.bmad.lib.delegation.delegate_one")
    def test_model_override_passed(self, mock_delegate, story):
        mock_delegate.return_value = {"status": "success", "summary": "VERDICT: PASS"}
        ctx = MagicMock()
        run_adversarial_gate(
            ctx, story, Path("/tmp"), model="custom-model"
        )
        _, kwargs = mock_delegate.call_args
        assert kwargs.get("model") == "custom-model"


# Need Path for fixtures
from pathlib import Path
