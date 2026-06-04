"""Tests for render.py (Story 12.2)."""

import pytest
from plugins.bmad.lib.spec_schema import CommandSpec, VerificationItem
from plugins.bmad.lib.render import render_command


def _dev_spec(**overrides) -> CommandSpec:
    """Helper to build a minimal dev-story-like spec."""
    defaults = dict(
        persona="Dev",
        phase="implementation",
        verification=[
            VerificationItem(description="Tests pass", predicate="predicates.dev_story.tests_pass"),
            VerificationItem(description="No regressions"),
        ],
    )
    defaults.update(overrides)
    return CommandSpec(**defaults)


# ── Legacy passthrough ──────────────────────────────────────────────────────


class TestLegacyPassthrough:
    def test_none_spec_returns_body_unchanged(self):
        body = "Create a PRD.\n\n1. Summary\n2. Requirements\n"
        assert render_command(None, body) == body

    def test_empty_body_with_none_spec(self):
        assert render_command(None, "") == ""


# ── Imperative preamble ────────────────────────────────────────────────────


class TestImperativePreamble:
    def test_preamble_present(self):
        spec = _dev_spec()
        result = render_command(spec, "Implement the story.\n")
        assert "EXECUTE NOW. You are Dev." in result
        assert "Phase: implementation" in result

    def test_preamble_absent_when_false(self):
        spec = _dev_spec(imperative_preamble=False)
        result = render_command(spec, "Help text here.\n")
        assert "EXECUTE NOW" not in result
        assert "Help text here." in result

    def test_body_included(self):
        spec = _dev_spec()
        result = render_command(spec, "## Instructions\nDo the thing.\n")
        assert "## Instructions" in result
        assert "Do the thing." in result


# ── Verification checklist ──────────────────────────────────────────────────


class TestVerificationChecklist:
    def test_checklist_present(self):
        spec = _dev_spec()
        result = render_command(spec, "Body\n")
        assert "## Verification Checklist" in result
        assert "- [ ] Tests pass" in result
        assert "- [ ] No regressions" in result

    def test_predicate_shown(self):
        spec = _dev_spec()
        result = render_command(spec, "Body\n")
        assert "`predicates.dev_story.tests_pass`" in result

    def test_manual_check_no_predicate(self):
        spec = _dev_spec()
        result = render_command(spec, "Body\n")
        # "No regressions" has no predicate — no backtick after it
        lines = result.split("\n")
        regressions_line = [l for l in lines if "No regressions" in l][0]
        assert "`" not in regressions_line


# ── Stop condition ──────────────────────────────────────────────────────────


class TestStopCondition:
    def test_stop_with_artifacts(self):
        spec = _dev_spec(output_artifacts=["output.md", "tests/"])
        result = render_command(spec, "Body\n")
        assert "## Stop Condition" in result
        assert "`output.md`" in result
        assert "`tests/`" in result

    def test_stop_without_artifacts(self):
        spec = _dev_spec()
        result = render_command(spec, "Body\n")
        assert "Complete all verification checklist items" in result
        assert "Report results and halt." in result


# ── PreservingUndefined ─────────────────────────────────────────────────────


class TestPreservingUndefined:
    def test_missing_variable_preserved(self):
        spec = _dev_spec()
        body = "Context: {{project_name}}\n"
        result = render_command(spec, body)
        assert "{{project_name}}" in result


# ── Full render ─────────────────────────────────────────────────────────────


class TestFullRender:
    def test_all_sections_present(self):
        spec = _dev_spec(output_artifacts=["story-dev-notes.md"])
        result = render_command(spec, "Implement the story.\n", args="story-001.md")
        assert "EXECUTE NOW" in result
        assert "Implement the story." in result
        assert "## Verification Checklist" in result
        assert "## Stop Condition" in result
        assert "`story-dev-notes.md`" in result
