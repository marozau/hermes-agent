"""CommandBodyConstraintValidator — validates tuned command bodies (Story 15.4).

Enforces three hard constraints on a candidate (tuned) command body
relative to its original:

1. **Spec preservation (OI-2)** — the ``spec:`` frontmatter block must be
   byte-identical between original and tuned body.
2. **Size cap** — the tuned body must not exceed 2× the original body size.
3. **Render-check** — the tuned body must render without Jinja2 errors
   through the Epic 12 ``render_command`` pipeline.

All three must pass; any single failure rejects the candidate.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from jinja2 import TemplateSyntaxError

from plugins.bmad.lib.render import render_command
from plugins.bmad.lib.spec_parser import parse_command_body

logger = logging.getLogger(__name__)

# ── Frontmatter extraction (same regex as spec_parser) ──────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# ── Result dataclass ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ValidationResult:
    """Result of a single constraint check."""

    passed: bool
    constraint_name: str
    message: str


# ── Validator ───────────────────────────────────────────────────────────────


class CommandBodyConstraintValidator:
    """Validates a tuned command body against hard constraints.

    Usage::

        validator = CommandBodyConstraintValidator()
        results = validator.validate(original_body, tuned_body)
        if not all(r.passed for r in results):
            # reject candidate
    """

    def __init__(self, max_growth_factor: float = 2.0) -> None:
        """Initialise the validator.

        Args:
            max_growth_factor: Maximum allowed ratio of tuned size to
                original size.  Defaults to 2.0 (i.e. tuned ≤ 2× original).
        """
        self.max_growth_factor = max_growth_factor

    # ── Public API ──────────────────────────────────────────────────────

    def validate(
        self,
        original_body: str,
        tuned_body: str,
    ) -> list[ValidationResult]:
        """Run all constraint checks.

        Args:
            original_body: The original command body (with frontmatter).
            tuned_body: The tuned command body (with frontmatter).

        Returns:
            List of ``ValidationResult``, one per check.  The candidate
            is valid only if every result has ``passed=True``.
        """
        results: list[ValidationResult] = []

        # 1. Spec frontmatter preservation (OI-2)
        results.append(self._check_spec_preserved(original_body, tuned_body))

        # 2. Size cap
        results.append(self._check_size(original_body, tuned_body))

        # 3. Render-check
        results.append(self._check_renderable(tuned_body))

        return results

    # ── Private checks ──────────────────────────────────────────────────

    @staticmethod
    def _check_spec_preserved(
        original_body: str,
        tuned_body: str,
    ) -> ValidationResult:
        """OI-2: The ``spec:`` frontmatter block must be byte-identical."""
        orig_match = _FRONTMATTER_RE.match(original_body)
        tuned_match = _FRONTMATTER_RE.match(tuned_body)

        if orig_match is None:
            # Original has no spec frontmatter — nothing to preserve.
            if tuned_match is None:
                return ValidationResult(
                    passed=True,
                    constraint_name="spec_preservation",
                    message="Neither body has spec: frontmatter — OK",
                )
            # Tuned added a frontmatter block — that's also acceptable
            # (no original spec to corrupt).
            return ValidationResult(
                passed=True,
                constraint_name="spec_preservation",
                message="Original has no spec: frontmatter — tuned may add one",
            )

        # Original has a spec block.
        orig_frontmatter = orig_match.group(0)

        if tuned_match is None:
            return ValidationResult(
                passed=False,
                constraint_name="spec_preservation",
                message="Original has spec: frontmatter but tuned body removed it",
            )

        tuned_frontmatter = tuned_match.group(0)

        if orig_frontmatter != tuned_frontmatter:
            return ValidationResult(
                passed=False,
                constraint_name="spec_preservation",
                message=(
                    "spec: frontmatter block changed (OI-2 violation). "
                    f"Original {len(orig_frontmatter)} bytes vs "
                    f"tuned {len(tuned_frontmatter)} bytes"
                ),
            )

        return ValidationResult(
            passed=True,
            constraint_name="spec_preservation",
            message="spec: frontmatter block preserved byte-identically",
        )

    def _check_size(
        self,
        original_body: str,
        tuned_body: str,
    ) -> ValidationResult:
        """Size cap: tuned body ≤ max_growth_factor × original body."""
        orig_size = len(original_body.encode("utf-8"))
        tuned_size = len(tuned_body.encode("utf-8"))
        limit = int(orig_size * self.max_growth_factor)

        if tuned_size <= limit:
            return ValidationResult(
                passed=True,
                constraint_name="size_cap",
                message=(
                    f"Size OK: {tuned_size} bytes "
                    f"(limit {limit} = {self.max_growth_factor}× {orig_size})"
                ),
            )

        return ValidationResult(
            passed=False,
            constraint_name="size_cap",
            message=(
                f"Size exceeded: {tuned_size} bytes "
                f"(limit {limit} = {self.max_growth_factor}× {orig_size}). "
                f"Over by {tuned_size - limit} bytes"
            ),
        )

    @staticmethod
    def _check_renderable(tuned_body: str) -> ValidationResult:
        """Render-check: the tuned body must render through Epic 12 Jinja2."""
        try:
            spec, body_text = parse_command_body(tuned_body)
            # Attempt to render with empty args and no ctx — this exercises
            # the Jinja2 template parser.  PreservingUndefined handles
            # missing variables gracefully, so a valid template should not
            # raise.
            render_command(spec, body_text, args="", ctx=None)
        except TemplateSyntaxError as exc:
            return ValidationResult(
                passed=False,
                constraint_name="render_check",
                message=f"Jinja2 template syntax error: {exc}",
            )
        except Exception as exc:
            return ValidationResult(
                passed=False,
                constraint_name="render_check",
                message=f"Render failed: {type(exc).__name__}: {exc}",
            )

        return ValidationResult(
            passed=True,
            constraint_name="render_check",
            message="Body renders successfully through Epic 12 Jinja2 pipeline",
        )
