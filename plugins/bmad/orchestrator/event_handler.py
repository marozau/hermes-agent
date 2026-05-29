"""ACP orchestrator event handler — listens for ACP session events from profiles.

Receives deserialized :class:`acp_adapter.messages.HermesSessionEvent`
subtypes and updates internal orchestrator task state accordingly.
Provides a query surface for stall detection and task status.

Thread safety: all mutable state is protected by a reentrant lock.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from acp_adapter.messages import (
    HermesSessionEvent,
    SessionCancelled,
    SessionEnd,
    SessionHeartbeat,
    SessionStart,
    SessionStalled,
    ToolCallResult,
    UserQuestion,
    deserialize_session_event,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Task state model
# ---------------------------------------------------------------------------


@dataclass
class OrchestratorTaskState:
    """State tracked for a single orchestrator task across ACP sessions.

    Mutable — the handler updates fields in place under lock.
    """

    # Identity
    session_id: str
    task_id: str = ""

    # Lifecycle
    status: str = "pending"  # pending | running | completed | cancelled | stalled
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    outcome: Optional[str] = None  # completed, error, cancelled, timeout

    # Progress tracking
    last_heartbeat_at: Optional[float] = None
    last_heartbeat_data: Optional[Dict[str, Any]] = None
    iteration: Optional[int] = None
    current_tool: Optional[str] = None

    # Event logs
    tool_results: List[Dict[str, Any]] = field(default_factory=list)
    user_questions: List[Dict[str, Any]] = field(default_factory=list)

    # Metadata
    cwd: str = ""
    client_info: Optional[Dict[str, str]] = None
    meta: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "task_id": self.task_id,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "outcome": self.outcome,
            "last_heartbeat_at": self.last_heartbeat_at,
            "iteration": self.iteration,
            "current_tool": self.current_tool,
            "tool_result_count": len(self.tool_results),
            "user_question_count": len(self.user_questions),
            "cwd": self.cwd,
            "client_info": self.client_info,
        }


# ---------------------------------------------------------------------------
# Event handler
# ---------------------------------------------------------------------------


class ACPOrchestratorEventHandler:
    """Subscribes to ACP session events and maintains orchestrator task state.

    Usage::

        handler = ACPOrchestratorEventHandler(kanban_comments_enabled=True)
        raw_event = {"eventType": "session_start", "sessionId": "abc", ...}
        event = deserialize_session_event(raw_event)
        if event:
            handler.handle_event(event)

    Thread-safe — designed to receive events from asyncio callbacks
    dispatched via SessionEventPublisher.
    """

    def __init__(
        self,
        kanban_comments_enabled: bool = False,
        kanban_board: str = "default",
    ):
        self._lock = threading.RLock()
        # task_id → state (primary index)
        self._tasks: Dict[str, OrchestratorTaskState] = {}
        # session_id → task_id (secondary index)
        self._session_to_task: Dict[str, str] = {}

        self._kanban_comments = kanban_comments_enabled
        self._kanban_board = kanban_board
        self._event_counters: Dict[str, int] = {}

    # -- Public API ----------------------------------------------------------

    def handle_event(self, event: HermesSessionEvent) -> None:
        """Dispatch a deserialized ACP session event.

        Args:
            event: A typed HermesSessionEvent from deserialize_session_event.
        """
        event_type = event.event_type
        with self._lock:
            self._event_counters[event_type] = (
                self._event_counters.get(event_type, 0) + 1
            )

        try:
            if isinstance(event, SessionStart):
                self._handle_session_start(event)
            elif isinstance(event, SessionHeartbeat):
                self._handle_session_heartbeat(event)
            elif isinstance(event, SessionEnd):
                self._handle_session_end(event)
            elif isinstance(event, ToolCallResult):
                self._handle_tool_call_result(event)
            elif isinstance(event, UserQuestion):
                self._handle_user_question(event)
            elif isinstance(event, SessionStalled):
                self._handle_session_stalled(event)
            elif isinstance(event, SessionCancelled):
                self._handle_session_cancelled(event)
            else:
                logger.debug("Unknown event type: %s", event_type)
        except Exception:
            logger.exception(
                "Error handling ACP event %s for session %s",
                event_type,
                getattr(event, "session_id", "?"),
            )

    def get_last_heartbeat(self, task_id: str) -> Optional[float]:
        """Return the Unix timestamp of the last heartbeat for a task.

        Returns:
            float if the task has received a heartbeat, None otherwise
            (task not found or no heartbeat yet).
        """
        with self._lock:
            state = self._tasks.get(task_id)
            return state.last_heartbeat_at if state else None

    def get_stalled_tasks(self, max_age_seconds: float) -> List[str]:
        """Return task_ids whose last heartbeat is older than max_age_seconds.

        Only considers tasks in 'running' status. Tasks with no heartbeat
        but in 'running' status are treated as stalled.

        Args:
            max_age_seconds: Age threshold in seconds.

        Returns:
            List of stalled task_ids, oldest first.
        """
        now = time.time()
        stalled: List[tuple[float, str]] = []
        with self._lock:
            for task_id, state in self._tasks.items():
                if state.status != "running":
                    continue
                age = now - (state.last_heartbeat_at or (state.started_at or now))
                if age > max_age_seconds:
                    stalled.append((age, task_id))
        stalled.sort(reverse=True)  # oldest first
        return [task_id for _, task_id in stalled]

    def get_task_state(self, task_id: str) -> Optional[OrchestratorTaskState]:
        """Return the current tracked state for a task, or None."""
        with self._lock:
            return self._tasks.get(task_id)

    def get_task_by_session(self, session_id: str) -> Optional[OrchestratorTaskState]:
        """Return the task state for a session, or None."""
        with self._lock:
            task_id = self._session_to_task.get(session_id)
            if task_id:
                return self._tasks.get(task_id)
        return None

    def active_task_count(self) -> int:
        """Return number of tasks currently in 'running' status."""
        with self._lock:
            return sum(
                1 for s in self._tasks.values() if s.status == "running"
            )

    def stats(self) -> Dict[str, Any]:
        """Return a snapshot of handler statistics."""
        with self._lock:
            return {
                "total_tasks": len(self._tasks),
                "active_tasks": self.active_task_count(),
                "completed_tasks": sum(
                    1 for s in self._tasks.values() if s.status == "completed"
                ),
                "cancelled_tasks": sum(
                    1 for s in self._tasks.values() if s.status == "cancelled"
                ),
                "stalled_tasks": sum(
                    1 for s in self._tasks.values() if s.status == "stalled"
                ),
                "session_mappings": len(self._session_to_task),
                "event_counters": dict(self._event_counters),
            }

    def reset(self) -> None:
        """Clear all tracked state — primarily for tests."""
        with self._lock:
            self._tasks.clear()
            self._session_to_task.clear()
            self._event_counters.clear()

    # -- Internal helpers ----------------------------------------------------

    def _ensure_task(self, session_id: str, task_id: str = "") -> OrchestratorTaskState:
        """Get or create task state for the given session/task.

        Returns the state under lock. If the same session arrives with a
        different task_id (session remapping), the association is updated.
        """
        with self._lock:
            # Look up by session
            existing_task_id = self._session_to_task.get(session_id)
            if existing_task_id and existing_task_id in self._tasks:
                # If task_id changed, remap the session
                if task_id and task_id != existing_task_id:
                    del self._session_to_task[session_id]
                else:
                    return self._tasks[existing_task_id]

            # Look up or create by task_id
            if task_id and task_id in self._tasks:
                state = self._tasks[task_id]
            else:
                state = OrchestratorTaskState(
                    session_id=session_id,
                    task_id=task_id,
                )
                if task_id:
                    self._tasks[task_id] = state

            self._session_to_task[session_id] = task_id or session_id
            if not task_id:
                self._tasks[session_id] = state
            return state

    # -- Event handlers ------------------------------------------------------

    def _handle_session_start(self, event: SessionStart) -> None:
        """SessionStart → mark task as started."""
        task_id = (event.meta or {}).get("task_id", "")
        state = self._ensure_task(event.session_id, task_id)
        now = time.time()
        state.status = "running"
        state.started_at = now
        state.last_heartbeat_at = now  # start counts as first heartbeat
        state.cwd = event.cwd
        state.client_info = event.client_info
        state.meta = event.meta or {}
        if task_id:
            state.task_id = task_id
        logger.info(
            "[orchestrator] session_start session=%s task=%s cwd=%s",
            event.session_id,
            state.task_id,
            event.cwd,
        )
        self._maybe_kanban_comment(
            state.task_id,
            f"🔵 **Session started** — cwd: `{event.cwd}`"
            + (f", client: {event.client_info.get('name', '?')}"
               if event.client_info else ""),
        )

    def _handle_session_heartbeat(self, event: SessionHeartbeat) -> None:
        """SessionHeartbeat → update progress/timestamp."""
        task_id = (event.meta or {}).get("task_id", "")
        state = self._ensure_task(event.session_id, task_id)
        now = time.time()
        state.last_heartbeat_at = now
        state.last_heartbeat_data = {
            "agent_state": event.agent_state,
            "current_tool": event.current_tool,
            "iteration": event.iteration,
        }
        if event.iteration is not None:
            state.iteration = event.iteration
        if event.current_tool is not None:
            state.current_tool = event.current_tool

    def _handle_session_end(self, event: SessionEnd) -> None:
        """SessionEnd → finalise task."""
        task_id = (event.meta or {}).get("task_id", "")
        state = self._ensure_task(event.session_id, task_id)
        state.status = "completed" if event.outcome == "completed" else event.outcome
        state.outcome = event.outcome
        state.ended_at = time.time()
        logger.info(
            "[orchestrator] session_end session=%s task=%s outcome=%s",
            event.session_id,
            state.task_id,
            event.outcome,
        )
        self._maybe_kanban_comment(
            state.task_id,
            f"✅ **Session ended** — outcome: `{event.outcome}`"
            + (f"\n> {event.summary}" if event.summary else ""),
        )

    def _handle_tool_call_result(self, event: ToolCallResult) -> None:
        """ToolCallResult → log result."""
        task_id = (event.meta or {}).get("task_id", "")
        state = self._ensure_task(event.session_id, task_id)
        entry = {
            "tool_call_id": event.tool_call_id,
            "tool_name": event.tool_name,
            "success": event.success,
            "duration_ms": event.duration_ms,
            "error": event.error,
            "result_summary": event.result_summary,
            "timestamp": time.time(),
        }
        state.tool_results.append(entry)
        if not event.success:
            logger.warning(
                "[orchestrator] tool_failure session=%s task=%s tool=%s error=%s",
                event.session_id,
                state.task_id,
                event.tool_name,
                event.error,
            )

    def _handle_user_question(self, event: UserQuestion) -> None:
        """UserQuestion → flag for human attention."""
        task_id = (event.meta or {}).get("task_id", "")
        state = self._ensure_task(event.session_id, task_id)
        entry = {
            "question_id": event.question_id,
            "question_text": event.question_text,
            "options": event.options,
            "timestamp": time.time(),
        }
        state.user_questions.append(entry)
        logger.info(
            "[orchestrator] user_question session=%s task=%s q=%s",
            event.session_id,
            state.task_id,
            event.question_text[:100],
        )
        self._maybe_kanban_comment(
            state.task_id,
            f"❓ **User question** `{event.question_id}`:\n"
            f"> {event.question_text}"
            + ("\nOptions: " + ", ".join(event.options) if event.options else ""),
        )

    def _handle_session_stalled(self, event: SessionStalled) -> None:
        """SessionStalled → mark task as stalled."""
        task_id = (event.meta or {}).get("task_id", "")
        state = self._ensure_task(event.session_id, task_id)
        state.status = "stalled"
        logger.warning(
            "[orchestrator] session_stalled session=%s task=%s age=%.1fs tool=%s",
            event.session_id,
            state.task_id,
            event.last_activity_age_seconds,
            event.current_tool,
        )

    def _handle_session_cancelled(self, event: SessionCancelled) -> None:
        """SessionCancelled → rollback task to cancelled state."""
        task_id = (event.meta or {}).get("task_id", "")
        state = self._ensure_task(event.session_id, task_id)
        state.status = "cancelled"
        state.outcome = "cancelled"
        state.ended_at = time.time()
        logger.info(
            "[orchestrator] session_cancelled session=%s task=%s by=%s reason=%s",
            event.session_id,
            state.task_id,
            event.cancelled_by,
            event.reason,
        )
        self._maybe_kanban_comment(
            state.task_id,
            f"🚫 **Session cancelled** by `{event.cancelled_by}`"
            + (f" — {event.reason}" if event.reason else ""),
        )

    # -- Kanban comment persistence ------------------------------------------

    def _maybe_kanban_comment(self, task_id: str, body: str) -> None:
        """Persist an event as a kanban comment if enabled.

        Falls back gracefully — kanban operations failing must not
        propagate exceptions to the event handler caller.
        """
        if not self._kanban_comments or not task_id:
            return
        try:
            # Deferred import to avoid hard kanban dependency at module load
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
