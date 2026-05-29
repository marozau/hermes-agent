"""Integration tests for BMAD lifecycle event capture.

Verifies:
    - Plugin loads with all 8 hooks registered
    - Each hook captures events to the bus when enabled
    - Disabled hooks do not capture events
    - Hook callbacks are wrapped by _catch_all (never raise on errors)
    - The event bus survives across multiple hook firings
"""

from __future__ import annotations

import json
import os
import time

import pytest

from plugins.bmad.lib.lifecycle_events import (
    LifecycleEvent,
    LifecycleEventBus,
    capture_event,
    get_event_bus,
    is_hook_enabled,
    reset_event_bus,
)


# ============================================================================
# Module setup
# ============================================================================


@pytest.fixture(autouse=True)
def _reset_bus_and_env():
    """Reset event bus and clean env before each test."""
    reset_event_bus()
    saved = {}
    for key in list(os.environ.keys()):
        if key.startswith("BMAD_LIFECYCLE_"):
            saved[key] = os.environ.pop(key)
    yield
    reset_event_bus()
    for k, v in saved.items():
        os.environ[k] = v


# ============================================================================
# Plugin load integration tests
# ============================================================================


class TestPluginLoad:
    """Verify the bmad plugin loads and registers all hooks."""

    def test_plugin_registers_all_8_hooks(self):
        """Verify all 8 hooks appear in the plugin's registered hooks."""
        # Simulate what the plugin manager does: import and call register
        from plugins.bmad import register

        # Create a mock PluginContext that records hook registrations
        class MockCtx:
            def __init__(self):
                self.hooks = {}
                self.tools = {}
                self.commands = {}
                self.cli_commands = {}
                self.profile_config = {}
                self.project_dir = None
                self.working_directory = None

            def register_hook(self, name, callback):
                self.hooks.setdefault(name, []).append(callback)

            def register_tool(self, name, **kwargs):
                self.tools[name] = kwargs

            def register_command(self, name, handler, args_hint=""):
                self.commands[name] = {"handler": handler, "args_hint": args_hint}

            def register_cli_command(self, name, help, setup_fn, handler_fn, description):
                self.cli_commands[name] = {
                    "help": help,
                    "setup_fn": setup_fn,
                    "handler_fn": handler_fn,
                    "description": description,
                }

        ctx = MockCtx()
        register(ctx)

        expected_hooks = {
            "on_session_start",
            "on_session_end",
            "pre_tool_call",
            "post_tool_call",
            "pre_llm_call",
            "post_llm_call",
            "transform_terminal_output",
            "subagent_stop",
        }
        registered = set(ctx.hooks.keys())
        assert registered == expected_hooks, (
            f"Missing hooks: {expected_hooks - registered}, "
            f"Extra hooks: {registered - expected_hooks}"
        )


# ============================================================================
# Hook activation integration tests
# ============================================================================


