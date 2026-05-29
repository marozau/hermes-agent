"""Tests for acp_adapter.messages — session event message types.

Covers serialisation (model_dump) and deserialisation (model_validate)
for all 7 Hermes session event message types.
"""

# pyright: reportCallIssue=false
# Pydantic models use populate_by_name=True, so snake_case kwargs work at
# runtime even though Pyright only sees the camelCase aliases.

import pytest

from acp_adapter.messages import (
    SessionCancelled,
    SessionEnd,
    SessionHeartbeat,
    SessionStalled,
    SessionStart,
    ToolCallResult,
    UserQuestion,
    deserialize_session_event,
)


# ---------------------------------------------------------------------------
# SessionStart
# ---------------------------------------------------------------------------

class TestSessionStart:
    def test_serialize_minimal(self):
        event = SessionStart(
            session_id="sess-001",
            cwd="/home/user/project",
        )
        d = event.to_json_dict()
        assert d["eventType"] == "session_start"
        assert d["sessionId"] == "sess-001"
        assert d["cwd"] == "/home/user/project"
        assert "event_id" in d or "eventId" in d
        assert "timestamp" in d

    def test_serialize_with_client_info(self):
        event = SessionStart(
            session_id="sess-002",
            cwd="/tmp",
            client_info={"name": "zed", "version": "1.0"},
        )
        d = event.to_json_dict()
        assert d["clientInfo"] == {"name": "zed", "version": "1.0"}

    def test_serialize_excludes_none_values(self):
        event = SessionStart(session_id="sess-003", cwd="/app")
        d = event.to_json_dict()
        assert "clientInfo" not in d

    def test_roundtrip(self):
        original = SessionStart(
            session_id="sess-004",
            cwd="/code",
            client_info={"name": "vscode"},
        )
        d = original.to_json_dict()
        restored = deserialize_session_event(d)
        assert isinstance(restored, SessionStart)
        assert restored.session_id == "sess-004"
        assert restored.cwd == "/code"
        assert restored.client_info == {"name": "vscode"}


# ---------------------------------------------------------------------------
# SessionHeartbeat
# ---------------------------------------------------------------------------

class TestSessionHeartbeat:
    def test_serialize_minimal(self):
        event = SessionHeartbeat(session_id="sess-001")
        d = event.to_json_dict()
        assert d["eventType"] == "session_heartbeat"
        assert d["sessionId"] == "sess-001"

    def test_serialize_with_state(self):
        event = SessionHeartbeat(
            session_id="sess-002",
            agent_state="running",
            current_tool="terminal",
            iteration=5,
        )
        d = event.to_json_dict()
        assert d["agentState"] == "running"
        assert d["currentTool"] == "terminal"
        assert d["iteration"] == 5

    def test_roundtrip(self):
        original = SessionHeartbeat(
            session_id="sess-003",
            agent_state="thinking",
            iteration=12,
        )
        d = original.to_json_dict()
        restored = deserialize_session_event(d)
        assert isinstance(restored, SessionHeartbeat)
        assert restored.session_id == "sess-003"
        assert restored.agent_state == "thinking"
        assert restored.iteration == 12


# ---------------------------------------------------------------------------
# SessionEnd
# ---------------------------------------------------------------------------

class TestSessionEnd:
    def test_serialize(self):
        event = SessionEnd(
            session_id="sess-001",
            outcome="completed",
            summary="Task finished successfully",
            reason={"exit_code": 0},
        )
        d = event.to_json_dict()
        assert d["eventType"] == "session_end"
        assert d["outcome"] == "completed"
        assert d["summary"] == "Task finished successfully"
        assert d["reason"] == {"exit_code": 0}

    def test_roundtrip(self):
        original = SessionEnd(
            session_id="sess-002",
            outcome="error",
            summary="Something went wrong",
        )
        d = original.to_json_dict()
        restored = deserialize_session_event(d)
        assert isinstance(restored, SessionEnd)
        assert restored.outcome == "error"
        assert restored.summary == "Something went wrong"


# ---------------------------------------------------------------------------
# ToolCallResult
# ---------------------------------------------------------------------------

class TestToolCallResult:
    def test_serialize_success(self):
        event = ToolCallResult(
            session_id="sess-001",
            tool_call_id="tc-abc123",
            tool_name="terminal",
            success=True,
            duration_ms=150.5,
            result_summary={"exit_code": 0, "lines": 42},
        )
        d = event.to_json_dict()
        assert d["eventType"] == "tool_call_result"
        assert d["toolCallId"] == "tc-abc123"
        assert d["toolName"] == "terminal"
        assert d["success"] is True
        assert d["durationMs"] == 150.5
        assert d["resultSummary"] == {"exit_code": 0, "lines": 42}

    def test_serialize_failure(self):
        event = ToolCallResult(
            session_id="sess-002",
            tool_call_id="tc-def456",
            tool_name="web_search",
            success=False,
            error="Connection timeout",
            duration_ms=30000.0,
        )
        d = event.to_json_dict()
        assert d["success"] is False
        assert d["error"] == "Connection timeout"
        assert "resultSummary" not in d

    def test_roundtrip(self):
        original = ToolCallResult(
            session_id="sess-003",
            tool_call_id="tc-ghi789",
            tool_name="read_file",
            success=True,
        )
        d = original.to_json_dict()
        restored = deserialize_session_event(d)
        assert isinstance(restored, ToolCallResult)
        assert restored.tool_call_id == "tc-ghi789"
        assert restored.tool_name == "read_file"
        assert restored.success is True


