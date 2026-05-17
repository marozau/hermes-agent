"""Tests for plugins.bmad.lib.phases — pure-functional phase state machine.

Tests cover:
    - PhaseRules.required_slots() at levels 1 and 2+
    - can_run() gate logic (yolo, product-brief, analysis, preceding phase)
    - next_required_slot() first-unsatisfied search
    - is_step_skipped() workflow-step skip conditions
    - template_outputs_satisfied() output verification
    - COMMAND_PHASE mapping integrity
"""

from __future__ import annotations

import pytest

from plugins.bmad.lib.phases import (
    COMMAND_PHASE,
    PHASE_ORDER,
    PhaseRules,
    SlotStatus,
    can_run,
    is_step_skipped,
    next_required_slot,
    template_outputs_satisfied,
)


# ===========================================================================
# PhaseRules
# ===========================================================================


class TestPhaseRules:
    """PhaseRules is a frozen dataclass; required_slots() is level-aware."""

    def test_level1_base_rules(self):
        """Level 1: analysis has product-brief, implementation has sprint-planning."""
        rules = PhaseRules(level=1)
        slots = rules.required_slots()

        assert slots["analysis"] == ["product-brief"]
        assert slots["planning"] == []
        assert slots["solutioning"] == []
        assert slots["implementation"] == ["sprint-planning"]

    def test_level2_adds_prd_and_architecture(self):
        """Level >= 2 adds prd to planning, architecture + solutioning-gate-check to solutioning."""
        rules = PhaseRules(level=2)
        slots = rules.required_slots()

        assert slots["analysis"] == ["product-brief"]
        assert slots["planning"] == ["prd"]
        assert slots["solutioning"] == ["architecture", "solutioning-gate-check"]
        assert slots["implementation"] == ["sprint-planning"]

    def test_level3_uses_same_rules_as_level2(self):
        """Level 3 (and above) uses the same extended rules as level 2."""
        rules = PhaseRules(level=3)
        slots = rules.required_slots()

        assert slots["analysis"] == ["product-brief"]
        assert slots["planning"] == ["prd"]
        assert slots["solutioning"] == ["architecture", "solutioning-gate-check"]
        assert slots["implementation"] == ["sprint-planning"]

    def test_frozen_dataclass(self):
        """PhaseRules instances cannot be mutated."""
        rules = PhaseRules(level=1)
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            rules.level = 2  # type: ignore[misc]

    def test_all_phases_present_in_output(self):
        """required_slots() always returns entries for all four phases."""
        for level in (0, 1, 2, 5):
            slots = PhaseRules(level=level).required_slots()
            assert set(slots.keys()) == set(PHASE_ORDER)


# ===========================================================================
# COMMAND_PHASE integrity
# ===========================================================================


class TestCommandPhase:
    """COMMAND_PHASE maps every slash command to a valid (phase, slot) tuple."""

    def test_all_values_are_valid_phase_slot_pairs(self):
        """Every command maps to a phase in PHASE_ORDER and a non-empty slot."""
        for cmd, (phase, slot) in COMMAND_PHASE.items():
            assert phase in PHASE_ORDER, f"{cmd!r} maps to unknown phase {phase!r}"
            assert isinstance(slot, str) and slot, f"{cmd!r} has empty slot"

    def test_product_brief_maps_to_analysis(self):
        assert COMMAND_PHASE["product-brief"] == ("analysis", "product-brief")

    def test_sprint_planning_maps_to_implementation(self):
        assert COMMAND_PHASE["sprint-planning"] == ("implementation", "sprint-planning")

    def test_analysis_commands_allowed(self):
        """All analysis-phase commands should be in the mapping."""
        analysis_commands = [
            cmd for cmd, (phase, _) in COMMAND_PHASE.items() if phase == "analysis"
        ]
        assert "product-brief" in analysis_commands
        assert "research" in analysis_commands
        assert "brainstorm" in analysis_commands

    def test_solutioning_has_architecture_and_gate_check(self):
        assert COMMAND_PHASE["create-architecture"] == ("solutioning", "architecture")
        assert COMMAND_PHASE["solutioning-gate-check"] == (
            "solutioning",
            "solutioning-gate-check",
        )


# ===========================================================================
# can_run()
# ===========================================================================