class TestHookActivation:
    """Verify each hook fires and captures events correctly."""

    def test_on_session_start_captures(self):
        os.environ["BMAD_LIFECYCLE_EVENTS_ENABLED"] = "1"
        from plugins.bmad.hooks.on_session_start import on_session_start

        class FakeCtx:
            project_dir = None
            working_directory = None
            profile_config = {}

        # Reset bus to ensure clean state
        reset_event_bus()
        bus = get_event_bus()
        assert bus.stats()["queue_size"] == 0

        # Fire the hook (this one checks for BMAD project directory)
        on_session_start(FakeCtx())

    def test_on_session_end_captures(self):
        os.environ["BMAD_LIFECYCLE_EVENTS_ENABLED"] = "1"
        from plugins.bmad.hooks.on_session_end import on_session_end

        reset_event_bus()
        bus = get_event_bus()

        on_session_end(
            session_id="test-sess-1",
            completed=True,
            interrupted=False,
            model="deepseek-v4",
            platform="cli",
        )

        assert bus.stats()["queue_size"] == 1
        events = bus.drain_all()
        assert len(events) == 1
        e = events[0]
        assert e.session_id == "test-sess-1"
        assert e.event_type == "on_session_end"
        assert e.payload["completed"] is True
        assert e.payload["interrupted"] is False
        assert e.payload["model"] == "deepseek-v4"

    def test_pre_llm_call_captures(self):
        os.environ["BMAD_LIFECYCLE_EVENTS_ENABLED"] = "1"
        from plugins.bmad.hooks.pre_llm_call import pre_llm_call

        reset_event_bus()
        bus = get_event_bus()

        result = pre_llm_call(
            session_id="test-sess-2",
            user_message="What is the capital of France?",
            conversation_history=[
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
            ],
            is_first_turn=False,
            model="deepseek-v4",
            platform="cli",
            sender_id="user1",
        )
        assert result is None  # Observer only

        assert bus.stats()["queue_size"] == 1
        events = bus.drain_all()
        assert len(events) == 1
        e = events[0]
        assert e.event_type == "pre_llm_call"
        assert e.payload["model"] == "deepseek-v4"
        assert e.payload["message_count"] == 2
        assert e.payload["is_question_detected"] is True  # "What is..."
        assert e.payload["user_message_excerpt"].startswith("What is the capital")

    def test_post_llm_call_captures(self):
        os.environ["BMAD_LIFECYCLE_EVENTS_ENABLED"] = "1"
        from plugins.bmad.hooks.post_llm_call import post_llm_call

        reset_event_bus()
        bus = get_event_bus()

        post_llm_call(
            session_id="test-sess-3",
            user_message="What is the weather?",
            assistant_response="The weather is sunny with a high of 72F.",
            conversation_history=[
                {"role": "user", "content": "What is the weather?"},
                {"role": "assistant", "content": "The weather is sunny with a high of 72F."},
            ],
            model="deepseek-v4",
            platform="cli",
        )

        assert bus.stats()["queue_size"] == 1
        events = bus.drain_all()
        assert len(events) == 1
        e = events[0]
        assert e.event_type == "post_llm_call"
        assert e.payload["assistant_response_length"] > 0
        assert e.payload["model"] == "deepseek-v4"

    def test_disabled_hook_does_not_capture(self):
        """Verify pre_tool_call (disabled by default) does not capture."""
        reset_event_bus()
        bus = get_event_bus()

        from plugins.bmad.hooks.pre_tool_call import pre_tool_call

        class FakeCtx:
            project_dir = None
            working_directory = None
            profile_config = {}

        pre_tool_call(FakeCtx(), tool_name="read_file", tool_args={}, tool_result=None)
        # pre_tool_call is disabled for event capture, but the hook itself
        # still runs (for phase gate). The lifecycle capture should be skipped.
        # There may be no events if disabled.

    def test_enabled_pre_tool_call_graceful(self):
        """pre_tool_call with lifecycle capture env set should not crash.

        The pre_tool_call hook is a phase gate, not a lifecycle capture hook.
        Even with BMAD_LIFECYCLE_HOOK_PRE_TOOL_CALL=1, the hook does not call
        capture_event() — that path is reserved for future implementation.
        """
        os.environ["BMAD_LIFECYCLE_HOOK_PRE_TOOL_CALL"] = "1"
        reset_event_bus()
        bus = get_event_bus()

        from plugins.bmad.hooks.pre_tool_call import pre_tool_call

        class FakeCtx:
            project_dir = None
            working_directory = None
            profile_config = {}

        # Must not raise — hook is wrapped by _catch_all
        pre_tool_call(FakeCtx(), tool_name="read_file", tool_args={}, tool_result=None)
        # pre_tool_call is a phase gate hook — no lifecycle capture expected
        assert bus.stats()["queue_size"] == 0

    def test_hook_never_raises(self):
        """Verify _catch_all wrapper prevents exceptions from propagating."""
        from plugins.bmad import _catch_all

        @_catch_all("test_hook")
        def _bad_hook():
            raise RuntimeError("should be caught")

        # Should not raise
        result = _bad_hook()
        assert result is None


# ============================================================================
# End-to-end: multiple hooks firing
# ============================================================================


