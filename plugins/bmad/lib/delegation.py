"""Sub-agent delegation wrapper — fan_out and delegate_one.

Wraps Hermes's delegate_task tool for BMAD's parallel-work patterns.
FR-14.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def fan_out(
    ctx: Any,
    goals: list[str],
    parent_skill: str,
    max_workers: int | None = None,
    context: str | None = None,
) -> list[dict[str, Any]]:
    """Dispatch *goals* as parallel sub-agents using Hermes delegate_task.

    Args:
        ctx: Hermes plugin context (has dispatch_tool)
        goals: One goal string per sub-agent
        parent_skill: BMAD skill name for audit logging
        max_workers: Max concurrent children (None = Hermes default)
        context: Shared context string for all children (optional)

    Returns:
        List of result dicts, one per goal in input order
    """
    results: list[dict[str, Any]] = []
    for i, goal in enumerate(goals):
        kwargs: dict[str, Any] = {
            "goal": goal,
            "mode": "single",
            "parent_skill_name": parent_skill,
        }
        if context is not None:
            kwargs["context"] = context
        try:
            result = ctx.dispatch_tool("delegate_task", **kwargs)
            result.setdefault("index", i)
            result.setdefault("goal", goal)
            results.append(result)
        except Exception:
            logger.exception("[bmad:delegation] fan_out task %d failed: %s", i, goal)
            results.append({
                "index": i,
                "goal": goal,
                "task_id": None,
                "status": "failure",
                "summary": f"Delegation failed: {goal}",
                "parent_skill_name": parent_skill,
                "error": True,
            })
    return results


def delegate_one(
    ctx: Any,
    goal: str,
    parent_skill: str,
    toolset: list[str] | None = None,
    context: str | None = None,
) -> dict[str, Any]:
    """Dispatch a single sub-agent task.

    Args:
        ctx: Hermes plugin context (has dispatch_tool)
        goal: Task description for the sub-agent
        parent_skill: BMAD skill name for audit logging
        toolset: Optional list of tool names to restrict the child
        context: Optional shared context string

    Returns:
        Result dict with task_id, status, summary, parent_skill_name
    """
    kwargs: dict[str, Any] = {
        "goal": goal,
        "mode": "single",
        "parent_skill_name": parent_skill,
    }
    if toolset is not None:
        kwargs["toolset"] = toolset
    if context is not None:
        kwargs["context"] = context
    try:
        result = ctx.dispatch_tool("delegate_task", **kwargs)
        result.setdefault("goal", goal)
        return result
    except Exception:
        logger.exception("[bmad:delegation] delegate_one failed: %s", goal)
        return {
            "task_id": None,
            "goal": goal,
            "status": "failure",
            "summary": f"Delegation failed: {goal}",
            "parent_skill_name": parent_skill,
            "error": True,
        }
