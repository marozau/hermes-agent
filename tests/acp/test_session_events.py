"""Integration tests for Hermes session event messages over ACP.

Exercises the full chain: message creation → serialisation → ext_notification
→ JSON-RPC notification frame. Uses ``asyncio.run()`` to properly drive the
event loop for cross-thread publisher tests.
"""

# pyright: reportCallIssue=false

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from acp_adapter.messages import (
    SESSION_EVENT_METHOD,
    SessionStart,
    deserialize_session_event,
)
from acp_adapter.publisher import SessionEventPublisher


# -- Mock ACP Client ----------------------------------------------------------


class MockACPClient:
    """Mock acp.Client that captures ext_notification calls."""

    def __init__(self):
        self.notifications: list[tuple[str, dict]] = []

    async def ext_notification(self, method: str, params: dict) -> None:
        self.notifications.append((method, params))

    async def session_update(self, session_id: str, update) -> None:
        pass


# -- Integration tests using asyncio.run() -----------------------------------


class TestSessionEventOverExtNotification:
    """Test the publisher sends events via conn.ext_notification."""

    def test_session_start_sent_via_ext_notification(self):
        """Emit a SessionStart from a worker thread and verify
        ext_notification receives the correct payload."""
        async def _test():
            client = MockACPClient()
            publisher = SessionEventPublisher(
                session_id="sess-integ-001",
                send_ext_notification=client.ext_notification,
                loop=asyncio.get_running_loop(),
            )

            def _emit():
                publisher.session_start(
                    cwd="/home/user/project",
                    client_info={"name": "zed", "version": "2.0"},
                )

            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(_emit)
                # Wait for the notification to arrive
                for _ in range(50):
                    await asyncio.sleep(0.01)
                    if client.notifications:
                        break

            assert len(client.notifications) == 1, f"Got {client.notifications}"
            method, payload = client.notifications[0]
            assert method == SESSION_EVENT_METHOD
            assert payload["eventType"] == "session_start"
            assert payload["sessionId"] == "sess-integ-001"
            assert payload["cwd"] == "/home/user/project"
            assert payload["clientInfo"] == {"name": "zed", "version": "2.0"}

        asyncio.run(_test())

    def test_multiple_events_in_sequence(self):
        """Emit multiple events from a worker thread and verify all arrive."""
        async def _test():
            client = MockACPClient()
            publisher = SessionEventPublisher(
                session_id="sess-multi",
                send_ext_notification=client.ext_notification,
                loop=asyncio.get_running_loop(),
            )

            def _emit():
                publisher.session_start(cwd="/tmp")
                publisher.session_heartbeat(agent_state="running", iteration=1)
                publisher.tool_call_result(
                    tool_call_id="tc-001",
                    tool_name="terminal",
                    success=True,
                    duration_ms=50.0,
                )
                publisher.session_end(outcome="completed", summary="Done")

            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(_emit)
                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if len(client.notifications) >= 4:
                        break

            assert len(client.notifications) == 4
            event_types = [p["eventType"] for _, p in client.notifications]
            assert event_types == [
                "session_start",
                "session_heartbeat",
                "tool_call_result",
                "session_end",
            ]

        asyncio.run(_test())

    def test_deserializable_after_roundtrip(self):
        """Emit an event from a thread and verify it deserializes."""
        async def _test():
            client = MockACPClient()
            publisher = SessionEventPublisher(
                session_id="sess-rt",
                send_ext_notification=client.ext_notification,
                loop=asyncio.get_running_loop(),
            )

            def _emit():
                publisher.session_start(
                    cwd="/app", client_info={"name": "vscode"}
                )

            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(_emit)
                for _ in range(50):
                    await asyncio.sleep(0.01)
                    if client.notifications:
                        break

            _, payload = client.notifications[0]
            restored = deserialize_session_event(payload)
            assert isinstance(restored, SessionStart)
            assert restored.session_id == "sess-rt"
            assert restored.cwd == "/app"
            assert restored.client_info == {"name": "vscode"}

        asyncio.run(_test())


