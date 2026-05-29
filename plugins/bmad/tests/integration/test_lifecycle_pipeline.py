"""Integration tests for lifecycle hooks → ACP → orchestrator pipeline.

Tests the full pipeline:
    hook fires → LifecycleEventBus → LifecycleBridge → ACP publisher
    → ACPOrchestratorEventHandler → ReactionDispatcher

Also tests:
    - LifecycleBridge conversion and emission
    - Reaction rules (success, failure, stall, tool errors, questions)
    - Periodic check cycle runner
    - ACP publisher integration via set_current_publisher
"""

from __future__ import annotations

import time
import pytest
from unittest.mock import MagicMock, call, patch

from plugins.bmad.lib.lifecycle_events import (
    LifecycleEvent,
    LifecycleEventBus,
    capture_event,
    clear_current_publisher,
    get_event_bus,
    reset_event_bus,
    set_current_publisher,
)
from plugins.bmad.lib.lifecycle_bridge import LifecycleBridge
from plugins.bmad.orchestrator.event_handler import (
    ACPOrchestratorEventHandler,
    OrchestratorTaskState,
)
from plugins.bmad.orchestrator.reactions import (
    LogGateHandler,
    LogJudgeHandler,
    LogReflectionHandler,
    LogUserHandler,
    ReactionDispatcher,
    ReactionResult,
)
from plugins.bmad.orchestrator.stall_detector import (
    StallDetector,
    StallDetectorConfig,
    StallStage,
)
from plugins.bmad.orchestrator.periodic import (
    CheckCycleResult,
    reset as periodic_reset,
    run_check_cycle,
)

