"""Sub-agent delegation wrapper — fan_out and delegate_one.

Wraps Hermes's delegate_task tool for BMAD's parallel-work patterns.
FR-14.

Per-skill model overrides: callers may pass ``model``, ``provider``,
``base_url``, and ``api_key`` to route children to a different
provider:model pair than the profile's delegation default. Used by
``/bmad:code-review`` to route adversarial reviewers to a stronger
model (e.g. Claude Opus 4.7) while the rest of the bmad profile
continues to delegate to its default (e.g. DeepSeek-V4-Pro).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# Optional override-kwargs recognised by Hermes's delegate_task tool.
# Only the keys with a non-None value are passed through.
_OVERRIDE_KEYS = ("model", "provider", "base_url", "api_key", "api_mode")


def _add_overrides(kwargs: dict[str, Any], overrides: dict[str, Any]) -> None:
    """Inject non-None override values into the dispatch kwargs."""
    for key in _OVERRIDE_KEYS:
        value = overrides.get(key)
        if value is not None:
            kwargs[key] = value


def fan_out(
    ctx: Any,
    goals: list[str],
    parent_skill: str,
    max_workers: int | None = None,
    context: str | None = None,
    *,
    model: str | None = None,
    provider: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    api_mode: str | None = None,
) -> list[dict[str, Any]]:
    """Dispatch *goals* as parallel sub-agents using Hermes delegate_task.

    Args:
        ctx: Hermes plugin context (has dispatch_tool)
        goals: One goal string per sub-agent
        parent_skill: BMAD skill name for audit logging
        max_workers: Max concurrent children (None = Hermes default)
        context: Shared context string for all children (optional)
        model: Override the child model (default: profile's delegation.model)
        provider: Override the provider (default: profile's delegation.provider)
        base_url: Override the API base URL
        api_key: Override the API key
        api_mode: Override the API mode (e.g. ``"messages"`` vs ``"responses"``)

    Returns:
        List of result dicts, one per goal in input order
    """
    overrides = {
        "model": model, "provider": provider, "base_url": base_url,
        "api_key": api_key, "api_mode": api_mode,
    }
    results: list[dict[str, Any]] = []
    for i, goal in enumerate(goals):
        kwargs: dict[str, Any] = {
            "goal": goal,
            "mode": "single",
            "parent_skill_name": parent_skill,
        }
        if context is not None:
            kwargs["context"] = context
        _add_overrides(kwargs, overrides)
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
    *,
    model: str | None = None,
    provider: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    api_mode: str | None = None,
) -> dict[str, Any]:
    """Dispatch a single sub-agent task.

    Args:
        ctx: Hermes plugin context (has dispatch_tool)
        goal: Task description for the sub-agent
        parent_skill: BMAD skill name for audit logging
        toolset: Optional list of tool names to restrict the child
        context: Optional shared context string
        model, provider, base_url, api_key, api_mode: per-call overrides
            for the child's provider:model pair. See ``fan_out``.

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
    _add_overrides(kwargs, {
        "model": model, "provider": provider, "base_url": base_url,
        "api_key": api_key, "api_mode": api_mode,
    })
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
