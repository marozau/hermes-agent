"""Tests for plugins.bmad.lib.lifecycle_events — event bus and capture.

Tests cover:
    - LifecycleEvent dataclass: creation, dedup_key, to_dict, to_json
    - LifecycleEventBus: push, drain, drain_all, peek, stats, clear, dedup
    - capture_event: enablement, task_id resolution, env vars
    - Hook enablement: global toggle, per-hook overrides, defaults
"""

from __future__ import annotations

import json
import os
import time

import pytest

from plugins.bmad.lib.lifecycle_events import (
    LifecycleEvent,
    LifecycleEventBus,
    _DEFAULT_HOOKS_ENABLED,
    _DEFAULT_MAX_EVENTS,
    capture_event,
    get_event_bus,
    is_hook_enabled,
    reset_event_bus,
)


# ============================================================================
# Module-level setup/teardown
# ============================================================================


@pytest.fixture(autouse=True)
def _reset_bus():
    """Reset the event bus before and after each test."""
    reset_event_bus()
    # Save and restore env vars we may mutate
    saved = {}
    for key in (
        "BMAD_LIFECYCLE_EVENTS_ENABLED",
        "BMAD_LIFECYCLE_MAX_EVENTS",
        "HERMES_KANBAN_TASK",
    ):
        saved[key] = os.environ.get(key)
        os.environ.pop(key, None)
    # Also pop any BMAD_LIFECYCLE_HOOK_* vars
    hook_vars = [k for k in os.environ if k.startswith("BMAD_LIFECYCLE_HOOK_")]
    for k in hook_vars:
        saved[k] = os.environ.pop(k, None)
    yield
    reset_event_bus()
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v
        elif k in os.environ:
            del os.environ[k]


# ============================================================================
# LifecycleEvent dataclass
# ============================================================================


class TestLifecycleEvent:
    """Tests for the LifecycleEvent dataclass."""

    def test_creation_defaults(self):
        event = LifecycleEvent(session_id="sess-1", event_type="on_session_start")
        assert event.session_id == "sess-1"
        assert event.event_type == "on_session_start"
        assert event.task_id == ""
        assert event.timestamp > 0
        assert isinstance(event.event_id, str)
        assert len(event.event_id) == 36  # UUID
        assert event.payload == {}

    def test_creation_with_all_fields(self):
        event = LifecycleEvent(
            session_id="sess-2",
            event_type="post_tool_call",
            task_id="t_abc123",
            payload={"tool": "read_file", "duration_ms": 42},
        )
        assert event.session_id == "sess-2"
        assert event.event_type == "post_tool_call"
        assert event.task_id == "t_abc123"
        assert event.payload["tool"] == "read_file"

    def test_dedup_key_same_event_same_key(self):
        # Lock to a specific integer second so we don't cross a boundary
        ts = 1780059000.0
        e1 = LifecycleEvent(
            session_id="sess-x",
            event_type="pre_llm_call",
            timestamp=ts,
            payload={"msg": "hello"},
        )
        e2 = LifecycleEvent(
            session_id="sess-x",
            event_type="pre_llm_call",
            timestamp=ts + 0.5,  # same integer second
            payload={"msg": "hello"},
        )
        assert e1.dedup_key() == e2.dedup_key()

    def test_dedup_key_different_session_different_key(self):
        now = time.time()
        e1 = LifecycleEvent(
            session_id="sess-a",
            event_type="pre_llm_call",
            timestamp=now,
        )
        e2 = LifecycleEvent(
            session_id="sess-b",
            event_type="pre_llm_call",
            timestamp=now,
        )
        assert e1.dedup_key() != e2.dedup_key()

    def test_dedup_key_different_payload_different_key(self):
        now = time.time()
        e1 = LifecycleEvent(
            session_id="sess-x",
            event_type="pre_llm_call",
            timestamp=now,
            payload={"msg": "hello"},
        )
        e2 = LifecycleEvent(
            session_id="sess-x",
            event_type="pre_llm_call",
            timestamp=now,
            payload={"msg": "world"},
        )
        assert e1.dedup_key() != e2.dedup_key()

    def test_dedup_key_different_second_different_key(self):
        t = time.time()
        e1 = LifecycleEvent(
            session_id="sess-x",
            event_type="pre_llm_call",
            timestamp=t,
        )
        e2 = LifecycleEvent(
            session_id="sess-x",
            event_type="pre_llm_call",
            timestamp=t + 1.1,  # next integer second
        )
        assert e1.dedup_key() != e2.dedup_key()

    def test_to_dict_contains_all_fields(self):
        event = LifecycleEvent(
            session_id="sess-3",
            event_type="on_session_end",
            task_id="t_xyz",
            payload={"completed": True},
        )
        d = event.to_dict()
        assert d["session_id"] == "sess-3"
        assert d["event_type"] == "on_session_end"
        assert d["task_id"] == "t_xyz"
        assert d["payload"]["completed"] is True
        assert "event_id" in d
        assert "timestamp" in d

    def test_to_json_valid(self):
        event = LifecycleEvent(session_id="sess-4", event_type="post_llm_call")
        s = event.to_json()
        parsed = json.loads(s)
        assert parsed["session_id"] == "sess-4"


