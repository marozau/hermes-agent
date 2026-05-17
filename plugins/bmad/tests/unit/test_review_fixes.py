"""Regression tests for defects discovered in the 2026-05-17 code review.

Each test names the original defect (CR-A / CR-1 / HI-1 / HI-5 / MED-2 / ...) so
future contributors can trace the rationale.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import jinja2
import pytest
import yaml

from plugins.bmad.lib import phases, status, templates


# ────────────────────────────────────────────────────────────────────────────
# CR-1 — mark_complete stores the artifact_path, not "complete"
# ────────────────────────────────────────────────────────────────────────────


class TestMarkCompleteStoresPath:
    """Per PRD FR-7 / Architecture A-9, mark_complete must store the path."""

    def test_stores_path_not_literal_complete(self, tmp_project_dir: Path) -> None:
        artifact = "planning-artifacts/prd-test-2026-05-17.md"
        status.mark_complete(tmp_project_dir, "planning", "prd", artifact)
        data = yaml.safe_load(
            (tmp_project_dir / "planning-artifacts" / "workflow-status.yaml").read_text()
        )
        assert data["phases"]["planning"]["prd"] == artifact
        assert data["phases"]["planning"]["prd"] != "complete"


# ────────────────────────────────────────────────────────────────────────────
# HI-3 — next_required_slot reads nested status correctly
# ────────────────────────────────────────────────────────────────────────────


class TestSlotStatusLookup:
    """phases._slot_status accepts both nested and flat status shapes."""

    def test_nested_form_correctly_resolved(self) -> None:
        state = {"phases": {"analysis": {"product-brief": "complete"}}}
        # phases.next_required_slot must NOT return product-brief — it IS complete.
        result = phases.next_required_slot(state, level=1)
        assert result is None or result["slot"] != "product-brief"

    def test_flat_form_still_works(self) -> None:
        """Tests pass flat status dicts — must keep working for backward compat."""
        result = phases.next_required_slot({"product-brief": "complete"}, level=1)
        assert result is not None
        assert result["slot"] == "sprint-planning"

    def test_path_stored_value_counts_as_complete(self) -> None:
        """A path string in the status slot is treated as complete (per CR-1 fix)."""
        state = {
            "phases": {
                "analysis": {"product-brief": "planning-artifacts/product-brief.md"},
                "planning": {"prd": "planning-artifacts/prd-foo.md"},
                "solutioning": {"architecture": "complete", "solutioning-gate-check": "complete"},
                "implementation": {"sprint-planning": "complete"},
            },
        }
        result = phases.next_required_slot(state, level=2)
        assert result is None, f"Expected all complete, got {result}"


# ────────────────────────────────────────────────────────────────────────────
# CR-3 — can_run checks ALL preceding phases, not just the immediately prior
# ────────────────────────────────────────────────────────────────────────────


class TestCanRunChecksAllPrecedingPhases:
    """Level-1 sprint-planning must require analysis.product-brief complete."""

    def test_sprint_planning_blocked_when_analysis_missing(self) -> None:
        # Level 1: analysis has product-brief required; solutioning has nothing
        # required. Implementation's immediate predecessor is solutioning which
        # is trivially satisfied (no required slots).
        state = {"phases": {
            "analysis": {"product-brief": "not-started"},
            "planning": {},
            "solutioning": {},
            "implementation": {"sprint-planning": "not-started"},
        }}
        ok, reason = phases.can_run("sprint-planning", state, level=1)
        assert not ok, "sprint-planning must be blocked by missing product-brief"
        assert "product-brief" in reason

    def test_level1_passes_when_brief_complete(self) -> None:
        state = {"phases": {
            "analysis": {"product-brief": "complete"},
        }}
        ok, reason = phases.can_run("sprint-planning", state, level=1)
        assert ok, f"sprint-planning should be allowed: {reason}"


# ────────────────────────────────────────────────────────────────────────────
# can_run also gates earlier required slots within the same phase
# ────────────────────────────────────────────────────────────────────────────


class TestCanRunSamePhaseOrdering:
    def test_solutioning_gate_blocked_until_architecture_complete(self) -> None:
        state = {"phases": {
            "analysis": {"product-brief": "complete"},
            "planning": {"prd": "complete"},
            "solutioning": {},  # architecture missing
        }}
        ok, reason = phases.can_run("solutioning-gate-check", state, level=2)
        assert not ok
        assert "architecture" in reason

    def test_solutioning_gate_passes_when_architecture_done(self) -> None:
        state = {"phases": {
            "analysis": {"product-brief": "complete"},
            "planning": {"prd": "complete"},
            "solutioning": {"architecture": "complete"},
        }}
        ok, reason = phases.can_run("solutioning-gate-check", state, level=2)
        assert ok, f"Should allow gate-check: {reason}"


# ────────────────────────────────────────────────────────────────────────────
# D-8 — solutioning-gate-check is required for level >= 2
# ────────────────────────────────────────────────────────────────────────────


class TestSolutioningGateCheckRequired:
    def test_level2_required_slots_include_gate_check(self) -> None:
        rules = phases.PhaseRules(level=2)
        assert "solutioning-gate-check" in rules.required_slots()["solutioning"]

    def test_level1_required_slots_do_not_include_gate_check(self) -> None:
        rules = phases.PhaseRules(level=1)
        assert "solutioning-gate-check" not in rules.required_slots().get("solutioning", [])

    def test_unrecognized_level_has_empty_rules(self) -> None:
        rules = phases.PhaseRules(level=99)
        for phase_slots in rules.required_slots().values():
            assert phase_slots == []


# ────────────────────────────────────────────────────────────────────────────
# HI-5 — PreservingUndefined handles filters, attribute access, iteration
# ────────────────────────────────────────────────────────────────────────────


class TestPreservingUndefinedEdgeCases:
    """PreservingUndefined must not crash on filters, attribute access, etc."""

    def test_plain_undefined_renders_literal(self) -> None:
        result = templates.render("hello {{unknown}}", {})
        assert "{{unknown}}" in result

    def test_filter_on_undefined_does_not_crash(self) -> None:
        # {{x | upper}} would historically crash on Undefined.upper()
        result = templates.render("{{unknown | upper}}", {})
        # Render produces *something* (literal placeholder or filtered) but
        # MUST NOT raise.
        assert isinstance(result, str)

    def test_attribute_access_on_undefined_does_not_crash(self) -> None:
        result = templates.render("{{unknown.attr}}", {})
        assert isinstance(result, str)

    def test_item_access_on_undefined_does_not_crash(self) -> None:
        result = templates.render("{{unknown[0]}}", {})
        assert isinstance(result, str)


# ────────────────────────────────────────────────────────────────────────────
# Templates reject jinja2 control flow per D-2 falsification
# ────────────────────────────────────────────────────────────────────────────


class TestTemplatesRejectControlFlow:
    def test_if_block_raises(self) -> None:
        with pytest.raises(ValueError, match="control flow"):
            templates.render("{% if x %}y{% endif %}", {})

    def test_for_block_raises(self) -> None:
        with pytest.raises(ValueError, match="control flow"):
            templates.render("{% for x in y %}{{x}}{% endfor %}", {})

    def test_plain_substitution_still_works(self) -> None:
        # Sanity check the rejection didn't break legitimate templates
        result = templates.render("{{project_name}}", {"project_name": "foo"})
        assert result == "foo"


# ────────────────────────────────────────────────────────────────────────────
# MED-2 — _atomic_write cleans up tmp file on exception
# ────────────────────────────────────────────────────────────────────────────


class TestAtomicWriteCleanupOnError:
    """If yaml.safe_dump or fsync raises, the tmp dotfile must be unlinked."""

    def test_tmp_cleaned_on_safe_dump_failure(self, tmp_path: Path) -> None:
        target = tmp_path / "out.yaml"
        # Pre-create the parent dir
        with mock.patch("plugins.bmad.lib.status.yaml.safe_dump", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                status._atomic_write(target, {"foo": "bar"})
        # No leftover dotfiles in the parent directory
        leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".") and p.name.endswith(".tmp")]
        assert leftovers == [], f"Tmp dotfiles leaked: {leftovers}"


# ────────────────────────────────────────────────────────────────────────────
# HI-1 — pre_tool_call blocks Read(offset|limit) on workflow files
# ────────────────────────────────────────────────────────────────────────────


class TestPreToolCallM1M7:
    """M1/M7 mandate: workflow files must be read complete."""

    def test_blocks_read_with_offset_on_workflow_xml(self) -> None:
        from plugins.bmad.hooks.pre_tool_call import pre_tool_call

        class MockCtx:
            pass
        ctx = MockCtx()
        ctx.project_dir = "/tmp/x"
        ctx.working_directory = "/tmp/x"
        ctx.profile_config = {}

        result = pre_tool_call(
            ctx,
            "Read",
            {"file_path": "/Users/x/.claude/plugins/bmad-method/skills/foo/workflow.xml", "offset": 10},
            None,
        )
        assert result is not None
        assert result["action"] == "block"
        assert "M1/M7" in result["reason"]

    def test_blocks_read_with_limit_on_skill_md(self) -> None:
        from plugins.bmad.hooks.pre_tool_call import pre_tool_call

        class MockCtx:
            pass
        ctx = MockCtx()
        ctx.project_dir = "/tmp/x"
        ctx.working_directory = "/tmp/x"
        ctx.profile_config = {}

        result = pre_tool_call(
            ctx,
            "Read",
            {"file_path": "/Users/im/.hermes/skills/bmad/bmm/create-prd/SKILL.md", "limit": 50},
            None,
        )
        assert result is not None
        assert result["action"] == "block"

    def test_allows_read_without_offset_or_limit(self) -> None:
        from plugins.bmad.hooks.pre_tool_call import pre_tool_call

        class MockCtx:
            pass
        ctx = MockCtx()
        ctx.project_dir = "/tmp/x"
        ctx.working_directory = "/tmp/x"
        ctx.profile_config = {}

        result = pre_tool_call(
            ctx,
            "Read",
            {"file_path": "/Users/im/.hermes/skills/bmad/bmm/create-prd/SKILL.md"},
            None,
        )
        assert result is None

    def test_allows_read_offset_on_non_workflow_file(self) -> None:
        from plugins.bmad.hooks.pre_tool_call import pre_tool_call

        class MockCtx:
            pass
        ctx = MockCtx()
        ctx.project_dir = "/tmp/x"
        ctx.working_directory = "/tmp/x"
        ctx.profile_config = {}

        result = pre_tool_call(
            ctx,
            "Read",
            {"file_path": "/tmp/some-other-file.py", "offset": 100, "limit": 20},
            None,
        )
        assert result is None