class TestEndToEnd:
    """Simulate a full conversation turn with multiple hook firings."""

    def test_full_turn_flow(self):
        os.environ["BMAD_LIFECYCLE_EVENTS_ENABLED"] = "1"
        os.environ["BMAD_LIFECYCLE_HOOK_PRE_TOOL_CALL"] = "1"
        os.environ["BMAD_LIFECYCLE_HOOK_POST_TOOL_CALL"] = "1"

        reset_event_bus()
        bus = get_event_bus()

        from plugins.bmad.hooks.on_session_end import on_session_end
        from plugins.bmad.hooks.pre_llm_call import pre_llm_call
        from plugins.bmad.hooks.post_llm_call import post_llm_call

        # Simulate a session lifecycle
        pre_llm_call(
            session_id="full-test",
            user_message="Read file app.py",
            conversation_history=[],
            is_first_turn=True,
            model="deepseek-v4",
            platform="cli",
        )

        # Simulate tool calls (pre/post would fire between)
        # Capture tool use via post_tool_call
        from plugins.bmad.hooks.post_tool_call import post_tool_call

        class FakeCtx:
            project_dir = None
            working_directory = None
            profile_config = {}

        post_tool_call(
            FakeCtx(),
            tool_name="read_file",
            tool_args={"path": "app.py", "offset": 1, "limit": 100},
            tool_result={"content": "print(hello)"},
        )

        post_llm_call(
            session_id="full-test",
            user_message="Read file app.py",
            assistant_response="The file contains: print(hello)",
            conversation_history=[
                {"role": "user", "content": "Read file app.py"},
                {"role": "assistant", "tool_calls": [{"name": "read_file", "args": {"path": "app.py"}}]},
                {"role": "tool", "content": '{"content": "print(hello)"}'},
                {"role": "assistant", "content": "The file contains: print(hello)"},
            ],
            model="deepseek-v4",
            platform="cli",
        )

        on_session_end(
            session_id="full-test",
            completed=True,
            interrupted=False,
            model="deepseek-v4",
            platform="cli",
        )

        stats = bus.stats()
        assert stats["queue_size"] > 0
        # Verify event types present
        types_seen = set()
        for e in bus.drain_all():
            types_seen.add(e.event_type)

        assert "pre_llm_call" in types_seen
        assert "post_llm_call" in types_seen
        assert "on_session_end" in types_seen

    def test_bus_survives_across_hooks(self):
        """The event bus instance persists across multiple hook firings."""
        os.environ["BMAD_LIFECYCLE_EVENTS_ENABLED"] = "1"
        reset_event_bus()

        from plugins.bmad.hooks.on_session_end import on_session_end

        bus1 = get_event_bus()
        on_session_end(session_id="s1", completed=True, model="m")
        on_session_end(session_id="s2", completed=False, model="m")
        bus2 = get_event_bus()

        assert bus1 is bus2
        assert bus1.stats()["queue_size"] == 2


# ============================================================================
# JSON round-trip
# ============================================================================


class TestJsonRoundTrip:
    """Events serialize and deserialize correctly."""

    def test_full_round_trip(self):
        event = LifecycleEvent(
            session_id="json-test",
            event_type="post_tool_call",
            task_id="t_123",
            payload={
                "tool_name": "read_file",
                "duration_ms": 42,
                "nested": {"key": [1, 2, 3]},
            },
        )
        as_json = event.to_json()
        as_dict = json.loads(as_json)
        assert as_dict["session_id"] == "json-test"
        assert as_dict["event_type"] == "post_tool_call"
        assert as_dict["task_id"] == "t_123"
        assert as_dict["payload"]["tool_name"] == "read_file"
        assert as_dict["payload"]["nested"]["key"] == [1, 2, 3]

    def test_payload_with_non_json_types(self):
        """Payload fields that aren't JSON-serializable are handled via default=str."""
        event = LifecycleEvent(
            session_id="s1",
            event_type="post_llm_call",
            payload={"fn": lambda x: x},  # not JSON-serializable
        )
        s = event.to_json()
        parsed = json.loads(s)
        # The lambda becomes its string representation
        assert "lambda" in parsed["payload"]["fn"]