class TestCanRun:
    """Gate-check logic for commands based on status and level."""

    def make_status(
        self,
        overrides: dict | None = None,
    ) -> dict:
        """Helper: build a full status dict with phases nesting."""
        status = {
            "phases": {
                "analysis": {"product-brief": "not-started"},
                "planning": {},
                "solutioning": {},
                "implementation": {"sprint-planning": "not-started"},
            },
        }
        if overrides:
            for phase, slots in overrides.get("phases", {}).items():
                if phase in status["phases"]:
                    status["phases"][phase].update(slots)
                else:
                    status["phases"][phase] = slots
        return status

    # ── yolo bypass ────────────────────────────────────────────────────────

    def test_yolo_bypasses_all_gates(self):
        """When yolo=True, can_run always returns (True, '')."""
        status = self.make_status()
        # Even commands with unmet prerequisites
        assert can_run("sprint-planning", status, level=1, yolo=True) == (True, "")
        assert can_run("create-architecture", status, level=2, yolo=True) == (True, "")

    # ── product-brief ──────────────────────────────────────────────────────

    def test_product_brief_always_allowed(self):
        """product-brief is always allowed regardless of status or level."""
        status = self.make_status()
        assert can_run("product-brief", status, level=1) == (True, "")
        assert can_run("product-brief", status, level=2) == (True, "")

    # ── analysis commands ──────────────────────────────────────────────────

    def test_analysis_commands_always_allowed(self):
        """Any analysis-phase command has no preceding phase — always allowed."""
        status = self.make_status()
        for cmd in ("research", "brainstorm", "document-project", "quick-spec"):
            assert can_run(cmd, status, level=1) == (True, "")

    # ── unknown command ────────────────────────────────────────────────────

    def test_unknown_command_denied(self):
        """An unknown command returns (False, reason)."""
        status = self.make_status()
        ok, reason = can_run("nonexistent-command", status, level=1)
        assert ok is False
        assert "Unknown command" in reason

    # ── preceding phase checks ─────────────────────────────────────────────

    def test_planning_denied_when_analysis_incomplete(self):
        """planning-phase commands are denied when product-brief is not complete."""
        status = self.make_status()  # product-brief is "not-started"
        ok, reason = can_run("create-prd", status, level=1)
        assert ok is False
        assert "product-brief" in reason
        assert "not complete" in reason

    def test_planning_allowed_when_analysis_complete_level1(self):
        """planning allowed when all analysis required slots are complete (level 1)."""
        status = self.make_status({"phases": {"analysis": {"product-brief": "complete"}}})
        ok, reason = can_run("create-prd", status, level=1)
        assert ok is True, f"Expected allowed, got: {reason}"
        assert reason == ""

    def test_planning_denied_when_prd_not_complete_level2(self):
        """At level 2, planning has prd as a required slot — check preceding phase."""
        # For create-architecture (solutioning), preceding is planning which has prd
        status = self.make_status(
            {
                "phases": {
                    "analysis": {"product-brief": "complete"},
                    "planning": {"prd": "not-started"},
                },
            }
        )
        ok, reason = can_run("create-architecture", status, level=2)
        assert ok is False
        assert "prd" in reason
        assert "planning" in reason

    def test_planning_prd_complete_allows_solutioning_level2(self):
        """solutioning-phase commands allowed when preceding phase slots are complete."""
        status = self.make_status(
            {
                "phases": {
                    "analysis": {"product-brief": "complete"},
                    "planning": {"prd": "complete"},
                },
            }
        )
        ok, reason = can_run("create-architecture", status, level=2)
        assert ok is True, f"Expected allowed, got: {reason}"

    def test_sprint_planning_denied_when_solutioning_incomplete_level2(self):
        """implementation-phase commands check solutioning phase."""
        status = self.make_status(
            {
                "phases": {
                    "analysis": {"product-brief": "complete"},
                    "planning": {"prd": "complete"},
                    "solutioning": {"architecture": "not-started"},
                },
            }
        )
        ok, reason = can_run("sprint-planning", status, level=2)
        assert ok is False
        # architecture is a required slot in solutioning at level 2
        assert "architecture" in reason

    def test_sprint_planning_denied_when_analysis_incomplete(self):
        """implementation denied when preceding phases have incomplete slots."""
        status = self.make_status()  # product-brief not complete
        ok, reason = can_run("sprint-planning", status, level=1)
        assert ok is False
        assert "product-brief" in reason

    def test_missing_phase_key_in_status(self):
        """Gracefully handle a completely absent preceding phase in status."""
        status = {"phases": {"analysis": {"product-brief": "complete"}}}
        # planning has no required slots at level 1, so solutioning should be allowed
        ok, reason = can_run("create-architecture", status, level=1)
        # level 1: no required slots in planning → preceding phase OK
        assert ok is True

    def test_missing_slot_in_phase_status(self):
        """A slot missing from status gets treated as 'missing' and blocks."""
        status = {
            "phases": {
                "analysis": {},  # product-brief missing entirely
            },
        }
        ok, reason = can_run("create-prd", status, level=1)
        assert ok is False
        assert "product-brief" in reason
        assert "missing" in reason


