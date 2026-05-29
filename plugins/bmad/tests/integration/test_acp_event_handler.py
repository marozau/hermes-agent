"""Integration tests for ACP orchestrator event handler.

Verifies:
    - All 6 handled event types (SessionStart, SessionHeartbeat, SessionEnd,
      ToolCallResult, UserQuestion, SessionCancelled) update state correctly
    - SessionStalled transitions task to stalled
    - get_last_heartbeat / get_stalled_tasks work for stall detection
    - Multiple tasks are tracked independently
    - Session-to-task mapping via meta.task_id
    - stats() and reset() work correctly
    - Unknown event types and missing tasks are handled gracefully
"""

from __future__ import annotations

import time
from typing import Any, Dict

import pytest

from acp_adapter.messages import (
    SessionCancelled,
    SessionEnd,
    SessionHeartbeat,
    SessionStart,
    SessionStalled,
    ToolCallResult,
    UserQuestion,
)
from plugins.bmad.orchestrator.event_handler import (
    ACPOrchestratorEventHandler,
    OrchestratorTaskState,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def handler() -> ACPOrchestratorEventHandler:
    """Fresh handler for each test."""
    h = ACPOrchestratorEventHandler(kanban_comments_enabled=False)
    yield h
    h.reset()


def _make_session_start(
    session_id: str = "sess-001",
    cwd: str = "/tmp/project",
    task_id: str = "",
    client_info: Dict[str, str] | None = None,
) -> SessionStart:
    meta: Dict[str, Any] = {}
    if task_id:
        meta["task_id"] = task_id
    return SessionStart(
        sessionId=session_id,
        cwd=cwd,
        clientInfo=client_info or {"name": "zed"},
        _meta=meta or None,
    )


def _make_session_heartbeat(
    session_id: str = "sess-001",
    agent_state: str = "working",
    current_tool: str | None = None,
    iteration: int | None = None,
    task_id: str = "",
) -> SessionHeartbeat:
    meta: Dict[str, Any] = {}
    if task_id:
        meta["task_id"] = task_id
    return SessionHeartbeat(
        sessionId=session_id,
        agentState=agent_state,
        currentTool=current_tool,
        iteration=iteration,
        _meta=meta or None,
    )


def _make_session_end(
    session_id: str = "sess-001",
    outcome: str = "completed",
    summary: str | None = None,
    task_id: str = "",
) -> SessionEnd:
    meta: Dict[str, Any] = {}
    if task_id:
        meta["task_id"] = task_id
    return SessionEnd(
        sessionId=session_id,
        outcome=outcome,
        summary=summary,
        _meta=meta or None,
    )


def _make_tool_call_result(
    session_id: str = "sess-001",
    tool_call_id: str = "tc-abc",
    tool_name: str = "terminal",
    success: bool = True,
    duration_ms: float = 100.0,
    task_id: str = "",
    error: str | None = None,
) -> ToolCallResult:
    meta: Dict[str, Any] = {}
    if task_id:
        meta["task_id"] = task_id
    return ToolCallResult(
        sessionId=session_id,
        toolCallId=tool_call_id,
        toolName=tool_name,
        success=success,
        durationMs=duration_ms,
        error=error,
        _meta=meta or None,
    )


def _make_user_question(
    session_id: str = "sess-001",
    question_id: str = "q-001",
    question_text: str = "Proceed?",
    options: list[str] | None = None,
    task_id: str = "",
) -> UserQuestion:
    meta: Dict[str, Any] = {}
    if task_id:
        meta["task_id"] = task_id
    return UserQuestion(
        sessionId=session_id,
        questionId=question_id,
        questionText=question_text,
        options=options,
        _meta=meta or None,
    )


def _make_session_cancelled(
    session_id: str = "sess-001",
    cancelled_by: str = "user",
    reason: str | None = None,
    task_id: str = "",
) -> SessionCancelled:
    meta: Dict[str, Any] = {}
    if task_id:
        meta["task_id"] = task_id
    return SessionCancelled(
        sessionId=session_id,
        cancelledBy=cancelled_by,
        reason=reason,
        _meta=meta or None,
    )


def _make_session_stalled(
    session_id: str = "sess-001",
    last_activity_age_seconds: float = 300.0,
    current_tool: str | None = None,
    task_id: str = "",
) -> SessionStalled:
    meta: Dict[str, Any] = {}
    if task_id:
        meta["task_id"] = task_id
    return SessionStalled(
        sessionId=session_id,
        lastActivityAgeSeconds=last_activity_age_seconds,
        currentTool=current_tool,
        _meta=meta or None,
    )


# ============================================================================
# SessionStart tests
# ============================================================================


class TestSessionStart:
    """SessionStart → mark task as running with start metadata."""

    def test_moves_task_to_running(self, handler):
        event = _make_session_start(task_id="t-001")
        handler.handle_event(event)

        state = handler.get_task_state("t-001")
        assert state is not None
        assert state.status == "running"
        assert state.started_at is not None
        assert state.last_heartbeat_at is not None
        assert state.cwd == "/tmp/project"

    def test_sets_client_info(self, handler):
        event = _make_session_start(
            task_id="t-002",
            client_info={"name": "vscode", "version": "1.90"},
        )
        handler.handle_event(event)

        state = handler.get_task_state("t-002")
        assert state.client_info == {"name": "vscode", "version": "1.90"}

    def test_start_counts_as_first_heartbeat(self, handler):
        event = _make_session_start(task_id="t-003")
        handler.handle_event(event)

        hb = handler.get_last_heartbeat("t-003")
        assert hb is not None
        assert hb > 0

    def test_no_task_id_uses_session_id_as_key(self, handler):
        event = _make_session_start(session_id="sess-no-task")
        handler.handle_event(event)

        state = handler.get_task_state("sess-no-task")
        assert state is not None
        assert state.status == "running"
        assert state.task_id == ""

    def test_active_count_increments(self, handler):
        assert handler.active_task_count() == 0
        handler.handle_event(_make_session_start(task_id="t-a"))
        assert handler.active_task_count() == 1
        handler.handle_event(_make_session_start(task_id="t-b"))
        assert handler.active_task_count() == 2


# ============================================================================
# SessionHeartbeat tests
# ============================================================================


class TestSessionHeartbeat:
    """SessionHeartbeat → update progress and timestamp."""

    def test_updates_heartbeat_timestamp(self, handler):
        handler.handle_event(_make_session_start(task_id="t-010"))
        before = handler.get_last_heartbeat("t-010")

        time.sleep(0.01)  # ensure timestamp difference
        handler.handle_event(
            _make_session_heartbeat(task_id="t-010", iteration=3)
        )

        after = handler.get_last_heartbeat("t-010")
        assert after is not None
        assert before is not None
        assert after > before

    def test_updates_iteration_and_current_tool(self, handler):
        handler.handle_event(_make_session_start(task_id="t-011"))
        handler.handle_event(
            _make_session_heartbeat(
                task_id="t-011",
                iteration=5,
                current_tool="read_file",
            )
        )

        state = handler.get_task_state("t-011")
        assert state.iteration == 5
        assert state.current_tool == "read_file"

    def test_stores_last_heartbeat_data(self, handler):
        handler.handle_event(_make_session_start(task_id="t-012"))
        handler.handle_event(
            _make_session_heartbeat(
                task_id="t-012",
                agent_state="thinking",
                current_tool="web_search",
                iteration=12,
            )
        )

        state = handler.get_task_state("t-012")
        assert state.last_heartbeat_data == {
            "agent_state": "thinking",
            "current_tool": "web_search",
            "iteration": 12,
        }

    def test_heartbeat_without_prior_start_creates_task(self, handler):
        handler.handle_event(
            _make_session_heartbeat(task_id="t-013", iteration=1)
        )

        state = handler.get_task_state("t-013")
        assert state is not None
        assert state.status == "pending"  # no start event, so not running
        assert state.last_heartbeat_at is not None


# ============================================================================
# SessionEnd tests
# ============================================================================


class TestSessionEnd:
    """SessionEnd → finalise task."""

    def test_completed_outcome(self, handler):
        handler.handle_event(_make_session_start(task_id="t-020"))
        handler.handle_event(
            _make_session_end(task_id="t-020", outcome="completed")
        )

        state = handler.get_task_state("t-020")
        assert state.status == "completed"
        assert state.outcome == "completed"
        assert state.ended_at is not None

    def test_error_outcome(self, handler):
        handler.handle_event(_make_session_start(task_id="t-021"))
        handler.handle_event(
            _make_session_end(
                task_id="t-021",
                outcome="error",
                summary="Connection refused",
            )
        )

        state = handler.get_task_state("t-021")
        assert state.status == "error"
        assert state.outcome == "error"

    def test_active_count_decrements(self, handler):
        handler.handle_event(_make_session_start(task_id="t-022"))
        assert handler.active_task_count() == 1
        handler.handle_event(
            _make_session_end(task_id="t-022", outcome="completed")
        )
        assert handler.active_task_count() == 0

    def test_timeout_outcome(self, handler):
        handler.handle_event(_make_session_start(task_id="t-023"))
        handler.handle_event(
            _make_session_end(task_id="t-023", outcome="timeout")
        )

        state = handler.get_task_state("t-023")
        assert state.status == "timeout"
        assert state.outcome == "timeout"


# ============================================================================
# ToolCallResult tests
# ============================================================================


class TestToolCallResult:
    """ToolCallResult → log result."""

    def test_logs_successful_tool_call(self, handler):
        handler.handle_event(_make_session_start(task_id="t-030"))
        handler.handle_event(
            _make_tool_call_result(
                task_id="t-030",
                tool_name="read_file",
                success=True,
                duration_ms=45.2,
            )
        )

        state = handler.get_task_state("t-030")
        assert len(state.tool_results) == 1
        tr = state.tool_results[0]
        assert tr["tool_name"] == "read_file"
        assert tr["success"] is True
        assert tr["duration_ms"] == 45.2

    def test_logs_failed_tool_call(self, handler):
        handler.handle_event(_make_session_start(task_id="t-031"))
        handler.handle_event(
            _make_tool_call_result(
                task_id="t-031",
                tool_name="terminal",
                success=False,
                error="command not found",
            )
        )

        state = handler.get_task_state("t-031")
        assert len(state.tool_results) == 1
        assert state.tool_results[0]["success"] is False
        assert state.tool_results[0]["error"] == "command not found"

    def test_multiple_tool_results_accumulate(self, handler):
        handler.handle_event(_make_session_start(task_id="t-032"))
        for i in range(5):
            handler.handle_event(
                _make_tool_call_result(
                    task_id="t-032",
                    tool_call_id=f"tc-{i:03d}",
                    tool_name=f"tool_{i}",
                    success=True,
                )
            )

        state = handler.get_task_state("t-032")
        assert len(state.tool_results) == 5

    def test_tool_result_without_prior_start_creates_task(self, handler):
        handler.handle_event(
            _make_tool_call_result(task_id="t-033", tool_name="read_file")
        )

        state = handler.get_task_state("t-033")
        assert state is not None
        assert len(state.tool_results) == 1


# ============================================================================
# UserQuestion tests
# ============================================================================


class TestUserQuestion:
    """UserQuestion → flag for human attention."""

    def test_logs_question(self, handler):
        handler.handle_event(_make_session_start(task_id="t-040"))
        handler.handle_event(
            _make_user_question(
                task_id="t-040",
                question_text="Should I proceed with the deletion?",
                options=["yes", "no", "abort"],
            )
        )

        state = handler.get_task_state("t-040")
        assert len(state.user_questions) == 1
        q = state.user_questions[0]
        assert q["question_text"] == "Should I proceed with the deletion?"
        assert q["options"] == ["yes", "no", "abort"]

    def test_multiple_questions_accumulate(self, handler):
        handler.handle_event(_make_session_start(task_id="t-041"))
        handler.handle_event(
            _make_user_question(task_id="t-041", question_text="Q1")
        )
        handler.handle_event(
            _make_user_question(task_id="t-041", question_text="Q2")
        )

        state = handler.get_task_state("t-041")
        assert len(state.user_questions) == 2

    def test_question_without_prior_start_creates_task(self, handler):
        handler.handle_event(
            _make_user_question(
                task_id="t-042",
                question_text="Need input",
            )
        )

        state = handler.get_task_state("t-042")
        assert state is not None
        assert len(state.user_questions) == 1


# ============================================================================
# SessionCancelled tests
# ============================================================================


class TestSessionCancelled:
    """SessionCancelled → rollback to cancelled state."""

    def test_marks_task_cancelled(self, handler):
        handler.handle_event(_make_session_start(task_id="t-050"))
        handler.handle_event(
            _make_session_cancelled(
                task_id="t-050",
                cancelled_by="user",
                reason="User interrupted",
            )
        )

        state = handler.get_task_state("t-050")
        assert state.status == "cancelled"
        assert state.outcome == "cancelled"
        assert state.ended_at is not None

    def test_cancelled_task_not_in_active_count(self, handler):
        handler.handle_event(_make_session_start(task_id="t-051"))
        assert handler.active_task_count() == 1
        handler.handle_event(
            _make_session_cancelled(task_id="t-051", cancelled_by="system")
        )
        assert handler.active_task_count() == 0

    def test_cancelled_by_timeout(self, handler):
        handler.handle_event(_make_session_start(task_id="t-052"))
        handler.handle_event(
            _make_session_cancelled(
                task_id="t-052",
                cancelled_by="timeout",
                reason="Max runtime exceeded",
            )
        )

        state = handler.get_task_state("t-052")
        assert state.status == "cancelled"


# ============================================================================
# SessionStalled tests
# ============================================================================


class TestSessionStalled:
    """SessionStalled → mark task as stalled."""

    def test_marks_task_stalled(self, handler):
        handler.handle_event(_make_session_start(task_id="t-060"))
        handler.handle_event(
            _make_session_stalled(
                task_id="t-060",
                last_activity_age_seconds=600.0,
                current_tool="terminal",
            )
        )

        state = handler.get_task_state("t-060")
        assert state.status == "stalled"

    def test_stalled_count_in_stats(self, handler):
        handler.handle_event(_make_session_start(task_id="t-061"))
        handler.handle_event(
            _make_session_stalled(task_id="t-061", last_activity_age_seconds=300)
        )

        stats = handler.stats()
        assert stats["stalled_tasks"] == 1


# ============================================================================
# Heartbeat query / stall detection tests
# ============================================================================


class TestStallDetection:
    """get_last_heartbeat and get_stalled_tasks work for detection."""

    def test_get_last_heartbeat_returns_none_for_unknown_task(self, handler):
        assert handler.get_last_heartbeat("nonexistent") is None

    def test_get_last_heartbeat_returns_timestamp(self, handler):
        handler.handle_event(_make_session_start(task_id="t-070"))
        hb = handler.get_last_heartbeat("t-070")
        assert hb is not None
        assert isinstance(hb, float)

    def test_get_stalled_tasks_empty_when_no_running_tasks(self, handler):
        stalled = handler.get_stalled_tasks(max_age_seconds=10.0)
        assert stalled == []

    def test_get_stalled_tasks_identifies_stale_heartbeat(self, handler):
        handler.handle_event(_make_session_start(task_id="t-071"))

        # Use an absurdly small threshold to force staleness
        stalled = handler.get_stalled_tasks(max_age_seconds=0.0)
        assert "t-071" in stalled

    def test_get_stalled_tasks_sorted_oldest_first(self, handler):
        handler.handle_event(_make_session_start(session_id="sess-stale-a", task_id="t-a"))
        time.sleep(0.02)
        handler.handle_event(_make_session_start(session_id="sess-stale-b", task_id="t-b"))

        # Use 0.0 threshold to force both tasks to appear stalled
        stalled = handler.get_stalled_tasks(max_age_seconds=0.0)
        # Both should be stalled; t-a started first → should appear first
        assert len(stalled) == 2
        assert stalled[0] == "t-a"
        assert stalled[1] == "t-b"

    def test_get_stalled_tasks_excludes_completed(self, handler):
        handler.handle_event(_make_session_start(task_id="t-072"))
        handler.handle_event(
            _make_session_end(task_id="t-072", outcome="completed")
        )

        stalled = handler.get_stalled_tasks(max_age_seconds=0.0)
        assert "t-072" not in stalled

    def test_get_stalled_tasks_excludes_cancelled(self, handler):
        handler.handle_event(_make_session_start(task_id="t-073"))
        handler.handle_event(
            _make_session_cancelled(task_id="t-073", cancelled_by="user")
        )

        stalled = handler.get_stalled_tasks(max_age_seconds=0.0)
        assert "t-073" not in stalled


# ============================================================================
# Multi-task tracking tests
# ============================================================================


class TestMultiTaskTracking:
    """Multiple tasks are tracked independently."""

    def test_independent_state(self, handler):
        handler.handle_event(_make_session_start(task_id="t-1", cwd="/proj/a"))
        handler.handle_event(_make_session_start(task_id="t-2", cwd="/proj/b"))
        handler.handle_event(
            _make_session_heartbeat(task_id="t-1", iteration=5)
        )
        handler.handle_event(
            _make_session_end(task_id="t-2", outcome="completed")
        )

        s1 = handler.get_task_state("t-1")
        s2 = handler.get_task_state("t-2")

        assert s1.status == "running"
        assert s1.iteration == 5
        assert s2.status == "completed"
        assert s2.cwd == "/proj/b"

    def test_session_to_task_mapping(self, handler):
        handler.handle_event(
            _make_session_start(session_id="sess-alpha", task_id="t-map")
        )

        state = handler.get_task_by_session("sess-alpha")
        assert state is not None
        assert state.task_id == "t-map"

    def test_session_to_task_nonexistent(self, handler):
        assert handler.get_task_by_session("no-such-session") is None


# ============================================================================
# Stats tests
# ============================================================================


class TestStats:
    """stats() returns accurate snapshots."""

    def test_empty_stats(self, handler):
        s = handler.stats()
        assert s["total_tasks"] == 0
        assert s["active_tasks"] == 0
        assert s["completed_tasks"] == 0
        assert s["cancelled_tasks"] == 0

    def test_counts_by_status(self, handler):
        # Running
        handler.handle_event(_make_session_start(task_id="t-r1"))
        handler.handle_event(_make_session_start(task_id="t-r2"))
        # Completed
        handler.handle_event(_make_session_start(task_id="t-c1"))
        handler.handle_event(
            _make_session_end(task_id="t-c1", outcome="completed")
        )
        # Cancelled
        handler.handle_event(_make_session_start(task_id="t-x1"))
        handler.handle_event(
            _make_session_cancelled(task_id="t-x1", cancelled_by="user")
        )

        s = handler.stats()
        assert s["total_tasks"] == 4
        assert s["active_tasks"] == 2
        assert s["completed_tasks"] == 1
        assert s["cancelled_tasks"] == 1

    def test_event_counters(self, handler):
        handler.handle_event(_make_session_start(task_id="t-ec"))
        handler.handle_event(_make_session_heartbeat(task_id="t-ec"))
        handler.handle_event(_make_session_heartbeat(task_id="t-ec"))
        handler.handle_event(
            _make_session_end(task_id="t-ec", outcome="completed")
        )

        s = handler.stats()
        assert s["event_counters"]["session_start"] == 1
        assert s["event_counters"]["session_heartbeat"] == 2
        assert s["event_counters"]["session_end"] == 1


# ============================================================================
# Reset tests
# ============================================================================


class TestReset:
    """reset() clears all state."""

    def test_reset_clears_tasks(self, handler):
        handler.handle_event(_make_session_start(task_id="t-080"))
        handler.handle_event(_make_session_start(task_id="t-081"))

        assert handler.stats()["total_tasks"] == 2

        handler.reset()

        assert handler.stats()["total_tasks"] == 0
        assert handler.get_task_state("t-080") is None
        assert handler.get_task_by_session("sess-001") is None

    def test_reset_clears_event_counters(self, handler):
        handler.handle_event(_make_session_start(task_id="t-082"))
        handler.reset()
        assert handler.stats()["event_counters"] == {}


# ============================================================================
# Gracefulness tests
# ============================================================================


class TestGracefulness:
    """Event handler doesn't crash on edge cases."""

    def test_event_without_task_id_works(self, handler):
        """Events without meta.task_id create state keyed by session_id."""
        handler.handle_event(
            SessionStart(
                sessionId="anon-session",
                cwd="/tmp",
            )
        )
        state = handler.get_task_by_session("anon-session")
        assert state is not None
        assert state.status == "running"

    def test_event_with_none_meta(self, handler):
        """Events with _meta=None don't crash."""
        handler.handle_event(
            SessionStart(
                sessionId="sess-no-meta",
                cwd="/tmp",
                _meta=None,
            )
        )
        state = handler.get_task_by_session("sess-no-meta")
        assert state is not None
        assert state.status == "running"

    def test_kanban_disabled_no_crash(self, handler):
        """When kanban comments are disabled, no crash on comment-worthy events."""
        handler.handle_event(
            _make_session_start(task_id="t-safe", cwd="/safe")
        )
        handler.handle_event(
            _make_session_end(task_id="t-safe", outcome="completed")
        )
        # Should not raise
        assert handler.get_task_state("t-safe").status == "completed"
