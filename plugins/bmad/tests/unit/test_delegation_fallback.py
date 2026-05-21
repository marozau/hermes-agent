"""Tests for lib/delegation fallback-to-default behavior.

When override kwargs (model/provider/...) cause dispatch_tool to fail,
the wrapper retries once with the overrides stripped, letting the
profile's default delegation model and Hermes's built-in fallback chain
take over. Logged at INFO; no user-facing error unless the fallback also
fails.
"""

from __future__ import annotations

import logging

import pytest

from plugins.bmad.lib import delegation


# ── Helpers ──────────────────────────────────────────────────────────────────


class _ProgrammableCtx:
    """ctx whose dispatch_tool runs through a queue of (raises?, payload) entries.

    Each call pops one entry. If raises is True, raise the payload as an
    exception. Otherwise, return the payload as the result dict.
    Records every call's kwargs to self.calls so tests can assert on
    them.
    """
    def __init__(self, entries):
        self.entries = list(entries)
        self.calls: list[tuple[str, dict]] = []

    def dispatch_tool(self, name, **kwargs):
        self.calls.append((name, dict(kwargs)))
        if not self.entries:
            raise RuntimeError("no more programmed entries")
        raises, payload = self.entries.pop(0)
        if raises:
            raise payload
        return payload


def _ok_result(summary: str = "ok") -> dict:
    return {"task_id": "t-1", "status": "success", "summary": summary}


# ── No-override path: no fallback attempt ───────────────────────────────────


class TestNoOverrideNoFallback:
    def test_success_passes_through(self):
        ctx = _ProgrammableCtx([(False, _ok_result())])
        out = delegation.fan_out(ctx, ["g1"], parent_skill="test")
        assert len(ctx.calls) == 1
        assert out[0]["status"] == "success"

    def test_failure_propagates_as_error_result(self):
        """No override → no retry → failure surfaced as before."""
        ctx = _ProgrammableCtx([(True, RuntimeError("provider down"))])
        out = delegation.fan_out(ctx, ["g1"], parent_skill="test")
        assert len(ctx.calls) == 1  # only the primary attempt
        assert out[0]["error"] is True
        assert out[0]["status"] == "failure"


# ── Override-with-fallback path ──────────────────────────────────────────────


class TestOverrideFallback:
    def test_override_succeeds_no_fallback_attempted(self):
        ctx = _ProgrammableCtx([(False, _ok_result("opus said hi"))])
        out = delegation.fan_out(
            ctx, ["g1"], parent_skill="bmad-code-review",
            model="claude-opus-4-7",
        )
        assert len(ctx.calls) == 1
        # Primary call carried the override
        assert ctx.calls[0][1]["model"] == "claude-opus-4-7"
        assert out[0]["summary"] == "opus said hi"

    def test_override_fails_fallback_succeeds(self, caplog):
        """Override dispatch fails → second dispatch without overrides → success.

        Result is the fallback's payload; user sees success.
        """
        ctx = _ProgrammableCtx([
            (True, ValueError("model `claude-opus-4-7` not found")),  # primary
            (False, _ok_result("default model said hi")),              # fallback
        ])
        with caplog.at_level(logging.INFO, logger="plugins.bmad.lib.delegation"):
            out = delegation.fan_out(
                ctx, ["g1"], parent_skill="bmad-code-review",
                model="claude-opus-4-7",
            )
        # Both attempts fired
        assert len(ctx.calls) == 2
        # Primary had override
        assert ctx.calls[0][1].get("model") == "claude-opus-4-7"
        # Fallback did NOT
        assert "model" not in ctx.calls[1][1]
        # User-visible result is the fallback's payload (success)
        # — success results don't carry the 'error' key at all.
        assert out[0].get("error") is not True
        assert out[0]["summary"] == "default model said hi"
        # INFO log mentions the fallback
        fallback_logs = [r for r in caplog.records if "falling back" in r.message]
        assert fallback_logs, f"expected an INFO 'falling back' log; got {[r.message for r in caplog.records]}"
        assert "bmad-code-review" in fallback_logs[0].message
        assert "claude-opus-4-7" in fallback_logs[0].message

    def test_both_fail_error_surfaced(self, caplog):
        """Override fails AND fallback also fails → error result with WARNING log."""
        ctx = _ProgrammableCtx([
            (True, ValueError("model not found")),
            (True, RuntimeError("provider also unreachable")),
        ])
        with caplog.at_level(logging.WARNING, logger="plugins.bmad.lib.delegation"):
            out = delegation.fan_out(
                ctx, ["g1"], parent_skill="bmad-code-review",
                model="claude-opus-4-7",
            )
        assert len(ctx.calls) == 2  # both attempted
        assert out[0]["error"] is True
        assert out[0]["status"] == "failure"
        warning_logs = [r for r in caplog.records if "fallback dispatch ALSO failed" in r.message]
        assert warning_logs, "expected a WARNING when both primary and fallback fail"

    def test_provider_only_override_also_triggers_fallback(self):
        """Override key set to non-None is sufficient to enable fallback path.

        Even if ``model`` is None, a non-None ``base_url`` triggers retry.
        """
        ctx = _ProgrammableCtx([
            (True, ValueError("bad provider")),
            (False, _ok_result("default ok")),
        ])
        out = delegation.fan_out(
            ctx, ["g1"], parent_skill="bmad-code-review",
            base_url="https://nope.example.com",
        )
        assert len(ctx.calls) == 2
        assert "base_url" in ctx.calls[0][1]
        assert "base_url" not in ctx.calls[1][1]
        assert out[0]["summary"] == "default ok"


# ── Fan-out across multiple goals: per-goal fallback ────────────────────────


class TestFanOutMultiGoalFallback:
    def test_one_failed_one_succeeded_one_recovered(self, caplog):
        """3 goals, 1 succeeds direct, 1 needs fallback, 1 fails entirely."""
        ctx = _ProgrammableCtx([
            (False, _ok_result("g1 ok")),                               # g1 primary
            (True, ValueError("opus unreachable")),                     # g2 primary fails
            (False, _ok_result("g2 fallback ok")),                      # g2 fallback ok
            (True, RuntimeError("g3 first error")),                     # g3 primary fails
            (True, RuntimeError("g3 fallback also dead")),              # g3 fallback fails
        ])
        with caplog.at_level(logging.INFO, logger="plugins.bmad.lib.delegation"):
            out = delegation.fan_out(
                ctx, ["g1", "g2", "g3"], parent_skill="bmad-code-review",
                model="claude-opus-4-7",
            )
        assert len(out) == 3
        assert out[0]["summary"] == "g1 ok"
        assert out[1]["summary"] == "g2 fallback ok"
        assert out[2]["error"] is True
        # 5 dispatch calls total: g1 once, g2 twice, g3 twice
        assert len(ctx.calls) == 5


# ── delegate_one mirror tests ───────────────────────────────────────────────


class TestDelegateOneFallback:
    def test_override_fails_falls_back(self):
        ctx = _ProgrammableCtx([
            (True, ValueError("opus not found")),
            (False, _ok_result("ok via default")),
        ])
        out = delegation.delegate_one(
            ctx, "single goal", parent_skill="bmad-code-review",
            model="claude-opus-4-7",
        )
        assert len(ctx.calls) == 2
        assert out["summary"] == "ok via default"

    def test_no_override_no_retry(self):
        ctx = _ProgrammableCtx([(True, RuntimeError("transient"))])
        out = delegation.delegate_one(ctx, "g", parent_skill="test")
        assert len(ctx.calls) == 1
        assert out["error"] is True