# -- JSON-RPC framing tests ---------------------------------------------------


class TestSessionEventJSONRPCFraming:
    """Verify the payload structure and JSON-RPC notification format."""

    def test_payload_is_valid_json_serializable(self):
        """The ext_notification params should be valid JSON-serializable."""
        async def _test():
            client = MockACPClient()
            publisher = SessionEventPublisher(
                session_id="sess-rpc-001",
                send_ext_notification=client.ext_notification,
                loop=asyncio.get_running_loop(),
            )

            def _emit():
                publisher.session_start(cwd="/code")

            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(_emit)
                for _ in range(50):
                    await asyncio.sleep(0.01)
                    if client.notifications:
                        break

            _, payload = client.notifications[0]
            json_str = json.dumps(payload)
            assert json_str
            roundtripped = json.loads(json_str)
            assert roundtripped == payload
            assert roundtripped["eventType"] == "session_start"

        asyncio.run(_test())

    def test_mock_tcp_wire_format(self):
        """Verify JSON-RPC notification frame structure for SessionStart."""
        payload = {
            "eventType": "session_start",
            "sessionId": "sess-tcp-001",
            "cwd": "/home/project",
            "eventId": "evt-test-123",
            "timestamp": "2026-01-01T00:00:00Z",
        }
        frame = json.dumps({
            "jsonrpc": "2.0",
            "method": f"_{SESSION_EVENT_METHOD}",
            "params": payload,
        }) + "\n"

        decoded = json.loads(frame.strip())
        assert decoded["jsonrpc"] == "2.0"
        assert decoded["method"] == f"_{SESSION_EVENT_METHOD}"
        assert decoded["params"]["eventType"] == "session_start"
        assert decoded["params"]["sessionId"] == "sess-tcp-001"
        assert decoded["params"]["cwd"] == "/home/project"

    def test_multiple_events_frame_structure(self):
        """Verify each event type serializes to a well-formed JSON-RPC frame."""
        events = [
            {"eventType": "session_start", "sessionId": "sess-1", "cwd": "/p"},
            {"eventType": "session_heartbeat", "sessionId": "sess-1",
             "agentState": "running"},
            {"eventType": "session_end", "sessionId": "sess-1",
             "outcome": "completed"},
        ]
        received = []
        for evt in events:
            frame = json.dumps({
                "jsonrpc": "2.0",
                "method": f"_{SESSION_EVENT_METHOD}",
                "params": evt,
            }) + "\n"
            received.append(json.loads(frame.strip()))

        assert len(received) == 3
        event_types = [r["params"]["eventType"] for r in received]
        assert event_types == ["session_start", "session_heartbeat", "session_end"]


# -- Callback factory integration tests ---------------------------------------