# ============================================================================
# LifecycleEventBus
# ============================================================================


class TestLifecycleEventBus:
    """Tests for the LifecycleEventBus."""

    def test_push_and_stats(self):
        bus = LifecycleEventBus(max_events=100)
        e = LifecycleEvent(session_id="s1", event_type="on_session_start")
        assert bus.push(e) is True
        assert bus.stats()["queue_size"] == 1
        assert bus.stats()["counters"]["on_session_start"] == 1

    def test_dedup_rejects_duplicate(self):
        bus = LifecycleEventBus(max_events=100)
        now = time.time()
        e1 = LifecycleEvent(session_id="s1", event_type="pre_llm_call", timestamp=now)
        e2 = LifecycleEvent(session_id="s1", event_type="pre_llm_call", timestamp=now)
        assert bus.push(e1) is True
        assert bus.push(e2) is False  # deduplicated
        assert bus.stats()["queue_size"] == 1

    def test_drain_removes_events(self):
        bus = LifecycleEventBus(max_events=100)
        for i in range(5):
            bus.push(LifecycleEvent(session_id=f"s{i}", event_type="post_llm_call"))
        assert bus.stats()["queue_size"] == 5
        drained = bus.drain(3)
        assert len(drained) == 3
        assert bus.stats()["queue_size"] == 2
        # Verify ordering: oldest first
        assert drained[0].session_id == "s0"
        assert drained[1].session_id == "s1"
        assert drained[2].session_id == "s2"

    def test_drain_all_empties_queue(self):
        bus = LifecycleEventBus(max_events=100)
        for i in range(3):
            bus.push(LifecycleEvent(session_id=f"s{i}", event_type="pre_llm_call"))
        drained = bus.drain_all()
        assert len(drained) == 3
        assert bus.stats()["queue_size"] == 0

    def test_peek_does_not_remove(self):
        bus = LifecycleEventBus(max_events=100)
        bus.push(LifecycleEvent(session_id="s1", event_type="on_session_end"))
        bus.push(LifecycleEvent(session_id="s2", event_type="on_session_end"))
        peeked = bus.peek(1)
        assert len(peeked) == 1
        assert peeked[0].session_id == "s2"  # newest
        assert bus.stats()["queue_size"] == 2  # unchanged

    def test_clear_resets_everything(self):
        bus = LifecycleEventBus(max_events=100)
        bus.push(LifecycleEvent(session_id="s1", event_type="on_session_start"))
        bus.clear()
        assert bus.stats()["queue_size"] == 0
        assert bus.stats()["counters"] == {}
        # After clear, dedup should allow the same key again
        now = time.time()
        e1 = LifecycleEvent(session_id="s1", event_type="on_session_start", timestamp=now)
        bus.push(e1)
        bus.clear()
        e2 = LifecycleEvent(session_id="s1", event_type="on_session_start", timestamp=now)
        assert bus.push(e2) is True

    def test_max_events_bounds_queue(self):
        bus = LifecycleEventBus(max_events=3)
        for i in range(5):
            bus.push(LifecycleEvent(session_id=f"s{i}", event_type="pre_llm_call"))
        assert bus.stats()["queue_size"] == 3
        drained = bus.drain_all()
        # Oldest events dropped; kept s2, s3, s4
        assert drained[0].session_id == "s2"
        assert drained[2].session_id == "s4"

    def test_stats_counts_by_type(self):
        bus = LifecycleEventBus(max_events=100)
        # Use different session_ids to avoid dedup rejecting the second push
        bus.push(LifecycleEvent(session_id="s1", event_type="on_session_start"))
        bus.push(LifecycleEvent(session_id="s2", event_type="on_session_start"))
        bus.push(LifecycleEvent(session_id="s3", event_type="on_session_end"))
        stats = bus.stats()
        assert stats["counters"]["on_session_start"] == 2
        assert stats["counters"]["on_session_end"] == 1


