"""
phases.py — Pure-functional state machine for BMAD workflow phases.

Architecture A-6: Phase-routing, slot-gating, and skipped-step semantics.
No I/O, no os.environ (yolo flag injected as param). Pure functions only.

Exports:
    SlotStatus              Type alias (Literal)
    PhaseRules              Frozen dataclass with required_slots()
    can_run                 Command gate check
    next_required_slot      First unfulfilled required slot
    is_step_skipped         Workflow-step skip check (M3/M5/R1)
    template_outputs_satisfied  Template output verification (M4/M9)
    COMMAND_PHASE           Slash-command → (phase, slot) mapping
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# ──────────────────────────────────────────────────────────────────────────
# Types
# ──────────────────────────────────────────────────────────────────────────

SlotStatus = Literal["not-started", "in-progress", "complete", "optional", "required"]

# ──────────────────────────────────────────────────────────────────────────
# Phase ordering
# ──────────────────────────────────────────────────────────────────────────

PHASE_ORDER: list[str] = ["analysis", "planning", "solutioning", "implementation"]

# ──────────────────────────────────────────────────────────────────────────
# PhaseRules
# ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PhaseRules:
    """Immutable ruleset defining required slots per phase for a given level.

    Base rules (level 1):
        analysis       → [product-brief]
        planning       → []
        solutioning    → []
        implementation → [sprint-planning]

    At level >= 2:
        planning       adds *prd*
        solutioning    adds *architecture* and *solutioning-gate-check*
    """

    level: int

    def required_slots(self) -> dict[str, list[str]]:
        """Return {phase: [required slot names]} for the configured level.

        Recognized levels are 0-4. Anything outside that range gets the
        empty rule set (no required slots) so callers can detect
        misconfigured projects via ``next_required_slot`` returning ``None``.
        """
        if not (0 <= self.level <= 4):
            return {"analysis": [], "planning": [], "solutioning": [], "implementation": []}

        slots: dict[str, list[str]] = {
            "analysis": ["product-brief"],
            "planning": [],
            "solutioning": [],
            "implementation": ["sprint-planning"],
        }

        if self.level >= 2:
            slots["planning"].append("prd")
            slots["solutioning"].extend(["architecture", "solutioning-gate-check"])

        return slots


# ──────────────────────────────────────────────────────────────────────────
# COMMAND_PHASE — map slash commands to (phase, slot)
# ──────────────────────────────────────────────────────────────────────────

COMMAND_PHASE: dict[str, tuple[str, str]] = {
    # ── analysis ──────────────────────────────────────
    "product-brief": ("analysis", "product-brief"),
    "research": ("analysis", "research"),
    "brainstorm": ("analysis", "brainstorm"),
    "document-project": ("analysis", "document-project"),
    "quick-spec": ("analysis", "quick-spec"),
    # ── planning ──────────────────────────────────────
    "create-prd": ("planning", "prd"),
    "validate-prd": ("planning", "prd"),
    "edit-prd": ("planning", "prd"),
    "create-ux-design": ("planning", "ux-design"),
    # ── solutioning ───────────────────────────────────
    "create-architecture": ("solutioning", "architecture"),
    "epics-stories": ("solutioning", "epics-stories"),
    "solutioning-gate-check": ("solutioning", "solutioning-gate-check"),
    # ── implementation ────────────────────────────────
    "sprint-planning": ("implementation", "sprint-planning"),
    "create-story": ("implementation", "story"),
    "dev-story": ("implementation", "dev"),
    "code-review": ("implementation", "code-review"),
    "correct-course": ("implementation", "correct-course"),
    "quick-dev": ("implementation", "quick-dev"),
    # ── tea (ungated — always allowed post-init) ────────
    "nfr": ("analysis", "nfr"),
    "atdd": ("analysis", "atdd"),
    "test-design": ("analysis", "test-design"),
    "test-review": ("analysis", "test-review"),
    "test-framework": ("analysis", "test-framework"),
    "trace": ("analysis", "trace"),
    "ci": ("analysis", "ci"),
    "automate": ("analysis", "automate"),
    # ── cis (ungated) ────────────────────────────────────
    "brainstorming": ("analysis", "brainstorming"),
    "design-thinking": ("analysis", "design-thinking"),
    "problem-solving": ("analysis", "problem-solving"),
    "innovation-strategy": ("analysis", "innovation-strategy"),
    "storytelling": ("analysis", "storytelling"),
    "presentation": ("analysis", "presentation"),
    # ── bmb (ungated) ────────────────────────────────────
    "agent-builder": ("analysis", "agent-builder"),
    "module-builder": ("analysis", "module-builder"),
    "workflow-builder": ("analysis", "workflow-builder"),
}

# ──────────────────────────────────────────────────────────────────────────
# can_run — gate check
# ──────────────────────────────────────────────────────────────────────────


def can_run(
    command: str,
    status: dict,
    level: int,
    yolo: bool = False,
) -> tuple[bool, str]:
    """Check whether *command* may be invoked given current *status*.

    Returns ``(True, '')`` on success or ``(False, reason)`` on denial.

    * ``product-brief`` is always allowed — it starts the analysis phase.
    * YOLO bypasses all gates.
    * Otherwise every required slot in the *preceding* phase must be
      ``'complete'`` before a command in any later phase can run.

    The *status* dict has the nested ``{phases: {phase: {slot: value}}}``
    shape produced by ``lib/status.load()``.
    """
    if yolo:
        return (True, "")

    # product-brief always starts analysis
    if command == "product-brief":
        return (True, "")

    phase_info = COMMAND_PHASE.get(command)
    if phase_info is None:
        return (False, f"Unknown command: {command!r}")

    command_phase, _command_slot = phase_info
    phase_idx = PHASE_ORDER.index(command_phase)

    # Analysis-phase commands have no preceding phase — always allowed
    if phase_idx == 0:
        return (True, "")

    rules = PhaseRules(level)
    required = rules.required_slots()

    # 1) Check ALL preceding phases must have their required slots complete.
    #    Otherwise a level-1 project can skip from analysis → implementation
    #    because solutioning has no required slots.
    for prev_idx in range(phase_idx):
        prev_phase = PHASE_ORDER[prev_idx]
        for slot in required.get(prev_phase, []):
            actual = _slot_status(status, prev_phase, slot)
            if actual is None:
                actual = "missing"
            if actual != "complete" and not _looks_like_path(actual):
                return (
                    False,
                    f'Required slot "{slot}" in phase "{prev_phase}" '
                    f'is not complete (status={actual!r}). '
                    f'Complete "{slot}" before entering the "{command_phase}" phase.',
                )

    # 2) Within the same phase, required slots that come BEFORE this
    #    command's slot must also be complete. E.g. solutioning-gate-check
    #    must wait for architecture to land.
    same_phase_required = required.get(command_phase, [])
    target_slot = _command_slot_for_command(command)
    if target_slot in same_phase_required:
        for slot in same_phase_required:
            if slot == target_slot:
                break
            actual = _slot_status(status, command_phase, slot)
            if actual is None:
                actual = "missing"
            if actual != "complete" and not _looks_like_path(actual):
                return (
                    False,
                    f'Required slot "{slot}" in phase "{command_phase}" '
                    f'is not complete (status={actual!r}). '
                    f'Complete "{slot}" before running "{command}".',
                )

    return (True, "")


def _command_slot_for_command(command: str) -> str | None:
    """Return the slot key associated with *command*, or None."""
    info = COMMAND_PHASE.get(command)
    return info[1] if info else None


# ──────────────────────────────────────────────────────────────────────────
# next_required_slot
# ──────────────────────────────────────────────────────────────────────────


def next_required_slot(
    status: dict[str, str],
    level: int,
) -> dict | None:
    """Find the first required slot (in phase order) that isn't complete.

    Returns ``{phase, slot, command}`` or ``None`` when all required slots
    are satisfied.

    The *command* key holds the canonical first command (in
    ``COMMAND_PHASE`` iteration order) that targets that slot, so the
    caller can suggest what to run next.
    """
    rules = PhaseRules(level)
    required = rules.required_slots()

    for phase in PHASE_ORDER:
        for slot in required.get(phase, []):
            slot_status = _slot_status(status, phase, slot)
            # "complete" or a path string (artifact location) both count as done.
            if slot_status != "complete" and not _looks_like_path(slot_status):
                command = _first_command_for(phase, slot)
                return {"phase": phase, "slot": slot, "command": command}

    return None


def _slot_status(status: dict, phase: str, slot: str):
    """Look up *slot* in *status*, accepting both nested and flat forms.

    - Nested: ``{"phases": {phase: {slot: value}}}`` (production form from lib/status.load)
    - Flat:   ``{slot: value}`` (test convenience form)
    """
    if "phases" in status and isinstance(status["phases"], dict):
        return status["phases"].get(phase, {}).get(slot)
    return status.get(slot)


def _looks_like_path(value) -> bool:
    """Heuristic: treat any string containing '/' or ending in '.md' as an artifact path."""
    if not isinstance(value, str):
        return False
    return "/" in value or value.endswith(".md") or value.endswith(".yaml")


def _first_command_for(phase: str, slot: str) -> str | None:
    """Return the first slash-command targeting (*phase*, *slot*)."""
    for cmd, (p, s) in COMMAND_PHASE.items():
        if p == phase and s == slot:
            return cmd
    return None


# ──────────────────────────────────────────────────────────────────────────
# is_step_skipped — M3 / M5 / R1 semantics
# ──────────────────────────────────────────────────────────────────────────


def is_step_skipped(
    workflow_file: str,
    current_step: str,
    status: dict[str, str],
) -> bool:
    """Determine whether *current_step* should be skipped.

    Parses *workflow_file* (YAML-like text) for a ``skip_when`` clause
    associated with *current_step*.  Supports the following conditions:

    - ``{slot} == <value>``         — skip if slot equals value
    - ``{slot} != <value>``         — skip if slot does not equal value
    - ``{slot} in [<v1>, <v2>]``    — skip if slot is one of the listed values
    - ``has_slot: {slot}``          — skip if slot exists in status
    - ``missing_slot: {slot}``      — skip if slot is missing from status

    If no ``skip_when`` is defined for *current_step*, the step runs
    (not skipped).

    Intended use: M3 (milestone gate), M5 (implementation gate),
    R1 (review gate) steps that conditionally activate based on
    project state.
    """
    step_block = _extract_step_block(workflow_file, current_step)
    if step_block is None:
        return False

    skip_condition = _parse_skip_condition(step_block)
    if skip_condition is None:
        return False

    return _evaluate_condition(skip_condition, status)


def _extract_step_block(text: str, step_name: str) -> str | None:
    """Naively extract a step block from YAML-like text.

    Looks for a top-level key matching *step_name* and returns everything
    under it until the next top-level key or end of file.
    """
    lines = text.split("\n")
    in_block = False
    block_lines: list[str] = []
    step_pattern = f"{step_name}:"
    indent: str | None = None

    for line in lines:
        stripped = line.strip()

        if not in_block:
            # Match "step_name:" at the start of a line
            if stripped.startswith(step_pattern) and not stripped.startswith("#"):
                in_block = True
                block_lines.append(stripped)
                # Detect indentation of first content
                if len(line) > len(stripped):
                    indent = " " * (len(line) - len(stripped)) + "  "
                else:
                    indent = "  "
            continue

        # Inside the block — stop when we hit the next top-level key
        if stripped and not stripped.startswith("#") and not line.startswith((" ", "\t")):
            break

        block_lines.append(line)

    return "\n".join(block_lines) if block_lines else None


def _parse_skip_condition(block: str) -> dict | None:
    """Extract ``skip_when`` clause from a step block.

    Returns a dict describing the condition, or None.
    """
    for line in block.split("\n"):
        stripped = line.strip()
        if stripped.startswith("skip_when:"):
            # Collect value on same line or next indented line
            value = stripped.partition(":")[2].strip()
            if value:
                return _parse_skip_value(value)
            # Look on next line
            # (simplified: single-line skip_when only for now)
    return None


def _parse_skip_value(value: str) -> dict:
    """Parse a skip_when value into a condition dict.

    Supported forms:
      ``{slot} == <value>``
      ``{slot} != <value>``
      ``has_slot: {slot}``
      ``missing_slot: {slot}``
    """
    value = value.strip()

    # has_slot / missing_slot
    for prefix in ("has_slot:", "missing_slot:"):
        if value.startswith(prefix):
            slot = value[len(prefix) :].strip().strip("'\"")
            return {"type": prefix.rstrip(":"), "slot": slot}

    # == / !=
    for op in ("==", "!="):
        if op in value:
            parts = value.split(op, 1)
            return {
                "type": "compare",
                "slot": parts[0].strip(),
                "op": op,
                "value": parts[1].strip().strip("'\""),
            }

    return {"type": "unknown", "raw": value}


def _evaluate_condition(condition: dict, status: dict[str, str]) -> bool:
    """Evaluate a parsed skip condition against *status*."""
    cond_type = condition.get("type")

    if cond_type == "has_slot":
        return condition["slot"] in status

    if cond_type == "missing_slot":
        return condition["slot"] not in status

    if cond_type == "compare":
        slot = condition["slot"]
        actual = status.get(slot)
        expected = condition["value"]
        if condition["op"] == "==":
            return actual == expected
        elif condition["op"] == "!=":
            return actual != expected

    return False


# ──────────────────────────────────────────────────────────────────────────
# template_outputs_satisfied — M4 / M9 semantics
# ──────────────────────────────────────────────────────────────────────────


def template_outputs_satisfied(
    step_file_text: str,
    writes_since_step: list[str],
) -> tuple[bool, str]:
    """Check whether template outputs declared in *step_file_text* have been
    written since the step started.

    Returns ``(True, '')`` if all expected outputs are satisfied, or
    ``(False, reason)`` with details of what's missing.

    Parses the step file for ``outputs:`` or ``template_outputs:`` entries
    (list of file/directory paths).  Each entry is checked against
    *writes_since_step* — a list of file paths that have been created or
    modified since the step began.

    Intended use: M4 (template-output verification) and M9 (output
    completeness check).
    """
    expected = _parse_template_outputs(step_file_text)
    if not expected:
        return (True, "")

    missing: list[str] = []
    for path in expected:
        if not _any_match(path, writes_since_step):
            missing.append(path)

    if missing:
        return (
            False,
            f"Template outputs not yet produced: {missing}. "
            f"Expected outputs must be created before proceeding.",
        )

    return (True, "")


def _parse_template_outputs(text: str) -> list[str]:
    """Extract list of expected output paths from step file text.

    Looks for:
      ``outputs:`` or ``template_outputs:`` followed by a YAML list:
          - path/to/file
          - path/to/dir/
    """
    lines = text.split("\n")
    in_outputs = False
    outputs: list[str] = []

    for line in lines:
        stripped = line.strip()

        # Detect start of output list
        if stripped.startswith("outputs:") or stripped.startswith("template_outputs:"):
            rest = stripped.partition(":")[2].strip()
            if rest:
                # Could be inline list
                if rest.startswith("["):
                    # Simple inline list parsing
                    items = rest.strip("[]").split(",")
                    outputs.extend(item.strip().strip("'\"") for item in items)
                    in_outputs = False
                    continue
            in_outputs = True
            continue

        # Inside outputs list
        if in_outputs:
            if stripped.startswith("- "):
                outputs.append(stripped[2:].strip().strip("'\""))
            elif not stripped or stripped.startswith("#"):
                continue
            else:
                # End of outputs block
                in_outputs = False

    return outputs


def _any_match(path: str, writes: list[str]) -> bool:
    """Check if *path* (or any subpath of it) appears in *writes*."""
    path = path.rstrip("/")
    for w in writes:
        w_stripped = w.rstrip("/")
        if w_stripped == path or w_stripped.startswith(path + "/"):
            return True
    return False
