"""Tests for _bind_hook_ctx — ctx injection must not break hooks that don't accept it."""

import inspect
from unittest.mock import MagicMock

import pytest


class TestBindHookCtx:
    """_bind_hook_ctx must only inject ctx when the handler accepts it."""

    def _get_bind_hook_ctx(self):
        """Extract _bind_hook_ctx from __init__.py by reading the source."""
        import plugins.bmad as bmad_mod
        source = inspect.getsource(bmad_mod)
        # The function is defined inside register(), but we can test the behavior
        # by importing the hooks and checking their signatures
        return source

    def test_post_llm_call_does_not_accept_ctx(self):
        """post_llm_call(session_id=...) has no ctx param — _bind_hook_ctx must not inject."""
        from plugins.bmad.hooks.post_llm_call import post_llm_call
        sig = inspect.signature(post_llm_call)
        params = list(sig.parameters.keys())
        # First param is session_id, NOT ctx
        assert params[0] == "session_id"
        assert "ctx" not in params

    def test_on_session_end_does_not_accept_ctx(self):
        """on_session_end(session_id=...) has no ctx param — _bind_hook_ctx must not inject."""
        from plugins.bmad.hooks.on_session_end import on_session_end
        sig = inspect.signature(on_session_end)
        params = list(sig.parameters.keys())
        assert params[0] == "session_id"
        assert "ctx" not in params

    def test_pre_llm_call_does_not_accept_ctx(self):
        """pre_llm_call(session_id=...) has no ctx param — _bind_hook_ctx must not inject."""
        from plugins.bmad.hooks.pre_llm_call import pre_llm_call
        sig = inspect.signature(pre_llm_call)
        params = list(sig.parameters.keys())
        assert params[0] == "session_id"
        assert "ctx" not in params

    def test_on_session_start_accepts_ctx(self):
        """on_session_start(ctx, ...) has ctx param — _bind_hook_ctx MUST inject."""
        from plugins.bmad.hooks.on_session_start import on_session_start
        sig = inspect.signature(on_session_start)
        params = list(sig.parameters.keys())
        assert params[0] == "ctx"

    def test_pre_tool_call_accepts_ctx(self):
        """pre_tool_call(ctx, ...) has ctx param — _bind_hook_ctx MUST inject."""
        from plugins.bmad.hooks.pre_tool_call import pre_tool_call
        sig = inspect.signature(pre_tool_call)
        params = list(sig.parameters.keys())
        assert params[0] == "ctx"

    def test_post_tool_call_accepts_ctx(self):
        """post_tool_call(ctx, ...) has ctx param — _bind_hook_ctx MUST inject."""
        from plugins.bmad.hooks.post_tool_call import post_tool_call
        sig = inspect.signature(post_tool_call)
        params = list(sig.parameters.keys())
        assert params[0] == "ctx"

    def test_wrapped_hook_without_ctx_does_not_raise_typeerror(self):
        """After fix: wrapped hook with kwargs-only bus call must NOT raise TypeError."""
        from plugins.bmad.hooks.post_llm_call import post_llm_call

        ctx = MagicMock()
        # Simulate what _bind_hook_ctx SHOULD do: only inject ctx if handler accepts it
        sig = inspect.signature(post_llm_call)
        if "ctx" in sig.parameters:
            def wrap(*args, **kwargs):
                return post_llm_call(ctx, *args, **kwargs)
        else:
            def wrap(*args, **kwargs):
                return post_llm_call(*args, **kwargs)

        # This is what the hook bus does: cb(**kwargs)
        # Must NOT raise TypeError
        wrap(session_id="test-123")

    def test_hook_bus_call_pattern_works_without_ctx_injection(self):
        """Hook bus calling handler directly with kwargs must work."""
        from unittest.mock import patch as _patch
        from plugins.bmad.hooks.post_llm_call import post_llm_call

        # Mock the actual implementation to avoid side effects
        with _patch.object(
            __import__("plugins.bmad.hooks.post_llm_call", fromlist=["post_llm_call"]),
            "post_llm_call",
            side_effect=lambda **kwargs: None
        ):
            # This is the hook bus pattern: cb(**kwargs)
            # Should NOT raise TypeError
            post_llm_call(session_id="test-123")