# ===========================================================================
# next_required_slot()
# ===========================================================================


class TestNextRequiredSlot:
    """Find the first required slot (in phase order) that isn't complete.

    NOTE: next_required_slot() takes a *flat* status dict (slot → value),
    not the nested {phases: {...}} structure that can_run() uses.
    """

    def test_returns_first_incomplete_level1(self):
        """Level 1: first incomplete is product-brief."""
        result = next_required_slot({"product-brief": "not-started"}, level=1)
        assert result is not None
        assert result["phase"] == "analysis"
        assert result["slot"] == "product-brief"
        assert result["command"] == "product-brief"

    def test_after_product_brief_returns_sprint_planning_level1(self):
        """Level 1: after product-brief complete, next is sprint-planning."""
        result = next_required_slot(
            {"product-brief": "complete", "sprint-planning": "not-started"},
            level=1,
        )
        assert result is not None
        assert result["phase"] == "implementation"
        assert result["slot"] == "sprint-planning"
        assert result["command"] == "sprint-planning"

    def test_all_complete_returns_none_level1(self):
        """Level 1: all required slots complete → None."""
        result = next_required_slot(
            {"product-brief": "complete", "sprint-planning": "complete"},
            level=1,
        )
        assert result is None

    def test_in_progress_treated_as_incomplete(self):
        """'in-progress' status is treated as not yet complete."""
        result = next_required_slot(
            {"product-brief": "in-progress"},
            level=1,
        )
        assert result is not None
        assert result["slot"] == "product-brief"

    def test_none_status_treated_as_not_started(self):
        """A slot not present in status dict is treated as not-started."""
        result = next_required_slot({}, level=1)
        assert result is not None
        assert result["slot"] == "product-brief"

    def test_level2_returns_prd_after_product_brief(self):
        """Level 2: after product-brief, next is prd (planning)."""
        result = next_required_slot(
            {"product-brief": "complete", "sprint-planning": "not-started"},
            level=2,
        )
        assert result is not None
        assert result["phase"] == "planning"
        assert result["slot"] == "prd"
        assert result["command"] == "create-prd"  # first command for (planning, prd)

    def test_level2_full_chain(self):
        """Level 2: walk through all required slots in order."""
        # Step 1: nothing done → product-brief
        r1 = next_required_slot({}, level=2)
        assert r1["slot"] == "product-brief"

        # Step 2: product-brief done → prd
        r2 = next_required_slot({"product-brief": "complete"}, level=2)
        assert r2["slot"] == "prd"

        # Step 3: prd done → architecture
        r3 = next_required_slot(
            {"product-brief": "complete", "prd": "complete"},
            level=2,
        )
        assert r3["slot"] == "architecture"

        # Step 4: architecture done → solutioning-gate-check
        r4 = next_required_slot(
            {
                "product-brief": "complete",
                "prd": "complete",
                "architecture": "complete",
            },
            level=2,
        )
        assert r4["slot"] == "solutioning-gate-check"

        # Step 5: all done → sprint-planning
        r5 = next_required_slot(
            {
                "product-brief": "complete",
                "prd": "complete",
                "architecture": "complete",
                "solutioning-gate-check": "complete",
            },
            level=2,
        )
        assert r5["slot"] == "sprint-planning"

        # Step 6: all complete → None
        r6 = next_required_slot(
            {
                "product-brief": "complete",
                "prd": "complete",
                "architecture": "complete",
                "solutioning-gate-check": "complete",
                "sprint-planning": "complete",
            },
            level=2,
        )
        assert r6 is None

    def test_first_command_for_slot(self):
        """The 'command' key holds the first matching command from COMMAND_PHASE iteration order."""
        # "create-prd" appears before "validate-prd" and "edit-prd" in COMMAND_PHASE
        result = next_required_slot({"product-brief": "complete"}, level=2)
        assert result["command"] == "create-prd"