# ============================================================================
# Get/reset event bus
# ============================================================================


class TestEventBusSingleton:
    """Tests for get_event_bus and reset_event_bus."""

    def test_singleton_same_instance(self):
        bus1 = get_event_bus()
        bus2 = get_event_bus()
        assert bus1 is bus2

    def test_reset_creates_new_instance(self):
        bus1 = get_event_bus()
        bus1.push(LifecycleEvent(session_id="s1", event_type="on_session_start"))
        reset_event_bus()
        bus2 = get_event_bus()
        assert bus2 is not bus1
        assert bus2.stats()["queue_size"] == 0

    def test_max_events_from_env(self):
        os.environ["BMAD_LIFECYCLE_MAX_EVENTS"] = "500"
        reset_event_bus()
        bus = get_event_bus()
        assert bus._max_events == 500


# ============================================================================
# Hook enablement
# ============================================================================


class TestHookEnablement:
    """Tests for is_hook_enabled and env var config."""

    def test_defaults(self):
        # pre_tool_call disabled by default (high volume)
        assert is_hook_enabled("pre_tool_call") is False
        assert is_hook_enabled("post_tool_call") is False
        # Most others enabled
        assert is_hook_enabled("on_session_start") is True
        assert is_hook_enabled("on_session_end") is True
        assert is_hook_enabled("pre_llm_call") is True
        assert is_hook_enabled("post_llm_call") is True

    def test_global_disable(self):
        os.environ["BMAD_LIFECYCLE_EVENTS_ENABLED"] = "0"
        assert is_hook_enabled("on_session_start") is False
        assert is_hook_enabled("on_session_end") is False
        assert is_hook_enabled("pre_llm_call") is False

    def test_global_enable_then_per_hook_override(self):
        os.environ["BMAD_LIFECYCLE_EVENTS_ENABLED"] = "1"
        os.environ["BMAD_LIFECYCLE_HOOK_PRE_TOOL_CALL"] = "1"
        assert is_hook_enabled("pre_tool_call") is True
        # on_session_start stays default
        assert is_hook_enabled("on_session_start") is True

    def test_per_hook_disable(self):
        os.environ["BMAD_LIFECYCLE_HOOK_ON_SESSION_START"] = "0"
        assert is_hook_enabled("on_session_start") is False

    def test_unknown_hook_returns_false(self):
        assert is_hook_enabled("nonexistent_hook") is False


# ============================================================================
# Capture event
# ============================================================================


class TestCaptureEvent:
    """Tests for capture_event function."""

    def test_capture_enabled_hook(self):
        result = capture_event(
            session_id="sess-test",
            event_type="on_session_start",
            payload={"model": "deepseek-v4"},
        )
        assert result is not None
        assert result.session_id == "sess-test"
        assert result.event_type == "on_session_start"
        assert result.payload["model"] == "deepseek-v4"

    def test_capture_disabled_hook_returns_none(self):
        # pre_tool_call is disabled by default
        result = capture_event(
            session_id="sess-test",
            event_type="pre_tool_call",
        )
        assert result is None

    def test_capture_respects_per_hook_override(self):
        os.environ["BMAD_LIFECYCLE_HOOK_PRE_TOOL_CALL"] = "1"
        result = capture_event(
            session_id="sess-test",
            event_type="pre_tool_call",
            payload={"tool": "read_file"},
        )
        assert result is not None
        assert result.event_type == "pre_tool_call"
        assert result.payload["tool"] == "read_file"

    def test_capture_resolves_task_id_from_env(self):
        os.environ["HERMES_KANBAN_TASK"] = "t_test123"
        result = capture_event(
            session_id="sess-test",
            event_type="on_session_end",
        )
        assert result is not None
        assert result.task_id == "t_test123"

    def test_capture_explicit_task_id_overrides_env(self):
        os.environ["HERMES_KANBAN_TASK"] = "t_env"
        result = capture_event(
            session_id="sess-test",
            event_type="on_session_end",
            task_id="t_explicit",
        )
        assert result is not None
        assert result.task_id == "t_explicit"

    def test_capture_dedup_returns_none(self):
        now = time.time()
        result1 = capture_event(
            session_id="dup-test",
            event_type="on_session_start",
            payload={"ts": now},
        )
        # Monkey-patch timestamp to match for dedup test
        import plugins.bmad.lib.lifecycle_events as le
        orig = le.LifecycleEvent.__init__

        def _fixed_timestamp(self, *args, **kwargs):
            self.timestamp = now
            orig(self, *args, **kwargs)

        le.LifecycleEvent.__init__ = _fixed_timestamp
        try:
            result2 = capture_event(
                session_id="dup-test",
                event_type="on_session_start",
                payload={"ts": now},
            )
            assert result1 is not None
            assert result2 is None  # deduped
        finally:
            le.LifecycleEvent.__init__ = orig

    def test_capture_enqueues_to_bus(self):
        bus = get_event_bus()
        assert bus.stats()["queue_size"] == 0
        capture_event(session_id="s1", event_type="on_session_start")
        assert bus.stats()["queue_size"] == 1
        assert bus.stats()["counters"]["on_session_start"] == 1

    def test_capture_disabled_does_not_enqueue(self):
        bus = get_event_bus()
        capture_event(session_id="s1", event_type="pre_tool_call")
        assert bus.stats()["queue_size"] == 0


