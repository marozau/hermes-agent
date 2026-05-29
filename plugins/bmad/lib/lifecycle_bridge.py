"""LifecycleBridge — drains LifecycleEventBus and emits ACP session events.

Connects the BMAD plugin's in-memory LifecycleEventBus to the ACP
SessionEventPublisher and optional kanban persistence.  Designed to be
called periodically (e.g., on session end, on a cron tick, or during
the agent teardown) to flush captured lifecycle events to the orchestrator.

Usage:
    # In a profile session with ACP:
    bridge = LifecycleBridge(publisher=session_state.event_publisher)
    events = bridge.drain_and_emit()

    # In a non-ACP session (CLI, Telegram):
    bridge = LifecycleBridge(kanban_board="default")
    bridge.drain_and_persist()  # persists to kanban comments

Thread safety: all bus operations go through the thread-safe LifecycleEventBus.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from plugins.bmad.lib.lifecycle_events import (
    LifecycleEvent,
    get_event_bus,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Conversion table: LifecycleEvent type → ACP event type + payload extractor
# ---------------------------------------------------------------------------


def _convert_session_end(event: LifecycleEvent) -> Optional[Dict[str, Any]]:
    """Convert on_session_end → SessionEnd."""
    payload = event.payload
    completed = payload.get("completed", False)
    interrupted = payload.get("interrupted", False)

    if interrupted:
        outcome = "cancelled"
    elif not completed:
        outcome = "error"
    else:
        outcome = "completed"

    return {
        "eventType": "session_end",
        "sessionId": event.session_id,
        "outcome": outcome,
        "summary": (
            f"Session completed" if completed
            else f"Session interrupted" if interrupted
            else f"Session ended without completion"
        ),
        "reason": {
            "model": payload.get("model", ""),
            "platform": payload.get("platform", ""),
            "wall_time_s": payload.get("wall_time_s", 0),
        },
        "_meta": {"task_id": event.task_id},
    }


def _convert_pre_llm_call(event: LifecycleEvent) -> Optional[Dict[str, Any]]:
    """Convert pre_llm_call → heartbeat (always) + optional UserQuestion."""
    payload = event.payload
    is_question = payload.get("is_question_detected", False)

    # Always emit a heartbeat
    heartbeat = {
        "eventType": "session_heartbeat",
        "sessionId": event.session_id,
        "agentState": "thinking",
        "currentTool": None,
        "iteration": None,
        "_meta": {"task_id": event.task_id},
    }

    # If question detected, also flag for UserQuestion event
    # (the actual question text comes from the next user_question tool call)
    if is_question:
        heartbeat["_meta"]["question_detected"] = True
        heartbeat["_meta"]["user_message_excerpt"] = payload.get(
            "user_message_excerpt", ""
        )

    return heartbeat


def _convert_post_llm_call(event: LifecycleEvent) -> Optional[Dict[str, Any]]:
    """Convert post_llm_call → heartbeat with progress data."""
    payload = event.payload
    return {
        "eventType": "session_heartbeat",
        "sessionId": event.session_id,
        "agentState": "responding",
        "currentTool": "llm_response",
        "iteration": None,
        "_meta": {
            "task_id": event.task_id,
            "response_length": payload.get("assistant_response_length", 0),
            "tool_call_count": payload.get("tool_call_count_this_turn", 0),
            "model": payload.get("model", ""),
        },
    }


def _convert_post_tool_call(event: LifecycleEvent) -> Optional[Dict[str, Any]]:
    """Convert post_tool_call → ToolCallResult (only on error)."""
    payload = event.payload
    error = payload.get("error")
    if not error:
        # Successful tool calls are too noisy for ACP — heartbeat already covers
        return None

    return {
        "eventType": "tool_call_result",
        "sessionId": event.session_id,
        "toolCallId": payload.get("tool_call_id", f"unknown-{event.event_id[:8]}"),
        "toolName": payload.get("tool_name", "unknown"),
        "success": False,
        "durationMs": payload.get("duration_ms"),
        "error": str(error)[:500],
        "_meta": {"task_id": event.task_id},
    }


def _convert_pre_tool_call(event: LifecycleEvent) -> Optional[Dict[str, Any]]:
    """Convert pre_tool_call → heartbeat with current tool info."""
    payload = event.payload
    return {
        "eventType": "session_heartbeat",
        "sessionId": event.session_id,
        "agentState": "executing",
        "currentTool": payload.get("tool_name", "unknown"),
        "iteration": payload.get("api_call_count"),
        "_meta": {"task_id": event.task_id},
    }


def _convert_default(event: LifecycleEvent) -> Optional[Dict[str, Any]]:
    """Fallback conversion — emit as generic heartbeat."""
    return {
        "eventType": "session_heartbeat",
        "sessionId": event.session_id,
        "agentState": event.event_type,
        "_meta": {
            "task_id": event.task_id,
            "original_event_type": event.event_type,
        },
    }


# Map of LifecycleEvent event_type → converter function
_CONVERTERS: Dict[str, Any] = {
    "on_session_end": _convert_session_end,
    "pre_llm_call": _convert_pre_llm_call,
    "post_llm_call": _convert_post_llm_call,
    "post_tool_call": _convert_post_tool_call,
    "pre_tool_call": _convert_pre_tool_call,
    # on_session_start, on_session_reset: handled by _convert_default
}


# ---------------------------------------------------------------------------
# LifecycleBridge
# ---------------------------------------------------------------------------


class LifecycleBridge:
    """Bridges LifecycleEventBus → ACP publisher + kanban persistence.

    Drains events from the process-global LifecycleEventBus, converts them
    to ACP-compatible payloads, and emits them via the publisher and/or
    persists them as kanban comments.

    Parameters:
        publisher: Optional SessionEventPublisher for ACP emission.
        kanban_comments_enabled: If True, persist events as kanban comments.
        kanban_board: Kanban board name for comment persistence.

    Publisher is optional — when absent, events can still be drained
    and returned (e.g., for kanban-only persistence or inspection).
    """

    def __init__(
        self,
        publisher: Optional[Any] = None,
        kanban_comments_enabled: bool = False,
        kanban_board: str = "default",
    ):
        self._publisher = publisher
        self._kanban_comments = kanban_comments_enabled
        self._kanban_board = kanban_board
        self._drain_counters: Dict[str, int] = {}

    # -- Public API ----------------------------------------------------------

    def drain_and_emit(self) -> List[Dict[str, Any]]:
        """Drain all events, convert, emit via ACP, and return summaries.

        Returns:
            List of dicts, one per emitted event, with keys:
            ``event_type``, ``session_id``, ``task_id``, ``emitted``.
        """
        bus = get_event_bus()
        raw_events = bus.drain_all()
        results: List[Dict[str, Any]] = []

        for event in raw_events:
            converted = self._convert(event)
            if converted is None:
                continue

            event_type = converted.get("eventType", "unknown")
            self._drain_counters[event_type] = (
                self._drain_counters.get(event_type, 0) + 1
            )

            self._emit_acp(converted)
            self._maybe_persist(event.task_id or "", converted)

            results.append({
                "event_type": event_type,
                "session_id": event.session_id,
                "task_id": event.task_id,
                "emitted": self._publisher is not None,
            })

        return results

    def drain_and_persist(self) -> List[Dict[str, Any]]:
        """Drain events, persist to kanban, return summaries.

        For non-ACP sessions (CLI, Telegram) where ACP publisher is absent.
        """
        return self.drain_and_emit()  # Emit is no-op without publisher

    def stats(self) -> Dict[str, Any]:
        """Return bridge statistics."""
        bus = get_event_bus()
        return {
            "bus_stats": bus.stats(),
            "drain_counters": dict(self._drain_counters),
            "publisher_available": self._publisher is not None,
            "kanban_comments": self._kanban_comments,
        }

    # -- Internal ------------------------------------------------------------

    def _convert(self, event: LifecycleEvent) -> Optional[Dict[str, Any]]:
        """Convert a LifecycleEvent to an ACP-compatible payload dict.

        Returns None if the event should be skipped (e.g., successful tool
        call with no error).
        """
        converter = _CONVERTERS.get(event.event_type, _convert_default)
        try:
            return converter(event)
        except Exception:
            logger.debug(
                "Failed to convert event %s (type=%s)",
                event.event_id,
                event.event_type,
                exc_info=True,
            )
            return None

    def _emit_acp(self, payload: Dict[str, Any]) -> None:
        """Emit a converted payload via the ACP publisher.

        Fire-and-forget: errors are logged but never raised.  The publisher
        itself is thread-safe and handles scheduling on the event loop.
        """
        if self._publisher is None:
            return

        event_type = payload.get("eventType", "unknown")
        session_id = payload.get("sessionId", "")

        try:
            # Dispatch to the publisher method matching the event type
            if event_type == "session_end":
                self._publisher.session_end(
                    outcome=payload.get("outcome", "error"),
                    summary=payload.get("summary"),
                    reason=payload.get("reason"),
                    meta=payload.get("_meta"),
                )
            elif event_type == "session_heartbeat":
                self._publisher.session_heartbeat(
                    agent_state=payload.get("agentState"),
                    current_tool=payload.get("currentTool"),
                    iteration=payload.get("iteration"),
                    meta=payload.get("_meta"),
                )
            elif event_type == "tool_call_result":
                self._publisher.tool_call_result(
                    tool_call_id=payload.get("toolCallId", ""),
                    tool_name=payload.get("toolName", "unknown"),
                    success=payload.get("success", True),
                    duration_ms=payload.get("durationMs"),
                    error=payload.get("error"),
                    result_summary=payload.get("resultSummary"),
                    meta=payload.get("_meta"),
                )
            elif event_type == "session_cancelled":
                self._publisher.session_cancelled(
                    cancelled_by=payload.get("cancelledBy", "system"),
                    reason=payload.get("reason"),
                    meta=payload.get("_meta"),
                )
            elif event_type == "session_stalled":
                self._publisher.session_stalled(
                    last_activity_age_seconds=payload.get(
                        "lastActivityAgeSeconds", 0.0
                    ),
                    current_tool=payload.get("currentTool"),
                    diagnostic=payload.get("diagnostic"),
                    meta=payload.get("_meta"),
                )
            elif event_type == "user_question":
                self._publisher.user_question(
                    question_id=payload.get("questionId", ""),
                    question_text=payload.get("questionText", ""),
                    options=payload.get("options"),
                    meta=payload.get("_meta"),
                )
            # session_start is handled by the ACP adapter entry point,
            # not typically via the bridge
            else:
                logger.debug("Unknown event type for ACP emit: %s", event_type)

        except Exception:
            logger.debug(
                "Failed to emit ACP event %s for session %s",
                event_type,
                session_id,
                exc_info=True,
            )

    def _maybe_persist(self, task_id: str, payload: Dict[str, Any]) -> None:
        """Persist event as a kanban comment if enabled and task_id is set."""
        if not self._kanban_comments or not task_id:
            return

        event_type = payload.get("eventType", "unknown")
        summary = payload.get("summary", "")
        outcome = payload.get("outcome", "")
        agent_state = payload.get("agentState", "")
        tool_name = payload.get("toolName", "")
        error = payload.get("error", "")

        # Build a compact one-line comment body
        if event_type == "session_end":
            body = (
                f"✅ **Session ended** — outcome: `{outcome}`"
                + (f"\\n> {summary}" if summary else "")
            )
        elif event_type == "session_heartbeat":
            body = (
                f"💓 **Heartbeat** — state: `{agent_state}`"
                + (f", tool: `{tool_name}`" if tool_name else "")
            )
        elif event_type == "tool_call_result":
            body = (
                f"🔧 **Tool call** `{tool_name}` — "
                + ("✅ success" if not error else f"❌ error: {error[:200]}")
            )
        else:
            body = f"📡 **{event_type}**"

        try:
            # Deferred import — avoids hard kanban dependency at module load
            from hermes_cli.kanban_commands import _add_comment

            _add_comment(
                task_id=task_id,
                body=body,
                board=self._kanban_board,
            )
        except Exception:
            logger.debug(
                "Failed to persist kanban comment for task %s",
                task_id,
                exc_info=True,
            )