class TestCallbackFactoryIntegration:
    """Test that the callback factories produce callables that invoke
    the publisher correctly."""

    def test_session_start_callback(self):
        """make_session_start_cb should emit a SessionStart event."""
        from acp_adapter.events import make_session_start_cb

        async def _test():
            client = MockACPClient()
            cb = make_session_start_cb(
                conn=client,
                session_id="sess-cb-001",
                loop=asyncio.get_running_loop(),
                cwd="/workspace",
            )

            def _emit():
                cb(client_info={"name": "test-editor"})

            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(_emit)
                for _ in range(50):
                    await asyncio.sleep(0.01)
                    if client.notifications:
                        break

            assert len(client.notifications) == 1
            _, payload = client.notifications[0]
            assert payload["eventType"] == "session_start"
            assert payload["cwd"] == "/workspace"
            assert payload["clientInfo"] == {"name": "test-editor"}

        asyncio.run(_test())

    def test_session_end_callback(self):
        """make_session_end_cb should emit a SessionEnd event."""
        from acp_adapter.events import make_session_end_cb

        async def _test():
            client = MockACPClient()
            cb = make_session_end_cb(
                conn=client,
                session_id="sess-cb-002",
                loop=asyncio.get_running_loop(),
            )

            def _emit():
                cb("completed", summary="All done", reason={"exit_code": 0})

            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(_emit)
                for _ in range(50):
                    await asyncio.sleep(0.01)
                    if client.notifications:
                        break

            assert len(client.notifications) == 1
            _, payload = client.notifications[0]
            assert payload["eventType"] == "session_end"
            assert payload["outcome"] == "completed"
            assert payload["summary"] == "All done"

        asyncio.run(_test())

    def test_tool_result_callback(self):
        """make_tool_result_cb should emit a ToolCallResult event."""
        from acp_adapter.events import make_tool_result_cb

        async def _test():
            client = MockACPClient()
            cb = make_tool_result_cb(
                conn=client,
                session_id="sess-cb-003",
                loop=asyncio.get_running_loop(),
            )

            def _emit():
                cb(
                    tool_call_id="tc-xyz",
                    tool_name="read_file",
                    success=True,
                    duration_ms=12.5,
                )

            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(_emit)
                for _ in range(50):
                    await asyncio.sleep(0.01)
                    if client.notifications:
                        break

            assert len(client.notifications) == 1
            _, payload = client.notifications[0]
            assert payload["eventType"] == "tool_call_result"
            assert payload["toolCallId"] == "tc-xyz"
            assert payload["toolName"] == "read_file"
            assert payload["success"] is True
            assert payload["durationMs"] == 12.5

        asyncio.run(_test())

    def test_user_question_callback(self):
        """make_user_question_cb should emit a UserQuestion event."""
        from acp_adapter.events import make_user_question_cb

        async def _test():
            client = MockACPClient()
            cb = make_user_question_cb(
                conn=client,
                session_id="sess-cb-004",
                loop=asyncio.get_running_loop(),
            )

            def _emit():
                cb(
                    question_id="q-001",
                    question_text="Overwrite file?",
                    options=["yes", "no", "diff"],
                )

            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(_emit)
                for _ in range(50):
                    await asyncio.sleep(0.01)
                    if client.notifications:
                        break

            assert len(client.notifications) == 1
            _, payload = client.notifications[0]
            assert payload["eventType"] == "user_question"
            assert payload["questionId"] == "q-001"
            assert payload["options"] == ["yes", "no", "diff"]

        asyncio.run(_test())

    def test_session_stalled_callback(self):
        """make_session_stalled_cb should emit a SessionStalled event."""
        from acp_adapter.events import make_session_stalled_cb

        async def _test():
            client = MockACPClient()
            cb = make_session_stalled_cb(
                conn=client,
                session_id="sess-cb-005",
                loop=asyncio.get_running_loop(),
            )

            def _emit():
                cb(
                    last_activity_age_seconds=180.0,
                    current_tool="terminal",
                    diagnostic={"pid": 12345},
                )

            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(_emit)
                for _ in range(50):
                    await asyncio.sleep(0.01)
                    if client.notifications:
                        break

            assert len(client.notifications) == 1
            _, payload = client.notifications[0]
            assert payload["eventType"] == "session_stalled"
            assert payload["lastActivityAgeSeconds"] == 180.0
            assert payload["currentTool"] == "terminal"

        asyncio.run(_test())

    def test_session_cancelled_callback(self):
        """make_session_cancelled_cb should emit a SessionCancelled event."""
        from acp_adapter.events import make_session_cancelled_cb

        async def _test():
            client = MockACPClient()
            cb = make_session_cancelled_cb(
                conn=client,
                session_id="sess-cb-006",
                loop=asyncio.get_running_loop(),
            )

            def _emit():
                cb(cancelled_by="user", reason="Pressed Ctrl+C")

            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(_emit)
                for _ in range(50):
                    await asyncio.sleep(0.01)
                    if client.notifications:
                        break

            assert len(client.notifications) == 1
            _, payload = client.notifications[0]
            assert payload["eventType"] == "session_cancelled"
            assert payload["cancelledBy"] == "user"
            assert payload["reason"] == "Pressed Ctrl+C"

        asyncio.run(_test())