# ---------------------------------------------------------------------------
# UserQuestion
# ---------------------------------------------------------------------------

class TestUserQuestion:
    def test_serialize_with_options(self):
        event = UserQuestion(
            session_id="sess-001",
            question_id="q-001",
            question_text="Which file should I edit?",
            options=["main.py", "utils.py", "config.py"],
        )
        d = event.to_json_dict()
        assert d["eventType"] == "user_question"
        assert d["questionId"] == "q-001"
        assert d["questionText"] == "Which file should I edit?"
        assert d["options"] == ["main.py", "utils.py", "config.py"]

    def test_serialize_without_options(self):
        event = UserQuestion(
            session_id="sess-002",
            question_id="q-002",
            question_text="Are you sure?",
        )
        d = event.to_json_dict()
        assert "options" not in d

    def test_roundtrip(self):
        original = UserQuestion(
            session_id="sess-003",
            question_id="q-003",
            question_text="Proceed?",
            options=["yes", "no"],
        )
        d = original.to_json_dict()
        restored = deserialize_session_event(d)
        assert isinstance(restored, UserQuestion)
        assert restored.question_id == "q-003"
        assert restored.options == ["yes", "no"]


# ---------------------------------------------------------------------------
# SessionStalled
# ---------------------------------------------------------------------------

class TestSessionStalled:
    def test_serialize(self):
        event = SessionStalled(
            session_id="sess-001",
            last_activity_age_seconds=120.0,
            current_tool="terminal",
            diagnostic={"reason": "Long-running command"},
        )
        d = event.to_json_dict()
        assert d["eventType"] == "session_stalled"
        assert d["lastActivityAgeSeconds"] == 120.0
        assert d["currentTool"] == "terminal"
        assert d["diagnostic"] == {"reason": "Long-running command"}

    def test_roundtrip(self):
        original = SessionStalled(
            session_id="sess-002",
            last_activity_age_seconds=300.0,
        )
        d = original.to_json_dict()
        restored = deserialize_session_event(d)
        assert isinstance(restored, SessionStalled)
        assert restored.last_activity_age_seconds == 300.0


# ---------------------------------------------------------------------------
# SessionCancelled
# ---------------------------------------------------------------------------

class TestSessionCancelled:
    def test_serialize_user_cancel(self):
        event = SessionCancelled(
            session_id="sess-001",
            cancelled_by="user",
            reason="User pressed stop",
        )
        d = event.to_json_dict()
        assert d["eventType"] == "session_cancelled"
        assert d["cancelledBy"] == "user"
        assert d["reason"] == "User pressed stop"

    def test_serialize_timeout(self):
        event = SessionCancelled(
            session_id="sess-002",
            cancelled_by="timeout",
        )
        d = event.to_json_dict()
        assert d["cancelledBy"] == "timeout"
        assert "reason" not in d

    def test_roundtrip(self):
        original = SessionCancelled(
            session_id="sess-003",
            cancelled_by="system",
            reason="OOM killed",
        )
        d = original.to_json_dict()
        restored = deserialize_session_event(d)
        assert isinstance(restored, SessionCancelled)
        assert restored.cancelled_by == "system"
        assert restored.reason == "OOM killed"


# ---------------------------------------------------------------------------
# deserialize_session_event edge cases
# ---------------------------------------------------------------------------

class TestDeserializeSessionEvent:
    def test_unknown_event_type_returns_none(self):
        result = deserialize_session_event({"eventType": "nonexistent", "sessionId": "x"})
        assert result is None

    def test_missing_event_type_returns_none(self):
        result = deserialize_session_event({"sessionId": "x"})
        assert result is None

    def test_empty_dict_returns_none(self):
        assert deserialize_session_event({}) is None


# ---------------------------------------------------------------------------
# All types importable from acp_adapter
# ---------------------------------------------------------------------------

class TestPublicExports:
    def test_all_types_importable(self):
        from acp_adapter import (  # noqa: F401
            SessionCancelled,
            SessionEnd,
            SessionHeartbeat,
            SessionStalled,
            SessionStart,
            ToolCallResult,
            UserQuestion,
            deserialize_session_event,
            SessionEventPublisher,
        )