# ===========================================================================
# is_step_skipped()
# ===========================================================================


class TestIsStepSkipped:
    """Workflow-step skip logic: M3, M5, R1 skip_when conditions."""

    def test_no_skip_when_returns_false(self):
        """If no skip_when clause, step runs (not skipped)."""
        workflow = """\
step_one:
  run: echo hello
"""
        assert is_step_skipped(workflow, "step_one", {}) is False

    def test_step_not_found_returns_false(self):
        """If the step is not in the workflow, it runs (not skipped)."""
        workflow = """\
step_one:
  run: echo hello
"""
        assert is_step_skipped(workflow, "nonexistent_step", {}) is False

    def test_skip_when_equals_match(self):
        """skip_when: {slot} == <value> — skip when value matches."""
        workflow = """\
milestone_gate:
  skip_when: project_type == simple
"""
        status = {"project_type": "simple"}
        assert is_step_skipped(workflow, "milestone_gate", status) is True

    def test_skip_when_equals_no_match(self):
        """skip_when: {slot} == <value> — run when value differs."""
        workflow = """\
milestone_gate:
  skip_when: project_type == simple
"""
        status = {"project_type": "complex"}
        assert is_step_skipped(workflow, "milestone_gate", status) is False

    def test_skip_when_not_equals_match(self):
        """skip_when: {slot} != <value> — skip when value differs."""
        workflow = """\
review_gate:
  skip_when: project_type != full
"""
        status = {"project_type": "lite"}
        assert is_step_skipped(workflow, "review_gate", status) is True

    def test_skip_when_not_equals_no_match(self):
        """skip_when: {slot} != <value> — run when value matches."""
        workflow = """\
review_gate:
  skip_when: project_type != full
"""
        status = {"project_type": "full"}
        assert is_step_skipped(workflow, "review_gate", status) is False

    def test_skip_when_has_slot(self):
        """skip_when: has_slot: {slot} — skip when slot exists in status."""
        workflow = """\
impl_gate:
  skip_when: has_slot: completed_review
"""
        status = {"completed_review": "yes"}
        assert is_step_skipped(workflow, "impl_gate", status) is True

    def test_skip_when_has_slot_missing(self):
        """skip_when: has_slot: {slot} — run when slot is absent."""
        workflow = """\
impl_gate:
  skip_when: has_slot: completed_review
"""
        status = {}
        assert is_step_skipped(workflow, "impl_gate", status) is False

    def test_skip_when_missing_slot(self):
        """skip_when: missing_slot: {slot} — skip when slot is absent."""
        workflow = """\
onboarding_gate:
  skip_when: missing_slot: full_license
"""
        status = {}
        assert is_step_skipped(workflow, "onboarding_gate", status) is True

    def test_skip_when_missing_slot_present(self):
        """skip_when: missing_slot: {slot} — run when slot is present."""
        workflow = """\
onboarding_gate:
  skip_when: missing_slot: full_license
"""
        status = {"full_license": "yes"}
        assert is_step_skipped(workflow, "onboarding_gate", status) is False

    def test_multiple_steps_in_workflow(self):
        """Only the specified step's skip_when is evaluated."""
        workflow = """\
first_step:
  run: echo first

milestone_gate:
  skip_when: project_type == simple

last_step:
  run: echo done
"""
        status = {"project_type": "simple"}
        assert is_step_skipped(workflow, "milestone_gate", status) is True
        assert is_step_skipped(workflow, "first_step", status) is False
        assert is_step_skipped(workflow, "last_step", status) is False

    def test_skip_value_with_quotes(self):
        """Quoted values in skip_when are handled correctly."""
        workflow = """\
gate:
  skip_when: phase == 'complete'
"""
        status = {"phase": "complete"}
        assert is_step_skipped(workflow, "gate", status) is True

        status = {"phase": "started"}
        assert is_step_skipped(workflow, "gate", status) is False

    def test_skip_when_with_comment_lines(self):
        """Comments (#) before or after skip_when don't break parsing."""
        workflow = """\
gate:
  # This condition controls the milestone gate
  skip_when: milestone == done
"""
        status = {"milestone": "done"}
        assert is_step_skipped(workflow, "gate", status) is True


