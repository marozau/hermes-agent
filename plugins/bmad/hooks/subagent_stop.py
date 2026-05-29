"""subagent_stop hook — auto-log child completion from delegate_task.

Per Story 3.2: fires when a child spawned via delegate_task completes.
Appends an entry to _subagent-log.yaml and updates workflow-status.yaml
for any artifact paths mentioned in the child's summary.

Must NEVER raise — wrapped by _catch_all in hooks/__init__.py if present.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Ordered: most specific first — mirrors hooks/post_tool_call.py PATH_RULES
PATH_RULES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"planning-artifacts/product-brief.*\.md$"), "analysis", "product-brief"),
    (re.compile(r"planning-artifacts/research/.*"), "analysis", "research"),
    (re.compile(r"planning-artifacts/brainstorm/.*"), "analysis", "brainstorm"),
    (re.compile(r"planning-artifacts/project-documentation/.*"), "analysis", "document-project"),
    (re.compile(r"planning-artifacts/quick-spec.*\.md$"), "analysis", "quick-spec"),
    (re.compile(r"planning-artifacts/prd[-_].*\.md$"), "planning", "prd"),
    (re.compile(r"planning-artifacts/ux-design/.*"), "planning", "ux-design"),
    (re.compile(r"planning-artifacts/(architecture|tech-spec)[-_].*\.md$"), "solutioning", "architecture"),
    (re.compile(r"planning-artifacts/(epics-stories|epics)[-_/].*"), "solutioning", "epics-stories"),
    (re.compile(r"planning-artifacts/solutioning-gate-check.*\.md$"), "solutioning", "solutioning-gate-check"),
    (re.compile(r"planning-artifacts/sprint-planning.*\.md$"), "implementation", "sprint-planning"),
    (re.compile(r"implementation-artifacts/stories/.*\.md$"), "implementation", "story"),
    (re.compile(r"implementation-artifacts/.+/dev-"), "implementation", "dev"),
    (re.compile(r"implementation-artifacts/.+/code-review"), "implementation", "code-review"),
    (re.compile(r"implementation-artifacts/.+/correct-course"), "implementation", "correct-course"),
]


def _find_matching_rule(project_dir, parent_skill: str, goal: str) -> tuple[str, str] | None:
    """Map (parent_skill, goal) → (phase, slot) using PATH_RULES.

    Returns the first PATH_RULE entry whose pattern is plausibly matched by
    the goal string, or None. Used by tests and the hook itself to attribute
    child results to a workflow slot.
    """
    if not parent_skill and not goal:
        return None
    search_text = f"{parent_skill or ''} {goal or ''}".lower()
    for pattern, phase, slot in PATH_RULES:
        # PATH_RULES regexes target file paths; lift the literal slot name
        # out of the pattern source and check if it occurs in the search text.
        slot_token = slot.lower()
        if slot_token in search_text or slot_token.replace("-", " ") in search_text:
            return (phase, slot)
    return None


def subagent_stop(ctx, **kwargs) -> None:
    """Post-completion hook for delegate_task children.

    The runtime hook bus (tools/delegate_tool.py:2272) passes individual
    kwargs ``parent_session_id``, ``child_role``, ``child_summary``,
    ``child_status``, ``duration_ms``. The body below works on the BMAD
    canonical ``child_result`` dict shape; translate at the boundary so
    we don't have to rewrite ``_process_child``.

    Must NEVER raise — exceptions are caught and logged.
    """
    child_result = {
        # parent_session_id is the only id-like field we have from the bus;
        # keep it under task_id for log keying. If a caller passes task_id
        # explicitly (e.g. tests), prefer that.
        "task_id": kwargs.get("task_id", kwargs.get("parent_session_id", "unknown")),
        "goal": kwargs.get("goal", ""),
        "status": kwargs.get("child_status", kwargs.get("status", "unknown")),
        "summary": kwargs.get("child_summary", kwargs.get("summary", "")) or "",
        "parent_skill_name": kwargs.get(
            "parent_skill_name", kwargs.get("child_role") or "unknown",
        ),
    }
    try:
        _process_child(ctx, child_result)
    except Exception:
        logger.exception("[bmad:subagent_stop] Hook failed — allowing through")


def _process_child(ctx, child_result: dict) -> None:
    """Core processing — extracted so top-level hook can be a pure try/except."""
    project_dir = _resolve_project_dir(ctx)
    if project_dir is None:
        return

    config_path = project_dir / "bmad" / "config.yaml"
    if not config_path.exists():
        return  # Not a BMAD project

    # Extract fields from child result
    task_id = child_result.get("task_id", "unknown")
    goal = child_result.get("goal", "unknown")
    status = child_result.get("status", "unknown")
    summary = child_result.get("summary", "") or ""
    parent_skill = child_result.get("parent_skill_name", "unknown")

    # Build log entry
    from plugins.bmad.lib._datetime import _now_iso

    entry: dict[str, Any] = {
        "timestamp": _now_iso(),
        "task_id": task_id,
        "goal": goal,
        "status": status,
        "summary": summary[:500],  # Truncate for readability
        "parent_skill": parent_skill,
    }

    # Append to subagent log
    from plugins.bmad.lib import subagent_log

    subagent_log.append(project_dir, entry)
    subagent_log.rotate(project_dir)

    # Check summary for artifact paths and update workflow status
    from plugins.bmad.lib import status as s

    for pattern, phase, slot in PATH_RULES:
        if pattern.search(summary):
            try:
                state = s.load(project_dir)
                current = state.get("phases", {}).get(phase, {}).get(slot)
                if current != "complete":
                    logger.info(
                        "[bmad:subagent_stop] child %s → %s/%s complete (from summary match)",
                        task_id, phase, slot,
                    )
                    s.mark_complete(project_dir, phase, slot, f"subagent:{task_id}")
            except Exception:
                logger.exception(
                    "[bmad:subagent_stop] Status update failed for %s/%s", phase, slot,
                )


def _resolve_project_dir(ctx) -> Path | None:
    """Extract project directory from plugin context."""
    if hasattr(ctx, "project_dir") and ctx.project_dir:
        return Path(ctx.project_dir)
    if hasattr(ctx, "working_directory") and ctx.working_directory:
        return Path(ctx.working_directory)
    return None
