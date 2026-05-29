"""Session event publisher — plugin-facing API for emitting Hermes session events.

Plugins and the agent runtime use ``SessionEventPublisher`` to send structured
session lifecycle events (start, heartbeat, end, tool results, questions,
stalls, cancellations) to connected ACP editors via ``ext_notification``.

Thread safety: publisher methods can be called from any thread. The publisher
schedules the async send on the configured event loop using
``asyncio.run_coroutine_threadsafe``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

from acp_adapter.messages import (
    SESSION_EVENT_METHOD,
    SessionCancelled,
    SessionEnd,
    SessionHeartbeat,
    SessionStalled,
    SessionStart,
    ToolCallResult,
    UserQuestion,
)

logger = logging.getLogger(__name__)


def _build_payload(event_type: str, session_id: str, **kwargs: Any) -> Dict[str, Any]:
    """Build a session event payload dict from kwargs.

    The dict is passed directly to ext_notification. We avoid Pydantic
    constructor arg mismatches by building the dict with the expected
    camelCase field names.
    """
    payload: Dict[str, Any] = {
        "eventType": event_type,
        "sessionId": session_id,
    }
    payload.update(kwargs)
    return payload


# ---------------------------------------------------------------------------
# Publisher class
# ---------------------------------------------------------------------------


class SessionEventPublisher:
    """Thread-safe publisher for Hermes session events over ACP.

    Usage from a plugin hook::

        publisher = session_state.event_publisher
        publisher.tool_call_result(
            tool_call_id="tc-abc",
            tool_name="terminal",
            success=True,
            duration_ms=123.4,
        )

    The publisher is designed to be held by ``SessionState`` and passed to
    callback factories. Plugin hooks receive the publisher and call methods
    matching the event they want to emit.
    """

    def __init__(
        self,
        session_id: str,
        send_ext_notification: Callable[..., Any],
        loop: asyncio.AbstractEventLoop,
    ):
        """Initialize the publisher.

        Args:
            session_id: The ACP session id to emit events for.
            send_ext_notification: A callable that sends an ext_notification
                over the ACP connection. Signature:
                ``(method: str, params: dict) -> None``.
                This is typically the ``conn.ext_notification`` bound method
                of the ``acp.Client`` protocol.
            loop: The asyncio event loop the ACP connection runs on.
        """
        self._session_id = session_id
        self._send = send_ext_notification
        self._loop = loop

    # -- send helper ---------------------------------------------------------

    def _emit(self, payload: Dict[str, Any]) -> None:
        """Send a payload via ACP ext_notification.

        Schedules the async send on the event loop. Fire-and-forget: errors
        are logged but never raised to the caller.
        """

        async def _send() -> None:
            try:
                await self._send(SESSION_EVENT_METHOD, payload)
            except Exception:
                logger.debug(
                    "Failed to send session event %s",
                    payload.get("eventType"),
                    exc_info=True,
                )

        try:
            asyncio.run_coroutine_threadsafe(_send(), self._loop)
        except Exception:
            logger.debug(
                "Failed to schedule session event %s",
                payload.get("eventType"),
                exc_info=True,
            )

    # -- public event methods ------------------------------------------------

    def session_start(
        self,
        cwd: str,
        client_info: Optional[Dict[str, str]] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Emit a SessionStart event."""
        payload = _build_payload(
            "session_start",
            self._session_id,
            cwd=cwd,
        )
        if client_info is not None:
            payload["clientInfo"] = client_info
        if meta is not None:
            payload["_meta"] = meta
        self._emit(payload)

    def session_heartbeat(
        self,
        agent_state: Optional[str] = None,
        current_tool: Optional[str] = None,
        iteration: Optional[int] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Emit a SessionHeartbeat event."""
        payload = _build_payload("session_heartbeat", self._session_id)
        if agent_state is not None:
            payload["agentState"] = agent_state
        if current_tool is not None:
            payload["currentTool"] = current_tool
        if iteration is not None:
            payload["iteration"] = iteration
        if meta is not None:
            payload["_meta"] = meta
        self._emit(payload)

    def session_end(
        self,
        outcome: str,
        summary: Optional[str] = None,
        reason: Optional[Dict[str, Any]] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Emit a SessionEnd event.

        Args:
            outcome: One of "completed", "error", "cancelled", "timeout".
            summary: Human-readable summary.
            reason: Structured reason info.
        """
        payload = _build_payload(
            "session_end",
            self._session_id,
            outcome=outcome,
        )
        if summary is not None:
            payload["summary"] = summary
        if reason is not None:
            payload["reason"] = reason
        if meta is not None:
            payload["_meta"] = meta
        self._emit(payload)

    def tool_call_result(
        self,
        tool_call_id: str,
        tool_name: str,
        success: bool,
        duration_ms: Optional[float] = None,
        error: Optional[str] = None,
        result_summary: Optional[Dict[str, Any]] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Emit a ToolCallResult event."""
        payload = _build_payload(
            "tool_call_result",
            self._session_id,
            toolCallId=tool_call_id,
            toolName=tool_name,
            success=success,
        )
        if duration_ms is not None:
            payload["durationMs"] = duration_ms
        if error is not None:
            payload["error"] = error
        if result_summary is not None:
            payload["resultSummary"] = result_summary
        if meta is not None:
            payload["_meta"] = meta
        self._emit(payload)

    def user_question(
        self,
        question_id: str,
        question_text: str,
        options: Optional[List[str]] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Emit a UserQuestion event."""
        payload = _build_payload(
            "user_question",
            self._session_id,
            questionId=question_id,
            questionText=question_text,
        )
        if options is not None:
            payload["options"] = options
        if meta is not None:
            payload["_meta"] = meta
        self._emit(payload)

    def session_stalled(
        self,
        last_activity_age_seconds: float,
        current_tool: Optional[str] = None,
        diagnostic: Optional[Dict[str, Any]] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Emit a SessionStalled event."""
        payload = _build_payload(
            "session_stalled",
            self._session_id,
            lastActivityAgeSeconds=last_activity_age_seconds,
        )
        if current_tool is not None:
            payload["currentTool"] = current_tool
        if diagnostic is not None:
            payload["diagnostic"] = diagnostic
        if meta is not None:
            payload["_meta"] = meta
        self._emit(payload)

    def session_cancelled(
        self,
        cancelled_by: str,
        reason: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Emit a SessionCancelled event.

        Args:
            cancelled_by: "user", "system", or "timeout".
            reason: Optional reason message.
        """
        payload = _build_payload(
            "session_cancelled",
            self._session_id,
            cancelledBy=cancelled_by,
        )
        if reason is not None:
            payload["reason"] = reason
        if meta is not None:
            payload["_meta"] = meta
        self._emit(payload)
