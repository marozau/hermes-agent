"""Unit tests for lib/delegation model-override plumbing.

Verifies fan_out() and delegate_one() pass model / provider / base_url / api_key
through to ctx.dispatch_tool("delegate_task", ...) only when non-None.
"""

from __future__ import annotations

from plugins.bmad.lib import delegation


class _CapturingCtx:
    """Mock ctx that records every dispatch_tool call."""
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def dispatch_tool(self, name, **kwargs):
        self.calls.append((name, kwargs))
        return {"task_id": f"t-{len(self.calls)}", "status": "success", "summary": "ok"}


# ── fan_out ──────────────────────────────────────────────────────────────────


class TestFanOutNoOverride:
    """With no model kwarg, dispatch_tool kwargs must NOT contain model/provider."""

    def test_default_omits_override_keys(self):
        ctx = _CapturingCtx()
        delegation.fan_out(ctx, ["g1", "g2"], parent_skill="test")
        assert len(ctx.calls) == 2
        for _, kw in ctx.calls:
            assert "model" not in kw
            assert "provider" not in kw
            assert "base_url" not in kw
            assert "api_key" not in kw
            assert "api_mode" not in kw


class TestFanOutWithModel:
    """When model=... is passed, it appears in every child kwargs."""

    def test_model_propagated_to_every_child(self):
        ctx = _CapturingCtx()
        delegation.fan_out(
            ctx, ["g1", "g2", "g3"],
            parent_skill="test",
            model="claude-opus-4-7",
        )
        assert len(ctx.calls) == 3
        for _, kw in ctx.calls:
            assert kw["model"] == "claude-opus-4-7"

    def test_provider_and_base_url_propagated(self):
        ctx = _CapturingCtx()
        delegation.fan_out(
            ctx, ["g1"],
            parent_skill="test",
            model="claude-opus-4-7",
            provider="anthropic",
            base_url="https://api.anthropic.com",
            api_key="sk-ant-test",
        )
        _, kw = ctx.calls[0]
        assert kw["model"] == "claude-opus-4-7"
        assert kw["provider"] == "anthropic"
        assert kw["base_url"] == "https://api.anthropic.com"
        assert kw["api_key"] == "sk-ant-test"

    def test_partial_override_only_passes_provided_keys(self):
        """If only model is set, provider/base_url are NOT in kwargs."""
        ctx = _CapturingCtx()
        delegation.fan_out(ctx, ["g1"], parent_skill="test", model="claude-opus-4-7")
        _, kw = ctx.calls[0]
        assert kw["model"] == "claude-opus-4-7"
        assert "provider" not in kw
        assert "base_url" not in kw


# ── delegate_one ─────────────────────────────────────────────────────────────


class TestDelegateOneOverrides:
    def test_no_override_passes_clean_kwargs(self):
        ctx = _CapturingCtx()
        delegation.delegate_one(ctx, "single goal", parent_skill="test")
        _, kw = ctx.calls[0]
        assert "model" not in kw
        assert "provider" not in kw

    def test_model_override_propagates(self):
        ctx = _CapturingCtx()
        delegation.delegate_one(
            ctx, "single goal",
            parent_skill="test",
            model="claude-opus-4-7",
            api_mode="messages",
        )
        _, kw = ctx.calls[0]
        assert kw["model"] == "claude-opus-4-7"
        assert kw["api_mode"] == "messages"


# ── Failure path still records the goal ──────────────────────────────────────


class TestFailureWithOverrides:
    def test_dispatch_failure_returns_error_result(self):
        class FailingCtx:
            def dispatch_tool(self, name, **kwargs):
                raise RuntimeError("provider unreachable")

        results = delegation.fan_out(
            FailingCtx(), ["g1"], parent_skill="test", model="claude-opus-4-7",
        )
        assert len(results) == 1
        assert results[0]["error"] is True
        assert results[0]["status"] == "failure"
