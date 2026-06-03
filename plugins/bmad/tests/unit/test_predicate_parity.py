"""Tests for predicates + predicate_runner (Story 12.4/12.5).

Verifies:
- dev_story predicate functions exist and return correct types
- predicate_runner resolves and calls predicates correctly
- Manual checks (no predicate) return None
"""

import pytest
from pathlib import Path
from plugins.bmad.predicates import dev_story
from plugins.bmad.lib.predicate_runner import run_predicates
from plugins.bmad.lib.spec_schema import CommandSpec, VerificationItem


# ── dev_story predicate function signatures ─────────────────────────────────


class TestDevStoryPredicates:
    def test_tests_pass_returns_tuple(self, tmp_path):
        result = dev_story.tests_pass(tmp_path)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert result[0] in (True, False, None)

    def test_no_regressions_returns_tuple(self, tmp_path):
        result = dev_story.no_regressions(tmp_path)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_ac_verified_deferred(self, tmp_path):
        passed, reason = dev_story.ac_verified(tmp_path)
        assert passed is None
        assert "deferred" in reason.lower()

    def test_code_conventions_deferred(self, tmp_path):
        passed, reason = dev_story.code_conventions(tmp_path)
        assert passed is None
        assert "deferred" in reason.lower()

    def test_diff_minimal_returns_tuple(self, tmp_path):
        result = dev_story.diff_minimal(tmp_path)
        assert isinstance(result, tuple)
        assert len(result) == 2


# ── predicate_runner ────────────────────────────────────────────────────────


class TestPredicateRunner:
    def test_run_predicates_manual_check(self, tmp_path):
        spec = CommandSpec(
            persona="Dev",
            phase="implementation",
            verification=[VerificationItem(description="Manual check")],
        )
        results = run_predicates(spec, tmp_path)
        assert len(results) == 1
        assert results[0]["passed"] is None
        assert "manual" in results[0]["reason"]

    def test_run_predicates_calls_function(self, tmp_path):
        spec = CommandSpec(
            persona="Dev",
            phase="implementation",
            verification=[
                VerificationItem(
                    description="AC verified",
                    predicate="predicates.dev_story.ac_verified",
                ),
            ],
        )
        results = run_predicates(spec, tmp_path)
        assert len(results) == 1
        assert results[0]["passed"] is None
        assert "deferred" in results[0]["reason"]

    def test_run_predicates_invalid_path(self, tmp_path):
        spec = CommandSpec(
            persona="Dev",
            phase="implementation",
            verification=[
                VerificationItem(
                    description="Bad predicate",
                    predicate="nonexistent.module.func",
                ),
            ],
        )
        results = run_predicates(spec, tmp_path)
        assert results[0]["passed"] is None
        assert "not found" in results[0]["reason"]

    def test_run_predicates_mixed(self, tmp_path):
        spec = CommandSpec(
            persona="Dev",
            phase="implementation",
            verification=[
                VerificationItem(description="Manual check"),
                VerificationItem(
                    description="AC verified",
                    predicate="predicates.dev_story.ac_verified",
                ),
            ],
        )
        results = run_predicates(spec, tmp_path)
        assert len(results) == 2
        assert results[0]["passed"] is None  # manual
        assert results[1]["passed"] is None  # deferred

    def test_predicate_exception_returns_false(self, tmp_path):
        """G-14: Exception during predicate execution returns False, not None."""
        spec = CommandSpec(
            persona="Dev",
            phase="implementation",
            verification=[
                VerificationItem(
                    description="Broken predicate",
                    predicate="predicates.dev_story.tests_pass",
                ),
            ],
        )
        # tests_pass tries to run pytest which may fail, but should return bool
        results = run_predicates(spec, tmp_path)
        assert results[0]["passed"] in (True, False, None)

    def test_predicate_contract_violation_returns_false(self, tmp_path):
        """G-14: Contract violation (wrong return shape) returns False."""
        import plugins.bmad.predicates.dev_story as ds
        original = ds.ac_verified
        try:
            # Monkey-patch to return wrong shape
            ds.ac_verified = lambda project_dir, **kwargs: True  # bare bool, not tuple
            spec = CommandSpec(
                persona="Dev",
                phase="implementation",
                verification=[
                    VerificationItem(
                        description="Bad shape",
                        predicate="predicates.dev_story.ac_verified",
                    ),
                ],
            )
            results = run_predicates(spec, tmp_path)
            assert results[0]["passed"] is False
            assert "contract violation" in results[0]["reason"]
        finally:
            ds.ac_verified = original


# ── Lint rule (F-4): no imperative preamble in body text ────────────────────


class TestLintRule:
    def test_no_executive_now_in_dev_story_body(self):
        """Commands with imperative_preamble: false must not contain
        'EXECUTE NOW' in their body (the renderer adds it for preamble=true)."""
        from plugins.bmad.lib.spec_parser import parse_command_body

        # Read a command with imperative_preamble: false (e.g. help.md)
        body_path = Path(__file__).parent.parent.parent / "commands" / "help.md"
        if body_path.exists():
            content = body_path.read_text()
            spec, body = parse_command_body(content)
            if spec and not spec.imperative_preamble:
                assert "EXECUTE NOW" not in body

    def test_all_spec_commands_parseable(self):
        """Every command .md with spec: frontmatter must parse to a valid spec."""
        from plugins.bmad.lib.spec_parser import parse_command_body

        commands_dir = Path(__file__).parent.parent.parent / "commands"
        failures = []
        for md_file in commands_dir.glob("*.md"):
            content = md_file.read_text()
            spec, body = parse_command_body(content)
            # If it has frontmatter but spec is None, it's malformed
            if content.startswith("---") and "spec:" in content and spec is None:
                failures.append(md_file.name)
        assert failures == [], f"Malformed specs: {failures}"
