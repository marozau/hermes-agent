"""Sub-agent delegation wrapper — fan_out and delegate_one.

Wraps Hermes's delegate_task tool for BMAD's parallel-work patterns.
FR-14.

Per-skill model overrides: callers may pass ``model``, ``provider``,
``base_url``, and ``api_key`` to route children to a different
provider:model pair than the profile's delegation default. Used by
``/bmad:code-review`` to route adversarial reviewers to a stronger
model (e.g. Claude Opus 4.7) while the rest of the bmad profile
continues to delegate to its default (e.g. DeepSeek-V4-Pro).

Fallback behaviour: when override kwargs are supplied AND the dispatch
fails, this module retries ONCE with the overrides stripped — letting
Hermes's profile-default delegation model (and its own built-in fallback
chain) take over. The fallback is logged at ``INFO`` so it's grep-able
but doesn't surface as a user-facing error. Rationale: skills are
designed to work on the default delegation model; a per-skill override
is a *preference* (e.g. "use Opus for review"), not a hard requirement.
Silent degradation to the default keeps the workflow flowing.

If the fallback dispatch also fails, the failure is surfaced as before.
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


def _has_any_override(overrides: dict[str, Any]) -> bool:
    """True iff at least one override key has a non-None value."""
    return any(overrides.get(key) is not None for key in _OVERRIDE_KEYS)


def _dispatch_with_fallback(
    ctx: Any,
    base_kwargs: dict[str, Any],
    overrides: dict[str, Any],
    parent_skill: str,
) -> dict[str, Any]:
    """Dispatch ``delegate_task`` with overrides; on failure, fall back once
    to the profile's default (no overrides).

    Returns the result dict either from the primary or fallback dispatch.
    Only raises if BOTH the primary AND the fallback fail (caller decides
    how to surface that).
    """
    # First attempt: with overrides if any are configured.
    primary_kwargs = dict(base_kwargs)
    _add_overrides(primary_kwargs, overrides)
    try:
        return ctx.dispatch_tool("delegate_task", **primary_kwargs)
    except Exception as primary_exc:
        if not _has_any_override(overrides):
            # No override was configured — nothing to fall back FROM.
            # Re-raise so the outer except handler in the caller fires.
            raise

        # Override was configured AND failed. Log + retry without overrides
        # so the profile's default delegation model (and Hermes's built-in
        # fallback chain on top of that) takes over.
        attempted_model = overrides.get("model") or "<provider-override>"
        logger.info(
            "[bmad:delegation] override dispatch failed for %s "
            "(attempted model=%s, exc=%s: %s); falling back to "
            "profile-default delegation model",
            parent_skill, attempted_model,
            primary_exc.__class__.__name__, primary_exc,
        )

        # Fallback: NO override keys; let Hermes resolve from profile.
        try:
            return ctx.dispatch_tool("delegate_task", **base_kwargs)
        except Exception as fallback_exc:
            logger.warning(
                "[bmad:delegation] fallback dispatch ALSO failed for %s "
                "after override-failure (fallback exc=%s: %s)",
                parent_skill,
                fallback_exc.__class__.__name__, fallback_exc,
            )
            raise


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
        List of result dicts, one per goal in input order.

    If an override is provided AND the dispatch fails, we silently retry
    once with the overrides stripped (using the profile's default
    delegation model). See module docstring for rationale.
    """
    overrides = {
        "model": model, "provider": provider, "base_url": base_url,
        "api_key": api_key, "api_mode": api_mode,
    }
    results: list[dict[str, Any]] = []
    for i, goal in enumerate(goals):
        base_kwargs: dict[str, Any] = {
            "goal": goal,
            "mode": "single",
            "parent_skill_name": parent_skill,
        }
        if context is not None:
            base_kwargs["context"] = context
        try:
            result = _dispatch_with_fallback(ctx, base_kwargs, overrides, parent_skill)
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
        Result dict with task_id, status, summary, parent_skill_name.

    On override-dispatch failure, retries once with the overrides
    stripped (profile default). See module docstring.
    """
    base_kwargs: dict[str, Any] = {
        "goal": goal,
        "mode": "single",
        "parent_skill_name": parent_skill,
    }
    if toolset is not None:
        base_kwargs["toolset"] = toolset
    if context is not None:
        base_kwargs["context"] = context
    overrides = {
        "model": model, "provider": provider, "base_url": base_url,
        "api_key": api_key, "api_mode": api_mode,
    }
    try:
        result = _dispatch_with_fallback(ctx, base_kwargs, overrides, parent_skill)
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
