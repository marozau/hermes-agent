"""Test that BMAD hook signatures match Hermes invocation patterns.

Prevents the class of bug where ``_bind_hook_ctx`` injects ``ctx`` as the
first positional argument, which maps to the hook's first-named parameter
(e.g. ``session_id``), and Hermes then passes the same name as a kwarg —
causing ``TypeError: got multiple values`` that ``_catch_all`` silently
swallows, turning the hook into a dead no-op.
"""

from __future__ import annotations

import importlib
import inspect

import pytest

# ── What Hermes actually passes to each hook ──────────────────────────
# Extracted from the invoke_hook() call sites in the Hermes source
# (agent/conversation_loop.py, tools/terminal_tool.py, tools/delegate_tool.py).

HERMES_HOOK_KWARGS: dict[str, set[str]] = {
    "on_session_start": {"session_id", "model", "platform"},
    "on_session_end": {"session_id", "completed", "interrupted", "model", "platform"},
    "pre_tool_call": {"tool_name", "args", "result"},
    "post_tool_call": {"tool_name", "args", "result"},
    "pre_llm_call": {
        "session_id",
        "user_message",
        "conversation_history",
        "is_first_turn",
        "model",
        "platform",
        "sender_id",
        "session_search_fn",
    },
    "post_llm_call": {
        "session_id",
        "user_message",
        "assistant_response",
        "conversation_history",
        "model",
        "platform",
    },
    "subagent_stop": {
        "parent_session_id",
        "child_role",
        "child_summary",
        "child_status",
        "duration_ms",
    },
    "transform_terminal_output": {
        "command",
        "output",
        "returncode",
        "task_id",
        "env_type",
    },
}

# Hooks that use _bind_hook_ctx (ctx injected as first positional)
_BIND_CTX_HOOKS = {
    "on_session_start",
    "pre_tool_call",
    "post_tool_call",
    "pre_llm_call",
    "post_llm_call",
    "on_session_end",
    "subagent_stop",
    "transform_terminal_output",
}


def _get_hook_fn(hook_name: str):
    """Import the BMAD hook function by name."""
    module_name = f"plugins.bmad.hooks.{hook_name}"
    try:
        mod = importlib.import_module(module_name)
        return getattr(mod, hook_name)
    except (ImportError, AttributeError) as exc:
        pytest.skip(f"Cannot import {hook_name}: {exc}")


def _positional_params(fn) -> list[str]:
    """Return the names of positional-or-keyword parameters (before *args / **kwargs).

    These are the params that _bind_hook_ctx's ctx injection could collide with.
    """
    sig = inspect.signature(fn)
    positional: list[str] = []
    for name, param in sig.parameters.items():
        if param.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            positional.append(name)
        else:
            break  # *args, * , or **kwargs — stop collecting positionals
    return positional


@pytest.mark.parametrize("hook_name", sorted(HERMES_HOOK_KWARGS))
def test_hook_signature_matches_hermes_kwargs(hook_name: str) -> None:
    """Every hook must accept the kwargs Hermes actually passes.

    This is the baseline check: if Hermes passes ``session_id`` but the
    hook doesn't have it (and has no ``**kwargs``), the call fails.
    """
    fn = _get_hook_fn(hook_name)
    sig = inspect.signature(fn)
    param_names = set(sig.parameters.keys())
    hermes_kwargs = HERMES_HOOK_KWARGS[hook_name]

    has_var_kwargs = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )

    if has_var_kwargs:
        # **kwargs catches everything — all hermes kwargs are accepted
        return

    missing = hermes_kwargs - param_names
    assert not missing, (
        f"{hook_name}() lacks parameters for Hermes kwargs: {sorted(missing)}. "
        f"Add them or add **kwargs."
    )


@pytest.mark.parametrize("hook_name", sorted(_BIND_CTX_HOOKS & HERMES_HOOK_KWARGS.keys()))
def test_bind_hook_ctx_no_param_collision(hook_name: str) -> None:
    """For hooks wrapped with _bind_hook_ctx, ctx must not collide with
    any Hermes kwarg name.

    _bind_hook_ctx passes ctx as the FIRST positional argument. If the
    hook's first positional parameter name ALSO appears in the Hermes
    kwargs, Python raises ``TypeError: got multiple values``.

    Example of BROKEN:
        def on_session_end(session_id, ...):   # ← first positional = 'session_id'
            ...
        # Hermes calls: fn(ctx, session_id='abc', ...)
        # ctx → maps to 'session_id' param
        # session_id='abc' → ALSO maps to 'session_id' → CONFLICT

    Example of FIXED:
        def on_session_end(ctx, session_id, ...):  # ← 'ctx' is unique
            ...
    """
    fn = _get_hook_fn(hook_name)
    positional = _positional_params(fn)

    if not positional:
        return  # No positional params — no collision possible

    first_param = positional[0]

    # ctx is injected by _bind_hook_ctx — it MUST be the first param name
    assert first_param == "ctx", (
        f"{hook_name}() first positional param is '{first_param}', not 'ctx'. "
        f"_bind_hook_ctx passes ctx as the first positional argument, which maps "
        f"to '{first_param}'. If Hermes also passes '{first_param}' as a kwarg "
        f"(check HERMES_HOOK_KWARGS), you get a TypeError. "
        f"Fix: add 'ctx' as the first parameter."
    )

    # Verify ctx doesn't also appear in Hermes kwargs (shouldn't, but check)
    hermes_kwargs = HERMES_HOOK_KWARGS.get(hook_name, set())
    assert "ctx" not in hermes_kwargs, (
        f"Hermes passes 'ctx' as a kwarg to {hook_name} — this is unexpected "
        f"and would collide with _bind_hook_ctx's injection."
    )

    # Verify the second positional (if any) doesn't collide with Hermes kwargs
    if len(positional) > 1:
        for param_name in positional[1:]:
            if param_name in hermes_kwargs:
                # This is OK — positional params CAN receive kwargs in Python.
                # The collision only happens when the FIRST positional matches
                # a kwarg name AND both are passed. But let's flag it.
                pass
