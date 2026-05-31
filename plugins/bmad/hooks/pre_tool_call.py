"""pre_tool_call hook — BMAD phase gate + workspace write-boundary enforcement.

Per architecture A-5 (FR-9): intercepts Write/Edit tool calls and
blocks them if they target a file that belongs to a phase whose
preceding phase isn't complete.

Enforces:
- M1: Read complete file mandate (detects Read(offset, limit) on known workflow files)
- Phase gates: prevents writing to a later phase's artifacts before the
  preceding phase's required slots are complete
- FR-21 (Story 6.4): workspace write boundary — in workspace mode, blocks
  writes outside planning-artifacts/ and worktree/<name>/

Per architecture §4.1: NEVER raises. Returns {"action": "block", "reason": str}
on denial, None to allow.

Wrapped by _catch_all in __init__.py — this is a second safety layer.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Pattern table: (regex, phase, slot)
PATH_RULES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"planning-artifacts/solutioning-gate-check.*\.md$"), "solutioning", "solutioning-gate-check"),
    (re.compile(r"planning-artifacts/(epics-stories|epics)[-_/].*"), "solutioning", "epics-stories"),
    (re.compile(r"planning-artifacts/(architecture|tech-spec)[-_].*\.md$"), "solutioning", "architecture"),
    (re.compile(r"planning-artifacts/prd[-_].*\.md$"), "planning", "prd"),
    (re.compile(r"planning-artifacts/product-brief.*\.md$"), "analysis", "product-brief"),
    (re.compile(r"planning-artifacts/research/.*"), "analysis", "research"),
    (re.compile(r"implementation-artifacts/stories/.*\.md$"), "implementation", "story"),
]

# Paths whose Read calls must always be complete (M1/M7 mandate).
WORKFLOW_FILE_PATTERNS: list[re.Pattern] = [
    re.compile(r"workflow\.xml$"),
    re.compile(r"instructions\.md$"),
    re.compile(r"/skills/bmad/.*/SKILL\.md$"),
    re.compile(r"/bmad-method/.*/(workflow|instructions)\.(xml|md)$"),
    re.compile(r"/bmad-method/.*/SKILL\.md$"),
]


def pre_tool_call(ctx, tool_name: str, args: dict, result: dict | None = None, **kwargs) -> dict | None:
    """Phase gate + M1/M7 + workspace write-boundary enforcement.

    Returns ``{"action": "block", "reason": str}`` to deny, or ``None`` to allow.
    """
    # M1/M7: block Read(offset|limit) on workflow files — ALWAYS, even under YOLO.
    if tool_name in ("Read", "read_file"):
        if "offset" in args or "limit" in args:
            file_path = args.get("file_path", args.get("path", ""))
            if file_path and _is_workflow_file(file_path):
                reason = (
                    f"BMAD M1/M7 violation: workflow files must be read complete "
                    f"(no offset/limit). Path: {file_path}"
                )
                logger.warning("[bmad:pre_tool_call] %s", reason)
                return {"action": "block", "reason": reason}
        return None

    # Only gate Write and Edit for phase enforcement
    if tool_name not in ("Write", "Edit", "write_file", "edit_file"):
        return None

    project_dir = _resolve_project_dir(ctx)
    if project_dir is None:
        return None

    config_path = project_dir / "bmad" / "config.yaml"
    if not config_path.exists():
        return None  # Not a BMAD project

    file_path = args.get("file_path", args.get("path", ""))
    if not file_path:
        return None

    # ── Story 6.4: Workspace write-boundary check (WI-2) ────────────
    try:
        import yaml
        raw_config = yaml.safe_load(config_path.read_text()) or {}
        if raw_config.get("workspace_mode"):
            from plugins.bmad.lib.workspace import is_write_allowed
            from plugins.bmad.lib.config import load_workspace_config

            ws_config = load_workspace_config(project_dir)
            if not is_write_allowed(file_path, project_dir, ws_config):
                reason = (
                    f"workspace_mode: write outside planning-artifacts/ and worktree/*: "
                    f"{file_path}"
                )
                logger.warning("[bmad:pre_tool_call] %s", reason)
                return {"action": "block", "reason": reason}
    except Exception:
        logger.exception("[bmad:pre_tool_call] Workspace boundary check failed — allowing through")

    # ── Phase gate check (existing logic) ────────────────────────────
    rel = _relative_to_project(file_path, project_dir)
    if rel is None:
        return None  # Writing outside the project — not gated

    # Check if this file matches a known BMAD artifact
    phase_slot = _match_path(rel)
    if phase_slot is None:
        return None  # Not a gated file

    command_phase, _ = phase_slot

    from plugins.bmad.lib import status as s
    from plugins.bmad.lib import phases

    try:
        state = s.load(project_dir)
        level = state.get("level", 1)
        yolo = ctx.profile_config.get("bmad_yolo", False)

        allowed, reason = phases.can_run(
            _slot_to_command(phase_slot),
            state,
            level,
            yolo=yolo,
        )
        if not allowed:
            logger.warning("[bmad:pre_tool_call] Blocked %s on %s: %s", tool_name, rel, reason)
            return {"action": "block", "reason": reason}

        return None
    except Exception:
        logger.exception("[bmad:pre_tool_call] Gate check failed — allowing through")
        return None  # Hooks never raise on internal errors


def _relative_to_project(file_path: str, project_dir: Path) -> str | None:
    """Compute project-relative path, or None if outside project."""
    path = Path(file_path).resolve()
    try:
        return str(path.relative_to(project_dir.resolve()))
    except ValueError:
        return None


def _match_path(rel_path: str) -> tuple[str, str] | None:
    """Return (phase, slot) for a project-relative path, or None."""
    for pattern, phase, slot in PATH_RULES:
        if pattern.search(rel_path):
            return (phase, slot)
    return None


def _slot_to_command(phase_slot: tuple[str, str]) -> str:
    """Derive a slash command name from (phase, slot)."""
    from plugins.bmad.lib.phases import COMMAND_PHASE

    for cmd, (p, s) in COMMAND_PHASE.items():
        if p == phase_slot[0] and s == phase_slot[1]:
            return cmd
    return phase_slot[1]


def _resolve_project_dir(ctx) -> Path | None:
    if hasattr(ctx, "project_dir") and ctx.project_dir:
        return Path(ctx.project_dir)
    if hasattr(ctx, "working_directory") and ctx.working_directory:
        return Path(ctx.working_directory)
    return None


def _is_workflow_file(file_path: str) -> bool:
    """True if file_path matches a BMAD workflow/instructions/SKILL file."""
    return any(p.search(file_path) for p in WORKFLOW_FILE_PATTERNS)
