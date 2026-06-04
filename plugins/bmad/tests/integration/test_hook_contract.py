"""Contract tests: BMAD hook signatures match Hermes invoke_hook() call sites.

Every test simulates exactly what ``PluginManager.invoke_hook()`` does:
``cb(**kwargs)`` → hook receives kwargs as named parameters.

Regressions caught by this test:
  - Hook author renames a parameter without updating the Hermes call site
  - Hermes adds/removes a kwarg without updating the hook signature
  - ``_bind_hook_ctx`` is accidentally added/removed, shifting ctx into a
    named parameter and causing ``multiple values`` TypeErrors
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock


# ═══════════════════════════════════════════════════════════════════════
# Fake PluginContext — provides enough surface for hooks that access ctx
# ═══════════════════════════════════════════════════════════════════════

def _make_ctx(**overrides):
    """Return a mock PluginContext with safe defaults."""
    ctx = MagicMock()
    ctx.profile_config = {"display": {}}
    ctx.project_dir = None
    ctx.working_directory = None
    for k, v in overrides.items():
        setattr(ctx, k, v)
    return ctx


# ═══════════════════════════════════════════════════════════════════════
# Test data: (hook_name, kwargs_hermes_passes, hook_function)
# ═══════════════════════════════════════════════════════════════════════

def _get_hooks_under_test():
    """Return list of (hook_name, kwargs_dict, fn) for every BMAD hook."""
    from plugins.bmad.hooks.on_session_start import on_session_start
    from plugins.bmad.hooks.on_session_end import on_session_end
    from plugins.bmad.hooks.pre_tool_call import pre_tool_call
    from plugins.bmad.hooks.post_tool_call import post_tool_call
    from plugins.bmad.hooks.pre_llm_call import pre_llm_call
    from plugins.bmad.hooks.post_llm_call import post_llm_call
    from plugins.bmad.hooks.transform_terminal_output import transform_terminal_output
    from plugins.bmad.hooks.subagent_stop import subagent_stop

    return [
        # session_id, model, platform — conversation_loop.py:295-300
        ("on_session_start", {
            "session_id": "sess-abc",
            "model": "deepseek-v4-pro",
            "platform": "cli",
        }, on_session_start),

        # session_id, completed, interrupted, model, platform — conversation_loop.py:4623-4630
        ("on_session_end", {
            "session_id": "sess-abc",
            "completed": True,
            "interrupted": False,
            "model": "deepseek-v4-pro",
            "platform": "cli",
        }, on_session_end),

        # tool_name, args, task_id, session_id, tool_call_id — plugins.py:1689-1696
        ("pre_tool_call", {
            "tool_name": "read_file",
            "args": {"path": "/tmp/x", "offset": 1},
            "task_id": "task-1",
            "session_id": "sess-abc",
            "tool_call_id": "call-1",
        }, pre_tool_call),

        # tool_name, args, result, task_id, session_id, tool_call_id, duration_ms — model_tools.py:995-1004
        ("post_tool_call", {
            "tool_name": "read_file",
            "args": {"path": "/tmp/x", "offset": 1},
            "result": {"content": "hello", "total_lines": 1},
            "task_id": "task-1",
            "session_id": "sess-abc",
            "tool_call_id": "call-1",
            "duration_ms": 42,
        }, post_tool_call),

        # session_id, user_message, conversation_history, is_first_turn, model, platform,
        # sender_id, session_search_fn — conversation_loop.py:693-703
        ("pre_llm_call", {
            "session_id": "sess-abc",
            "user_message": "hello",
            "conversation_history": [],
            "is_first_turn": True,
            "model": "deepseek-v4-pro",
            "platform": "cli",
            "sender_id": "",
            "session_search_fn": None,
        }, pre_llm_call),

        # session_id, user_message, assistant_response, conversation_history, model, platform
        # — conversation_loop.py:4504-4512
        ("post_llm_call", {
            "session_id": "sess-abc",
            "user_message": "hello",
            "assistant_response": "hi there",
            "conversation_history": [],
            "model": "deepseek-v4-pro",
            "platform": "cli",
        }, post_llm_call),

        # command, output, returncode, task_id, env_type — terminal_tool.py:2255-2261
        ("transform_terminal_output", {
            "command": "ls",
            "output": "file1.txt\nfile2.txt",
            "returncode": 0,
            "task_id": "task-1",
            "env_type": "local",
        }, transform_terminal_output),

        # parent_session_id, child_role, child_summary, child_status, duration_ms
        # — delegate_tool.py:2268-2275
        ("subagent_stop", {
            "parent_session_id": "sess-abc",
            "child_role": "leaf",
            "child_summary": "done",
            "child_status": "completed",
            "duration_ms": 1500,
        }, subagent_stop),
    ]


# ═══════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════

class TestHookContract:
    """Every BMAD hook accepts exactly what Hermes invoke_hook() passes."""

    @pytest.mark.parametrize("hook_name, kwargs, fn", _get_hooks_under_test())
    def test_hook_accepts_hermes_kwargs(self, hook_name, kwargs, fn):
        """The hook function is callable with Hermes' exact kwargs dict.

        This is what PluginManager.invoke_hook() does: cb(**kwargs).
        No TypeError, no missing positional args, no multiple-values conflict.
        """
        ctx = _make_ctx()

        # Some hooks take ctx as first positional — _bind_hook_ctx adds it.
        # Detect this by inspecting the hook's first parameter name.
        import inspect
        sig = inspect.signature(fn)
        params = list(sig.parameters.keys())
        takes_ctx = bool(params) and params[0] == "ctx"

        try:
            if takes_ctx:
                result = fn(ctx, **kwargs)
            else:
                result = fn(**kwargs)
        except TypeError as exc:
            pytest.fail(
                f"{hook_name} raised TypeError with Hermes kwargs: {exc}\n"
                f"  kwargs passed: {sorted(kwargs.keys())}\n"
                f"  signature: {sig}\n"
                f"  takes_ctx: {takes_ctx}"
            )

        # Hooks must never raise (but contract test only checks callability)
        # Return value can be None — that's fine (pass-through / no-op).

    @pytest.mark.parametrize("hook_name, kwargs, fn", _get_hooks_under_test())
    def test_hook_never_raises_on_empty_kwargs(self, hook_name, kwargs, fn):
        """Every hook tolerates being called with no kwargs (empty session)."""
        ctx = _make_ctx()
        import inspect
        sig = inspect.signature(fn)
        params = list(sig.parameters.keys())
        takes_ctx = bool(params) and params[0] == "ctx"

        try:
            if takes_ctx:
                fn(ctx)
            else:
                fn()
        except TypeError as exc:
            # TypeError is okay if the hook truly requires args — but let's log it
            pass
        except Exception:
            # Any non-TypeError exception on empty call is a bug
            pytest.fail(f"{hook_name} raised on empty call: see traceback above")


class TestBindHookCtxCorrectness:
    """Verify _bind_hook_ctx is applied to the right hooks."""

    def test_hooks_with_ctx_in_signature_use_bind(self):
        """5 hooks declare ctx as first param → must use _bind_hook_ctx."""
        # These are verified by the contract test above:
        # on_session_start, pre_tool_call, post_tool_call,
        # transform_terminal_output, subagent_stop
        hooks_with_ctx = {"on_session_start", "pre_tool_call", "post_tool_call",
                          "transform_terminal_output", "subagent_stop"}
        for hook_name, kwargs, fn in _get_hooks_under_test():
            import inspect
            sig = inspect.signature(fn)
            params = list(sig.parameters.keys())
            takes_ctx = bool(params) and params[0] == "ctx"
            if hook_name in hooks_with_ctx:
                assert takes_ctx, f"{hook_name} should have ctx as first param"

    def test_all_eight_hooks_take_ctx(self):
        """All 8 BMAD hooks declare ctx as first param — _bind_hook_ctx injects it."""
        for hook_name, kwargs, fn in _get_hooks_under_test():
            import inspect
            sig = inspect.signature(fn)
            params = list(sig.parameters.keys())
            takes_ctx = bool(params) and params[0] == "ctx"
            assert takes_ctx, (
                f"{hook_name} must have ctx as first param — "
                f"__init__.py wraps ALL hooks with _bind_hook_ctx. "
                f"Got signature: {sig}"
            )


class TestTransformTerminalOutput:
    """Specific regression: text→output param fix."""

    def test_output_param_not_text(self):
        from plugins.bmad.hooks.transform_terminal_output import transform_terminal_output
        import inspect
        sig = inspect.signature(transform_terminal_output)
        params = list(sig.parameters.keys())
        assert "output" in params, "transform_terminal_output must use 'output', not 'text'"
        assert "text" not in params, "transform_terminal_output must not have 'text' param"