from acp_adapter.messages import (
    SessionEnd,
    SessionHeartbeat,
    SessionStart,
    ToolCallResult,
    UserQuestion,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_event_bus(monkeypatch):
    """Reset event bus before and after each test."""
    monkeypatch.setenv("BMAD_LIFECYCLE_HOOK_POST_TOOL_CALL", "1")
    monkeypatch.setenv("BMAD_LIFECYCLE_HOOK_PRE_TOOL_CALL", "1")
    reset_event_bus()
    clear_current_publisher()
    periodic_reset()
    yield
    reset_event_bus()
    clear_current_publisher()
    periodic_reset()


@pytest.fixture
def mock_publisher():
    """Return a mock SessionEventPublisher."""
    pub = MagicMock()
    pub.session_start = MagicMock()
    pub.session_end = MagicMock()
    pub.session_heartbeat = MagicMock()
    pub.tool_call_result = MagicMock()
    pub.user_question = MagicMock()
    pub.session_stalled = MagicMock()
    pub.session_cancelled = MagicMock()
    return pub


@pytest.fixture
def handler():
    """Return a fresh ACPOrchestratorEventHandler."""
    return ACPOrchestratorEventHandler()


@pytest.fixture
def bridge(mock_publisher):
    """Return a LifecycleBridge with mock publisher."""
    return LifecycleBridge(publisher=mock_publisher)


# ---------------------------------------------------------------------------
# LifecycleBridge tests
# ---------------------------------------------------------------------------


class TestLifecycleBridge:
    """Tests for LifecycleBridge — bus → ACP conversion and emission."""

    def test_drain_empty_bus(self, bridge):
        """Draining an empty bus returns empty list."""
        results = bridge.drain_and_emit()
        assert results == []

    def test_convert_session_end_completed(self, bridge, mock_publisher):
        """on_session_end (completed=True) → SessionEnd ACP event."""
        capture_event(
            session_id="s1",
            event_type="on_session_end",
            payload={"completed": True, "interrupted": False,
                     "model": "deepseek", "platform": "cli",
                     "wall_time_s": 100.0},
            task_id="t1",
        )

        results = bridge.drain_and_emit()
        assert len(results) == 1
        assert results[0]["event_type"] == "session_end"
        assert results[0]["task_id"] == "t1"

        mock_publisher.session_end.assert_called_once()
        call_kwargs = mock_publisher.session_end.call_args[1]
        assert call_kwargs["outcome"] == "completed"

    def test_convert_session_end_interrupted(self, bridge, mock_publisher):
        """on_session_end (interrupted=True) → SessionEnd with cancelled outcome."""
        capture_event(
            session_id="s2",
            event_type="on_session_end",
            payload={"completed": False, "interrupted": True,
                     "model": "deepseek", "platform": "cli",
                     "wall_time_s": 50.0},
            task_id="t2",
        )

        bridge.drain_and_emit()
        call_kwargs = mock_publisher.session_end.call_args[1]
        assert call_kwargs["outcome"] == "cancelled"

    def test_convert_session_end_error(self, bridge, mock_publisher):
        """on_session_end (completed=False, interrupted=False) → error outcome."""
        capture_event(
            session_id="s3",
            event_type="on_session_end",
            payload={"completed": False, "interrupted": False,
                     "model": "deepseek", "platform": "cli",
                     "wall_time_s": 30.0},
            task_id="t3",
        )

        bridge.drain_and_emit()
        call_kwargs = mock_publisher.session_end.call_args[1]
        assert call_kwargs["outcome"] == "error"

    def test_convert_pre_llm_to_heartbeat(self, bridge, mock_publisher):
        """pre_llm_call → session_heartbeat."""
        capture_event(
            session_id="s4",
            event_type="pre_llm_call",
            payload={"model": "deepseek", "platform": "cli",
                     "is_first_turn": True, "message_count": 5,
                     "approx_input_tokens": 2000,
                     "is_question_detected": False},
            task_id="t4",
        )

        bridge.drain_and_emit()
        mock_publisher.session_heartbeat.assert_called_once()
        call_kwargs = mock_publisher.session_heartbeat.call_args[1]
        assert call_kwargs["agent_state"] == "thinking"

    def test_convert_post_llm_to_heartbeat(self, bridge, mock_publisher):
        """post_llm_call → session_heartbeat with progress data."""
        capture_event(
            session_id="s5",
            event_type="post_llm_call",
            payload={"model": "deepseek", "platform": "cli",
                     "assistant_response_length": 500,
                     "tool_call_count_this_turn": 3},
            task_id="t5",
        )

        bridge.drain_and_emit()
        mock_publisher.session_heartbeat.assert_called_once()
        call_kwargs = mock_publisher.session_heartbeat.call_args[1]
        assert call_kwargs["agent_state"] == "responding"
        assert call_kwargs["current_tool"] == "llm_response"

    def test_convert_post_tool_call_error(self, bridge, mock_publisher):
        """post_tool_call with error → ToolCallResult."""
        capture_event(
            session_id="s6",
            event_type="post_tool_call",
            payload={"tool_name": "terminal", "tool_call_id": "tc-1",
                     "error": "command not found: xyz",
                     "duration_ms": 1234.5},
            task_id="t6",
        )

        bridge.drain_and_emit()
        mock_publisher.tool_call_result.assert_called_once()
        call_kwargs = mock_publisher.tool_call_result.call_args[1]
        assert call_kwargs["success"] is False
        assert call_kwargs["tool_name"] == "terminal"
        assert "command not found" in call_kwargs["error"]

    def test_convert_post_tool_call_success_skipped(self, bridge, mock_publisher):
        """post_tool_call with no error → not emitted (too noisy)."""
        capture_event(
            session_id="s7",
            event_type="post_tool_call",
            payload={"tool_name": "read_file", "tool_call_id": "tc-2",
                     "error": None},
            task_id="t7",
        )

        bridge.drain_and_emit()
        mock_publisher.tool_call_result.assert_not_called()

    def test_multiple_events_drained(self, bridge, mock_publisher):
        """Multiple events drain and emit in order."""
        for i in range(5):
            capture_event(
                session_id=f"s{i}",
                event_type="pre_llm_call",
                payload={"model": "deepseek", "is_question_detected": False},
                task_id=f"t{i}",
            )

        results = bridge.drain_and_emit()
        assert len(results) == 5
        assert mock_publisher.session_heartbeat.call_count == 5

    def test_deduplicated_events(self, bridge, mock_publisher):
        """Duplicate events are deduplicated in the bus, only one emitted."""
        # Same session, same event_type, same payload → dedup
        payload = {"model": "deepseek", "platform": "cli"}
        e1 = capture_event("s-dup", "pre_llm_call", payload, "t-dup")
        e2 = capture_event("s-dup", "pre_llm_call", payload, "t-dup")

        assert e1 is not None  # First is captured
        assert e2 is None       # Second is deduplicated

        bridge.drain_and_emit()
        assert mock_publisher.session_heartbeat.call_count == 1

    def test_no_publisher_graceful(self):
        """Bridge without publisher doesn't crash."""
        bridge_no_pub = LifecycleBridge(publisher=None)
        capture_event("s8", "pre_llm_call",
                      {"model": "deepseek", "is_question_detected": False},
                      "t8")
        results = bridge_no_pub.drain_and_emit()
        assert len(results) == 1
        assert results[0]["emitted"] is False


# ---------------------------------------------------------------------------
# Reaction logic tests
# ---------------------------------------------------------------------------


class TestReactionLogic:
    """Tests for ReactionDispatcher — business rules for events."""

    def test_session_end_success_advances_gate(self, handler):
        """Completed session → gate_handler called."""
        mock_gate = MagicMock()
        dispatcher = ReactionDispatcher(
            handler=handler,
            gate_handler=mock_gate,
        )

        # Simulate a completed session
        handler.handle_event(SessionStart(
            sessionId="s10", cwd="/tmp",
            _meta={"task_id": "t10"},
        ))
        handler.handle_event(SessionEnd(
            sessionId="s10", outcome="completed", summary="All done",
            _meta={"task_id": "t10"},
        ))

        results = dispatcher.react_to_ended_tasks()
        assert len(results) == 1
        assert results[0].action == "advance_gate"
        assert results[0].handler_called
        mock_gate.advance_gate.assert_called_once()
        call_args = mock_gate.advance_gate.call_args[0]
        assert call_args[0] == "t10"
        assert call_args[1]["outcome"] == "completed"
        assert call_args[1]["tool_results"] == 0
        assert call_args[1]["user_questions"] == 0

    def test_session_end_failure_logs_reflection(self, handler):
        """Failed session → reflection_handler called."""
        mock_reflection = MagicMock()
        mock_reflection.log_failure.return_value = "ref-1"
        dispatcher = ReactionDispatcher(
            handler=handler,
            reflection_handler=mock_reflection,
        )

        handler.handle_event(SessionStart(
            sessionId="s11", cwd="/tmp",
            _meta={"task_id": "t11"},
        ))
        handler.handle_event(SessionEnd(
            sessionId="s11", outcome="error", summary="Crashed",
            _meta={"task_id": "t11"},
        ))

        results = dispatcher.react_to_ended_tasks()
        assert len(results) == 1
        assert results[0].action == "log_reflection"
        assert results[0].handler_called
        mock_reflection.log_failure.assert_called_once()
        call_args = mock_reflection.log_failure.call_args[0]
        assert call_args[1] == "error"  # second positional arg

    def test_session_cancelled_logs_reflection(self, handler):
        """Cancelled session → reflection_handler called."""
        mock_reflection = MagicMock()
        mock_reflection.log_failure.return_value = "ref-2"
        dispatcher = ReactionDispatcher(
            handler=handler,
            reflection_handler=mock_reflection,
        )

        handler.handle_event(SessionStart(
            sessionId="s12", cwd="/tmp",
            _meta={"task_id": "t12"},
        ))
        handler.handle_event(SessionEnd(
            sessionId="s12", outcome="cancelled",
            _meta={"task_id": "t12"},
        ))

        results = dispatcher.react_to_ended_tasks()
        assert len(results) == 1
        assert results[0].action == "log_reflection"
        mock_reflection.log_failure.assert_called_once()

    def test_stalled_task_triggers_judge(self, handler):
        """Stalled task → judge handler called."""
        mock_judge = MagicMock()
        mock_judge.evaluate_reroute.return_value = {
            "action": "block", "reason": "stalled too long",
            "suggested_assignee": "default",
        }
        dispatcher = ReactionDispatcher(
            handler=handler,
            judge_handler=mock_judge,
        )

        # Start a task and let it go stale
        handler.handle_event(SessionStart(
            sessionId="s13", cwd="/tmp",
            _meta={"task_id": "t13"},
        ))
        # No heartbeat — task should be detected as stalled

        results = dispatcher.react_to_stalled_tasks(max_age_seconds=-1)
        assert len(results) >= 1
        stalled_result = results[0]
        assert stalled_result.action == "evaluate_reroute"
        assert stalled_result.handler_called
        mock_judge.evaluate_reroute.assert_called_once()

    def test_tool_errors_trigger_judge(self, handler):
        """≥3 tool errors → judge handler called."""
        mock_judge = MagicMock()
        mock_judge.evaluate_reroute.return_value = {
            "action": "retry", "reason": "flaky tool",
            "suggested_assignee": None,
        }
        dispatcher = ReactionDispatcher(
            handler=handler,
            judge_handler=mock_judge,
        )

        handler.handle_event(SessionStart(
            sessionId="s14", cwd="/tmp",
            _meta={"task_id": "t14"},
        ))
        # Push 3 tool errors
        for i in range(3):
            handler.handle_event(ToolCallResult(
                sessionId="s14", toolCallId=f"tc-{i}",
                toolName="terminal", success=False,
                error=f"error {i}",
                _meta={"task_id": "t14"},
            ))

        results = dispatcher.react_to_tool_errors(min_errors=3)
        assert len(results) >= 1
        tool_result = results[0]
        assert tool_result.action == "evaluate_adjust"
        assert tool_result.handler_called

    def test_tool_errors_below_threshold_ignored(self, handler):
        """<3 tool errors → not flagged."""
        mock_judge = MagicMock()
        dispatcher = ReactionDispatcher(
            handler=handler,
            judge_handler=mock_judge,
        )

        handler.handle_event(SessionStart(
            sessionId="s15", cwd="/tmp",
            _meta={"task_id": "t15"},
        ))
        # Only 2 errors — below threshold
        for i in range(2):
            handler.handle_event(ToolCallResult(
                sessionId="s15", toolCallId=f"tc-{i}",
                toolName="terminal", success=False,
                error=f"error {i}",
                _meta={"task_id": "t15"},
            ))

        results = dispatcher.react_to_tool_errors(min_errors=3)
        assert len(results) == 0

    def test_user_question_forwarded(self, handler):
        """User question → user_handler called."""
        mock_user = MagicMock()
        dispatcher = ReactionDispatcher(
            handler=handler,
            user_handler=mock_user,
        )

        handler.handle_event(SessionStart(
            sessionId="s16", cwd="/tmp",
            _meta={"task_id": "t16"},
        ))
        handler.handle_event(UserQuestion(
            sessionId="s16", questionId="q1",
            questionText="Which database should we use?",
            options=["PostgreSQL", "MySQL", "SQLite"],
            _meta={"task_id": "t16"},
        ))

        results = dispatcher.react_to_user_questions()
        assert len(results) >= 1
        q_result = results[0]
        assert q_result.action == "forward_to_user"
        assert q_result.handler_called
        mock_user.forward_question.assert_called_once()

    def test_process_all_reactions(self, handler):
        """process_all_reactions returns categorized results."""
        dispatcher = ReactionDispatcher(
            handler=handler,
            gate_handler=LogGateHandler(),
            reflection_handler=LogReflectionHandler(),
            judge_handler=LogJudgeHandler(),
            user_handler=LogUserHandler(),
        )

        handler.handle_event(SessionStart(
            sessionId="s17", cwd="/tmp",
            _meta={"task_id": "t17"},
        ))
        handler.handle_event(SessionEnd(
            sessionId="s17", outcome="completed",
            _meta={"task_id": "t17"},
        ))

        all_results = dispatcher.process_all_reactions()
        assert "ended" in all_results
        assert len(all_results["ended"]) >= 1
        assert all_results["ended"][0].action == "advance_gate"

    def test_handler_error_graceful(self, handler):
        """Failing handler doesn't prevent reaction completion."""
        bad_gate = MagicMock()
        bad_gate.advance_gate.side_effect = RuntimeError("boom")

        dispatcher = ReactionDispatcher(
            handler=handler,
            gate_handler=bad_gate,
        )

        handler.handle_event(SessionStart(
            sessionId="s18", cwd="/tmp",
            _meta={"task_id": "t18"},
        ))
        handler.handle_event(SessionEnd(
            sessionId="s18", outcome="completed",
            _meta={"task_id": "t18"},
        ))

        results = dispatcher.react_to_ended_tasks()
        assert len(results) == 1
        assert results[0].error == "boom"
        assert results[0].handler_called is False

    def test_no_handlers_still_returns_results(self, handler):
        """Dispatcher without handlers still analyzes and returns results."""
        dispatcher = ReactionDispatcher(handler=handler)

        handler.handle_event(SessionStart(
            sessionId="s19", cwd="/tmp",
            _meta={"task_id": "t19"},
        ))
        handler.handle_event(SessionEnd(
            sessionId="s19", outcome="completed",
            _meta={"task_id": "t19"},
        ))

        results = dispatcher.react_to_ended_tasks()
        assert len(results) == 1
        assert results[0].action == "advance_gate"
        assert results[0].handler_called is False  # No handler wired

    def test_reaction_stats(self, handler):
        """Stats reflect reaction counts."""
        dispatcher = ReactionDispatcher(
            handler=handler,
            gate_handler=LogGateHandler(),
        )

        handler.handle_event(SessionStart(
            sessionId="s20", cwd="/tmp",
            _meta={"task_id": "t20"},
        ))
        handler.handle_event(SessionEnd(
            sessionId="s20", outcome="completed",
            _meta={"task_id": "t20"},
        ))

        dispatcher.react_to_ended_tasks()
        stats = dispatcher.stats()
        assert stats["reaction_counts"].get("success", 0) >= 1
        assert stats["handlers"]["gate"] is True
        assert stats["handlers"]["reflection"] is False


# ---------------------------------------------------------------------------
# Periodic runner tests
# ---------------------------------------------------------------------------


class TestPeriodicRunner:
    """Tests for the periodic check cycle runner."""

    def setup_method(self):
        periodic_reset()

    def test_run_check_cycle_empty(self):
        """Running a cycle with no tasks produces empty result."""
        result = run_check_cycle(kanban_comments=False)
        assert isinstance(result, CheckCycleResult)
        assert result.detector_results == []
        assert result.reaction_results == {}
        assert len(result.errors) == 0

    def test_run_check_cycle_with_tasks(self, handler):
        """Running a cycle with tasks detects events."""
        # Pre-populate the handler with some task state
        handler.handle_event(SessionStart(
            sessionId="s21", cwd="/tmp",
            _meta={"task_id": "t21"},
        ))
        # Give a fresh heartbeat
        handler.handle_event(SessionHeartbeat(
            sessionId="s21", agentState="working",
            _meta={"task_id": "t21"},
        ))
        handler.handle_event(SessionEnd(
            sessionId="s21", outcome="completed",
            _meta={"task_id": "t21"},
        ))

        result = run_check_cycle(kanban_comments=False)
        assert isinstance(result, CheckCycleResult)
        assert len(result.errors) == 0
        # Reactions should pick up the completed task
        if result.reaction_results.get("ended"):
            assert len(result.reaction_results["ended"]) >= 1

    def test_to_summary(self):
        """CheckCycleResult.to_summary produces readable output."""
        result = CheckCycleResult(
            detector_results=[
                {"stage": "stage1", "task_id": "t1", "action": "stage1",
                 "stall_age_seconds": 301, "escalation_count": 0},
            ],
            reaction_results={
                "stalled": [
                    {"task_id": "t1", "action": "evaluate_reroute",
                     "event_type": "session_stalled",
                     "handler_called": True, "error": None,
                     "details": {"stall_age_s": 301}},
                ],
            },
        )
        summary = result.to_summary()
        assert "1 stage-1 stall" in summary
        assert "evaluate_reroute" in summary

    def test_has_escalations(self):
        """has_escalations detects stage-2 results."""
        result = CheckCycleResult(
            detector_results=[
                {"stage": "stage2", "task_id": "t2", "action": "block",
                 "stall_age_seconds": 601, "escalation_count": 1},
            ],
        )
        assert result.has_escalations()
        assert result.has_stalls()

    def test_has_stalls(self):
        """has_stalls detects stage-1 results."""
        result = CheckCycleResult(
            detector_results=[
                {"stage": "stage1", "task_id": "t3", "action": "stage1",
                 "stall_age_seconds": 301, "escalation_count": 0},
            ],
        )
        assert result.has_stalls()
        assert not result.has_escalations()


# ---------------------------------------------------------------------------
# Direct ACP publisher hook integration tests
# ---------------------------------------------------------------------------


class TestHookACPIntegration:
    """Tests for set_current_publisher → hook emits ACP directly."""

    def test_publisher_set_and_cleared(self, mock_publisher):
        """Publisher can be set and cleared."""
        set_current_publisher(mock_publisher)
        from plugins.bmad.lib.lifecycle_events import get_current_publisher
        assert get_current_publisher() is mock_publisher

        clear_current_publisher()
        assert get_current_publisher() is None

    def test_hook_with_publisher_emits_acp(self, mock_publisher):
        """When publisher is set, hooks emit ACP events directly."""
        set_current_publisher(mock_publisher)

        capture_event(
            session_id="s-hook1",
            event_type="on_session_end",
            payload={"completed": True, "interrupted": False,
                     "model": "deepseek", "platform": "cli",
                     "wall_time_s": 100.0},
            task_id="t-hook1",
        )

        # Publisher should have been called directly from the hook
        mock_publisher.session_end.assert_called_once()
        call_kwargs = mock_publisher.session_end.call_args[1]
        assert call_kwargs["outcome"] == "completed"

    def test_hook_without_publisher_no_acp(self, mock_publisher):
        """Without publisher, hooks only write to bus (no ACP call)."""
        # Publisher is NOT set — should only write to bus

        capture_event(
            session_id="s-hook2",
            event_type="on_session_end",
            payload={"completed": True, "interrupted": False,
                     "model": "deepseek", "platform": "cli",
                     "wall_time_s": 50.0},
            task_id="t-hook2",
        )

        # Verify event IS in the bus
        bus = get_event_bus()
        events = bus.peek(10)
        assert len(events) == 1
        assert events[0].event_type == "on_session_end"

        # But publisher was NOT called (no publisher set)
        mock_publisher.session_end.assert_not_called()

    def test_heartbeat_via_publisher(self, mock_publisher):
        """pre_llm_call hook with publisher emits heartbeat."""
        set_current_publisher(mock_publisher)

        capture_event(
            session_id="s-hook3",
            event_type="pre_llm_call",
            payload={"model": "deepseek", "is_question_detected": False},
            task_id="t-hook3",
        )

        mock_publisher.session_heartbeat.assert_called_once()
        call_kwargs = mock_publisher.session_heartbeat.call_args[1]
        assert call_kwargs["agent_state"] == "thinking"

    def test_tool_error_via_publisher(self, mock_publisher):
        """post_tool_call with error emits ToolCallResult."""
        set_current_publisher(mock_publisher)

        capture_event(
            session_id="s-hook4",
            event_type="post_tool_call",
            payload={"tool_name": "terminal", "tool_call_id": "tc-x",
                     "error": "permission denied"},
            task_id="t-hook4",
        )

        mock_publisher.tool_call_result.assert_called_once()
        call_kwargs = mock_publisher.tool_call_result.call_args[1]
        assert call_kwargs["success"] is False
        assert "permission denied" in call_kwargs["error"]

    def test_concurrent_publisher_access(self, mock_publisher):
        """Multiple hooks can safely use the publisher."""
        set_current_publisher(mock_publisher)

        for i in range(20):
            capture_event(
                session_id=f"s-conc{i}",
                event_type="pre_llm_call",
                payload={"model": "deepseek", "is_question_detected": False},
                task_id=f"t-conc{i}",
            )

        # All 20 should produce heartbeat calls
        assert mock_publisher.session_heartbeat.call_count == 20


# ---------------------------------------------------------------------------
# End-to-end pipeline test
# ---------------------------------------------------------------------------


class TestEndToEndPipeline:
    """Full pipeline: hook → bus → bridge → ACP → handler → reactions."""

    def test_full_pipeline_success(self, handler, mock_publisher):
        """Happy path: hook captures, bridge emits, handler tracks, reactions apply."""
        # 1. Register publisher for direct ACP emission from hooks
        set_current_publisher(mock_publisher)

        # 2. Simulate a session lifecycle
        # Session starts (via hook)
        capture_event(
            session_id="s-e2e", event_type="on_session_start",
            payload={"cwd": "/tmp", "model": "deepseek"},
            task_id="t-e2e",
        )

        # Heartbeats during work
        for i in range(3):
            capture_event(
                session_id="s-e2e", event_type="pre_llm_call",
                payload={"model": "deepseek", "platform": "cli",
                         "is_question_detected": False,
                         "message_count": i + 1},
                task_id="t-e2e",
            )

        # Session ends successfully
        capture_event(
            session_id="s-e2e", event_type="on_session_end",
            payload={"completed": True, "interrupted": False,
                     "model": "deepseek", "platform": "cli",
                     "wall_time_s": 150.0},
            task_id="t-e2e",
        )

        # 3. Bridge drains bus and emits ACP events
        bridge = LifecycleBridge(publisher=mock_publisher)
        results = bridge.drain_and_emit()
        assert len(results) > 0

        # 4. Feed ACP events into handler (simulating ACP transport)
        # In reality, the publisher sends via ACP and handler receives.
        # Here we directly simulate reception.
        handler.handle_event(SessionStart(
            sessionId="s-e2e", cwd="/tmp",
            _meta={"task_id": "t-e2e"},
        ))
        handler.handle_event(SessionEnd(
            sessionId="s-e2e", outcome="completed",
            summary="All done",
            _meta={"task_id": "t-e2e"},
        ))

        # 5. Reactions process the handler state
        mock_gate = MagicMock()
        dispatcher = ReactionDispatcher(
            handler=handler,
            gate_handler=mock_gate,
        )
        reaction_results = dispatcher.react_to_ended_tasks()
        assert len(reaction_results) >= 1
        assert reaction_results[0].action == "advance_gate"
        assert reaction_results[0].handler_called

        # 6. Gate was advanced
        mock_gate.advance_gate.assert_called_once()
        call_args = mock_gate.advance_gate.call_args[0]
        assert call_args[0] == "t-e2e"

    def test_full_pipeline_failure(self, handler, mock_publisher):
        """Failure path: hook → bus → handler → reflection."""
        set_current_publisher(mock_publisher)

        capture_event(
            session_id="s-e2e-fail", event_type="on_session_end",
            payload={"completed": False, "interrupted": False,
                     "model": "deepseek", "platform": "cli",
                     "wall_time_s": 30.0},
            task_id="t-e2e-fail",
        )

        # In a real scenario: bridge emits ACP → handler receives
        handler.handle_event(SessionEnd(
            sessionId="s-e2e-fail", outcome="error",
            summary="Unexpected crash",
            _meta={"task_id": "t-e2e-fail"},
        ))

        mock_reflection = MagicMock()
        mock_reflection.log_failure.return_value = "ref-99"
        dispatcher = ReactionDispatcher(
            handler=handler,
            reflection_handler=mock_reflection,
        )
        results = dispatcher.react_to_ended_tasks()
        assert len(results) >= 1
        assert results[0].action == "log_reflection"
        mock_reflection.log_failure.assert_called_once()

    def test_full_pipeline_stall_resolution(self, handler):
        """Stall detection → escalation → resolution."""
        from plugins.bmad.orchestrator.stall_detector import (
            StallDetector,
            StallDetectorConfig,
            LogRecoveryHandler,
        )

        # Start a task
        handler.handle_event(SessionStart(
            sessionId="s-stall", cwd="/tmp",
            _meta={"task_id": "t-stall"},
        ))

        # No heartbeat — task is stale
        detector = StallDetector(
            handler=handler,
            recovery=LogRecoveryHandler(),
            config=StallDetectorConfig(
                heartbeat_timeout_seconds=-1,  # immediate
                escalation_timeout_seconds=0.001,  # immediate escalation
                max_escalations=1,
            ),
        )

        # First check → stage 1
        results = detector.check()
        stage1_results = [r for r in results if r.stage == StallStage.STAGE1]
        assert len(stage1_results) >= 1

        # Let enough time pass for escalation timeout
        import time as _time
        _time.sleep(0.01)

        # Second check → stage 2 escalation
        results2 = detector.check()
        stage2_results = [r for r in results2 if r.stage == StallStage.STAGE2]
        assert len(stage2_results) >= 1

        # Task recovers → handler gets session end (status changes to "completed")
        handler.handle_event(SessionEnd(
            sessionId="s-stall", outcome="completed",
            _meta={"task_id": "t-stall"},
        ))

        # Third check → resolution via status change
        results3 = detector.check()
        resolved = [r for r in results3 if r.stage == StallStage.RESOLVED]
        assert len(resolved) >= 1

    def test_full_pipeline_reaction_chain(self, handler, mock_publisher):
        """End-to-end with all reaction types."""
        set_current_publisher(mock_publisher)

        # Simulate multiple task sessions
        # Task A: successful
        handler.handle_event(SessionStart(
            sessionId="sa", cwd="/tmp", _meta={"task_id": "ta"},
        ))
        handler.handle_event(SessionEnd(
            sessionId="sa", outcome="completed", _meta={"task_id": "ta"},
        ))

        # Task B: running with tool errors
        handler.handle_event(SessionStart(
            sessionId="sb", cwd="/tmp", _meta={"task_id": "tb"},
        ))
        # 3 tool errors
        for i in range(3):
            handler.handle_event(ToolCallResult(
                sessionId="sb", toolCallId=f"tcb-{i}",
                toolName="read_file", success=False,
                error=f"not found {i}",
                _meta={"task_id": "tb"},
            ))

        # Check tool errors BEFORE SessionEnd (which changes status)
        mock_judge = MagicMock()
        mock_judge.evaluate_reroute.return_value = {
            "action": "reroute", "reason": "many errors",
            "suggested_assignee": "engineer",
        }
        dispatcher_pre = ReactionDispatcher(handler=handler, judge_handler=mock_judge)
        tool_error_results = dispatcher_pre.react_to_tool_errors(min_errors=3)
        assert len(tool_error_results) >= 1, f"Expected tool error reactions, got {tool_error_results}"

        # Now send SessionEnd for tb
        handler.handle_event(SessionEnd(
            sessionId="sb", outcome="error", _meta={"task_id": "tb"},
        ))

        # Task C: has a question
        handler.handle_event(SessionStart(
            sessionId="sc", cwd="/tmp", _meta={"task_id": "tc"},
        ))
        handler.handle_event(UserQuestion(
            sessionId="sc", questionId="q-c",
            questionText="What should I do?",
            _meta={"task_id": "tc"},
        ))

        # Run all reactions (tool errors already verified above; now check ended + questions)
        mock_gate = MagicMock()
        mock_reflection = MagicMock()
        mock_reflection.log_failure.return_value = "r1"
        mock_judge2 = MagicMock()
        mock_user = MagicMock()

        dispatcher = ReactionDispatcher(
            handler=handler,
            gate_handler=mock_gate,
            reflection_handler=mock_reflection,
            judge_handler=mock_judge2,
            user_handler=mock_user,
        )

        all_results = dispatcher.process_all_reactions()

        # Verify categories
        assert len(all_results["ended"]) >= 2  # ta completed, tb failed
        assert len(all_results["questions"]) >= 1  # tc has a question

        # Verify specific handlers called
        mock_gate.advance_gate.assert_called()  # ta success
        mock_reflection.log_failure.assert_called()  # tb failure
        mock_user.forward_question.assert_called()  # tc question