# ===========================================================================
# template_outputs_satisfied()
# ===========================================================================


class TestTemplateOutputsSatisfied:
    """M4 / M9 template-output verification."""

    def test_no_outputs_returns_true(self):
        """No outputs declared → satisfied."""
        step_text = """\
run: echo hello
"""
        assert template_outputs_satisfied(step_text, []) == (True, "")

    def test_all_outputs_written_returns_true(self):
        """All expected outputs present in writes_since_step."""
        step_text = """\
outputs:
  - docs/api.md
  - src/handler.py
"""
        writes = ["docs/api.md", "src/handler.py", "other.txt"]
        assert template_outputs_satisfied(step_text, writes) == (True, "")

    def test_missing_output_returns_false_with_reason(self):
        """A missing output produces (False, reason)."""
        step_text = """\
outputs:
  - docs/api.md
  - src/handler.py
"""
        writes = ["docs/api.md"]
        ok, reason = template_outputs_satisfied(step_text, writes)
        assert ok is False
        assert "src/handler.py" in reason
        assert "Template outputs" in reason

    def test_template_outputs_keyword(self):
        """The 'template_outputs:' keyword is recognized alongside 'outputs:'."""
        step_text = """\
template_outputs:
  - generated/report.md
"""
        writes = ["generated/report.md"]
        assert template_outputs_satisfied(step_text, writes) == (True, "")

        writes = []
        ok, reason = template_outputs_satisfied(step_text, writes)
        assert ok is False

    def test_directory_prefix_match(self):
        """A trailing / on a path matches any file under that directory."""
        step_text = """\
outputs:
  - docs/
"""
        writes = ["docs/index.html", "docs/style.css"]
        assert template_outputs_satisfied(step_text, writes) == (True, "")

        writes = ["readme.md"]
        ok, reason = template_outputs_satisfied(step_text, writes)
        assert ok is False

    def test_inline_list_syntax(self):
        """Inline list notation [a, b] is parsed correctly."""
        step_text = """\
outputs: [docs/api.md, src/handler.py]
"""
        writes = ["docs/api.md", "src/handler.py"]
        assert template_outputs_satisfied(step_text, writes) == (True, "")

    def test_multiple_missing_outputs_in_reason(self):
        """Reason lists all missing outputs."""
        step_text = """\
outputs:
  - alpha.txt
  - beta.txt
  - gamma.txt
"""
        writes = []
        ok, reason = template_outputs_satisfied(step_text, writes)
        assert ok is False
        assert "alpha.txt" in reason
        assert "beta.txt" in reason
        assert "gamma.txt" in reason

    def test_comment_lines_ignored(self):
        """Comment lines (#) inside outputs block are ignored."""
        step_text = """\
outputs:
  - important.py
  # This will be generated later
  - optional.txt
"""
        writes = ["important.py"]
        ok, reason = template_outputs_satisfied(step_text, writes)
        assert ok is False
        assert "optional.txt" in reason


# ===========================================================================
# SlotStatus type alias
# ===========================================================================


class TestSlotStatusType:
    """SlotStatus is a Literal type with expected values."""

    def test_slot_status_values(self):
        """Verify SlotStatus allows the expected literal values."""
        # Type check at runtime by verifying acceptible values
        from typing import get_args

        values = get_args(SlotStatus)
        assert "not-started" in values
        assert "in-progress" in values
        assert "complete" in values
        assert "optional" in values
        assert "required" in values


# ===========================================================================
# PHASE_ORDER
# ===========================================================================


class TestPhaseOrder:
    """PHASE_ORDER defines the canonical phase sequence."""

    def test_phase_order_sequence(self):
        """Phases appear in the expected order."""
        assert PHASE_ORDER == ["analysis", "planning", "solutioning", "implementation"]

    def test_phase_order_is_immutable_list(self):
        """PHASE_ORDER is a list (not a tuple) but should not be mutated."""
        assert isinstance(PHASE_ORDER, list)