# ============================================================================
# Hook handler signatures (smoke tests)
# ============================================================================


class TestHookHandlers:
    """Smoke tests: verify each hook handler accepts its expected kwargs."""

    def test_on_session_end_accepts_kwargs(self):
        from plugins.bmad.hooks.on_session_end import on_session_end
        # Should not raise
        on_session_end(
            session_id="test-sess",
            completed=True,
            interrupted=False,
            model="deepseek-v4",
            platform="cli",
        )

    def test_pre_llm_call_accepts_kwargs(self):
        from plugins.bmad.hooks.pre_llm_call import pre_llm_call
        result = pre_llm_call(
            session_id="test-sess",
            user_message="What is the weather?",
            conversation_history=[
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi!"},
            ],
            is_first_turn=False,
            model="deepseek-v4",
            platform="cli",
            sender_id="user123",
        )
        # pre_llm_call always returns None (observer only)
        assert result is None

    def test_post_llm_call_accepts_kwargs(self):
        from plugins.bmad.hooks.post_llm_call import post_llm_call
        # Should not raise
        post_llm_call(
            session_id="test-sess",
            user_message="What is the weather?",
            assistant_response="The weather is sunny.",
            conversation_history=[
                {"role": "user", "content": "What is the weather?"},
                {"role": "assistant", "content": "The weather is sunny."},
            ],
            model="deepseek-v4",
            platform="cli",
        )

    def test_hooks_capture_to_bus(self):
        """Integration: verify hooks actually capture events to the bus."""
        bus = get_event_bus()
        assert bus.stats()["queue_size"] == 0

        from plugins.bmad.hooks.on_session_end import on_session_end
        on_session_end(session_id="s1", completed=True, model="test-model")

        assert bus.stats()["queue_size"] == 1
        events = bus.drain_all()
        assert len(events) == 1
        e = events[0]
        assert e.session_id == "s1"
        assert e.event_type == "on_session_end"
        assert e.payload["completed"] is True
        assert e.payload["model"] == "test-model"


# ============================================================================
# Question detection (pre_llm_call helper)
# ============================================================================


class TestQuestionDetection:
    """Tests for the _detect_question heuristic in pre_llm_call."""

    def test_question_mark(self):
        from plugins.bmad.hooks.pre_llm_call import _detect_question
        assert _detect_question("What is this?") is True

    def test_interrogative_start(self):
        from plugins.bmad.hooks.pre_llm_call import _detect_question
        assert _detect_question("How do I fix this error") is True
        assert _detect_question("Why is it not working") is True
        assert _detect_question("When does this expire") is True
        assert _detect_question("Where is the config file") is True
        assert _detect_question("Who changed this") is True

    def test_courtesy_start(self):
        from plugins.bmad.hooks.pre_llm_call import _detect_question
        assert _detect_question("Can you help me") is True
        assert _detect_question("Could you explain this") is True
        assert _detect_question("Please show me the code") is True
        assert _detect_question("Tell me about this") is True
        assert _detect_question("Show me the logs") is True

    def test_non_question(self):
        from plugins.bmad.hooks.pre_llm_call import _detect_question
        assert _detect_question("Fix the bug in auth.py") is False
        assert _detect_question("Run the tests") is False
        assert _detect_question("") is False
        assert _detect_question("Here is the report") is False
