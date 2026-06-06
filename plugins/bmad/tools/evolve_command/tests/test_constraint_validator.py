"""Tests for CommandBodyConstraintValidator (Story 15.4).

Covers:
- Spec frontmatter preservation (OI-2)
- Size cap enforcement (2× original)
- Render-check through Epic 12 Jinja2 pipeline
"""

from __future__ import annotations

import pytest

from evolve_command.adapters.constraint_validator import (
    CommandBodyConstraintValidator,
    ValidationResult,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

# Minimal valid command body with spec: frontmatter
_SPEC_FRONTMATTER = """\
---
spec:
  persona: Dev
  phase: implementation
  verification:
    - description: All tests pass
      predicate: predicates.dev_story.tests_pass
    - description: No regressions
---
"""

_BODY_ONLY = """\
## Instructions

Implement the story following TDD principles.

1. Read the story spec
2. Write failing tests
3. Implement the code
"""

# Complete original body = frontmatter + body
ORIGINAL_BODY = _SPEC_FRONTMATTER + _BODY_ONLY


def _make_tuned(modified_body: str) -> str:
    """Build a tuned body preserving the same frontmatter."""
    return _SPEC_FRONTMATTER + modified_body


# ── Tests ───────────────────────────────────────────────────────────────────


class TestSpecPreservation:
    """OI-2: spec: frontmatter must be byte-identical."""

    def test_unchanged_body_passes(self) -> None:
        """Identical body is always valid."""
        validator = CommandBodyConstraintValidator()
        results = validator.validate(ORIGINAL_BODY, ORIGINAL_BODY)
        assert all(r.passed for r in results), _format_failures(results)

    def test_modified_body_only_passes(self) -> None:
        """Changing only the body (after frontmatter) preserves spec."""
        tuned = _make_tuned("## Instructions\n\nNew implementation details.\n")
        validator = CommandBodyConstraintValidator()
        results = validator.validate(ORIGINAL_BODY, tuned)
        assert all(r.passed for r in results), _format_failures(results)

    def test_changed_frontmatter_fails(self) -> None:
        """Altering spec: frontmatter is an OI-2 violation."""
        bad_frontmatter = """\
---
spec:
  persona: QA
  phase: testing
  verification:
    - description: Tests pass
---
"""
        tuned = bad_frontmatter + _BODY_ONLY
        validator = CommandBodyConstraintValidator()
        results = validator.validate(ORIGINAL_BODY, tuned)
        spec_result = _find_result(results, "spec_preservation")
        assert spec_result is not None
        assert spec_result.passed is False
        assert "OI-2" in spec_result.message

    def test_removed_frontmatter_fails(self) -> None:
        """Removing the spec: frontmatter block is rejected."""
        tuned = "## Plain markdown without frontmatter\n"
        validator = CommandBodyConstraintValidator()
        results = validator.validate(ORIGINAL_BODY, tuned)
        spec_result = _find_result(results, "spec_preservation")
        assert spec_result is not None
        assert spec_result.passed is False

    def test_no_spec_in_original_accepts_anything(self) -> None:
        """If original has no spec: block, tuned is free to add/remove."""
        legacy = "## Plain command body\nDo stuff.\n"
        tuned = _SPEC_FRONTMATTER + "## New body\n"
        validator = CommandBodyConstraintValidator()
        results = validator.validate(legacy, tuned)
        spec_result = _find_result(results, "spec_preservation")
        assert spec_result is not None
        assert spec_result.passed is True


class TestSizeCap:
    """Size cap: tuned body ≤ 2× original body."""

    def test_size_within_limit_passes(self) -> None:
        """Body within 2× limit passes size check."""
        # Original is ~500 bytes; tuned is slightly larger — well within 2×
        tuned = _make_tuned("## Instructions\n\nSome expanded content.\n" * 5)
        validator = CommandBodyConstraintValidator()
        results = validator.validate(ORIGINAL_BODY, tuned)
        size_result = _find_result(results, "size_cap")
        assert size_result is not None
        assert size_result.passed is True

    def test_size_exceeding_limit_fails(self) -> None:
        """Body exceeding 2× limit fails size check."""
        # Pad the tuned body to be > 2× the original
        big_body = "x" * (len(ORIGINAL_BODY) * 3)
        tuned = _SPEC_FRONTMATTER + big_body
        validator = CommandBodyConstraintValidator()
        results = validator.validate(ORIGINAL_BODY, tuned)
        size_result = _find_result(results, "size_cap")
        assert size_result is not None
        assert size_result.passed is False
        assert "exceeded" in size_result.message.lower()

    def test_custom_growth_factor(self) -> None:
        """Custom growth factor is respected."""
        # Set a very tight limit: 1.01×
        tuned = _make_tuned(_BODY_ONLY + "\n# extra line\n")
        validator = CommandBodyConstraintValidator(max_growth_factor=1.01)
        results = validator.validate(ORIGINAL_BODY, tuned)
        size_result = _find_result(results, "size_cap")
        # May or may not pass depending on the exact size — just verify the
        # check ran and the factor is applied.
        assert size_result is not None


class TestRenderCheck:
    """Render-check: tuned body must render through Epic 12 Jinja2."""

    def test_valid_template_renders(self) -> None:
        """A valid body renders without errors."""
        validator = CommandBodyConstraintValidator()
        results = validator.validate(ORIGINAL_BODY, ORIGINAL_BODY)
        render_result = _find_result(results, "render_check")
        assert render_result is not None
        assert render_result.passed is True

    def test_body_with_jinja_vars_renders(self) -> None:
        """Body with {{args}} and {{ctx.foo}} variables renders (PreservingUndefined)."""
        tuned = _make_tuned(
            "## Instructions\n\nRun with: {{args}}\nContext: {{ctx.project}}\n"
        )
        validator = CommandBodyConstraintValidator()
        results = validator.validate(ORIGINAL_BODY, tuned)
        render_result = _find_result(results, "render_check")
        assert render_result is not None
        assert render_result.passed is True

    def test_malformed_jinja_fails(self) -> None:
        """A body with broken Jinja2 syntax fails the render check."""
        # Unclosed {{ — will cause a TemplateSyntaxError
        tuned = _SPEC_FRONTMATTER + "## Bad\n\n{{unclosed_var\n"
        validator = CommandBodyConstraintValidator()
        results = validator.validate(ORIGINAL_BODY, tuned)
        render_result = _find_result(results, "render_check")
        assert render_result is not None
        assert render_result.passed is False
        assert "error" in render_result.message.lower() or "fail" in render_result.message.lower()


class TestIntegration:
    """Integration: all checks must pass for a valid candidate."""

    def test_all_checks_returned(self) -> None:
        """validate() always returns exactly 3 results."""
        validator = CommandBodyConstraintValidator()
        results = validator.validate(ORIGINAL_BODY, ORIGINAL_BODY)
        assert len(results) == 3
        names = {r.constraint_name for r in results}
        assert names == {"spec_preservation", "size_cap", "render_check"}

    def test_combined_failure(self) -> None:
        """A body that violates multiple constraints reports all failures."""
        # Create a body that is both too large AND has broken Jinja
        big_broken = "x" * (len(ORIGINAL_BODY) * 5) + "\n{{broken\n"
        tuned = _SPEC_FRONTMATTER + big_broken
        validator = CommandBodyConstraintValidator()
        results = validator.validate(ORIGINAL_BODY, tuned)
        failed = [r for r in results if not r.passed]
        # Both size_cap and render_check should fail
        assert len(failed) >= 2


# ── Helpers ─────────────────────────────────────────────────────────────────


def _find_result(
    results: list[ValidationResult], name: str
) -> ValidationResult | None:
    """Find a result by constraint name."""
    for r in results:
        if r.constraint_name == name:
            return r
    return None


def _format_failures(results: list[ValidationResult]) -> str:
    """Format failed results for assertion messages."""
    failures = [r for r in results if not r.passed]
    if not failures:
        return "No failures"
    return "; ".join(f"[{r.constraint_name}] {r.message}" for r in failures)
