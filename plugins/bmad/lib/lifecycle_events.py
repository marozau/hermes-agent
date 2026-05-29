"""BMAD lifecycle event capture — in-memory event bus with structured events.

Captures Hermes lifecycle events (on_session_start, on_session_end,
pre_tool_call, post_tool_call, pre_llm_call, post_llm_call) into a
standardised internal event structure. No transport — events are
queued in a bounded in-memory ring buffer. Downstream consumers
(skills, cron jobs, dashboards) can drain the queue at their own
pace.

Per the task spec:
- Non‑blocking: each hook callback is wrapped by _catch_all in __init__.py
- Idempotent: duplicate events within the same second are deduplicated
- Configurable: enable/disable per hook via env vars or profile config
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Defaults ──────────────────────────────────────────────────────────────

_DEFAULT_MAX_EVENTS = 10_000
_DEFAULT_ENABLED = True
_DEFAULT_HOOKS_ENABLED: Dict[str, bool] = {
    "on_session_start": True,
    "on_session_end": True,
    "pre_tool_call": False,   # high volume — off by default
    "post_tool_call": False,  # high volume — off by default
    "pre_llm_call": True,
    "post_llm_call": True,
}


# ── Event structure ────────────────────────────────────────────────────────


@dataclass
class LifecycleEvent:
    """Standardised event captured at each lifecycle hook firing.

    Fields correspond to the task spec: session id, task id, timestamp,
    event type, and relevant payload.
    """

    session_id: str
    event_type: str
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)

    def dedup_key(self) -> str:
        """Return a deduplication key for idempotency within the same second.

        Two events are considered duplicates when they share the same
        session_id, event_type, integer-second timestamp, and a content
        hash of the payload.
        """
        ts_int = int(self.timestamp)
        payload_hash = hashlib.sha256(
            json.dumps(self.payload, sort_keys=True, default=str).encode()
        ).hexdigest()[:8]
        return f"{self.session_id}:{self.event_type}:{ts_int}:{payload_hash}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "payload": self.payload,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


# ── Event bus ──────────────────────────────────────────────────────────────


class LifecycleEventBus:
    """Thread-safe bounded ring buffer for lifecycle events.

    Singleton per process — all hooks write to the same bus.
    No transport; downstream consumers drain via :meth:`drain` and
    :meth:`drain_all`.
    """

    def __init__(self, max_events: int = _DEFAULT_MAX_EVENTS):
        self._lock = threading.Lock()
        self._queue: deque[LifecycleEvent] = deque(maxlen=max_events)
        self._dedup_set: deque[str] = deque(maxlen=max_events * 2)
        self._max_events = max_events
        self._counters: Dict[str, int] = {}  # event_type → count

    def push(self, event: LifecycleEvent) -> bool:
        """Push an event onto the bus.

        Returns True if the event was enqueued, False if it was
        deduplicated (same key saw within the dedup window).
        """
        dedup_key = event.dedup_key()
        with self._lock:
            if dedup_key in self._dedup_set:
                return False
            self._dedup_set.append(dedup_key)
            self._queue.append(event)
            self._counters[event.event_type] = (
                self._counters.get(event.event_type, 0) + 1
            )
            return True

    def drain(self, limit: int = 100) -> List[LifecycleEvent]:
        """Remove and return up to ``limit`` oldest events."""
        with self._lock:
            events = []
            for _ in range(min(limit, len(self._queue))):
                events.append(self._queue.popleft())
            return events

    def drain_all(self) -> List[LifecycleEvent]:
        """Remove and return ALL events, emptying the queue."""
        return self.drain(len(self._queue))

    def peek(self, limit: int = 10) -> List[LifecycleEvent]:
        """Return up to ``limit`` newest events without removing them."""
        with self._lock:
            items = list(self._queue)
            return items[-limit:] if limit < len(items) else items

    def stats(self) -> Dict[str, Any]:
        """Return bus statistics."""
        with self._lock:
            return {
                "queue_size": len(self._queue),
                "max_events": self._max_events,
                "counters": dict(self._counters),
            }

    def clear(self) -> None:
        """Clear the event queue and counters."""
        with self._lock:
            self._queue.clear()
            self._dedup_set.clear()
            self._counters.clear()


# ── Singleton access ───────────────────────────────────────────────────────

_event_bus: Optional[LifecycleEventBus] = None
_bus_lock = threading.Lock()


def get_event_bus() -> LifecycleEventBus:
    """Return the process-global event bus, creating it on first access."""
    global _event_bus
    if _event_bus is None:
        with _bus_lock:
            if _event_bus is None:
                max_events = int(
                    os.environ.get("BMAD_LIFECYCLE_MAX_EVENTS", _DEFAULT_MAX_EVENTS)
                )
                _event_bus = LifecycleEventBus(max_events=max_events)
                logger.info(
                    "[bmad:lifecycle] Event bus initialised (max_events=%d)",
                    max_events,
                )
    return _event_bus


def reset_event_bus() -> None:
    """Reset the event bus — primarily for tests."""
    global _event_bus
    with _bus_lock:
        if _event_bus is not None:
            _event_bus.clear()
        _event_bus = None


# ── Hook enablement ────────────────────────────────────────────────────────


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean env var with a default."""
    val = os.environ.get(name, "").strip().lower()
    if not val:
        return default
    return val in ("1", "true", "yes", "on", "enabled")


def _get_enabled_hooks() -> Dict[str, bool]:
    """Resolve which hooks are enabled.

    Reads from env vars (BMAD_LIFECYCLE_HOOK_<NAME>) → per-hook overrides,
    then BMAD_LIFECYCLE_EVENTS_ENABLED → global toggle.
    """
    global_enabled = _env_bool("BMAD_LIFECYCLE_EVENTS_ENABLED", _DEFAULT_ENABLED)
    if not global_enabled:
        return {k: False for k in _DEFAULT_HOOKS_ENABLED}

    result = dict(_DEFAULT_HOOKS_ENABLED)
    for hook_name in _DEFAULT_HOOKS_ENABLED:
        env_key = f"BMAD_LIFECYCLE_HOOK_{hook_name.upper()}"
        if env_key in os.environ:
            result[hook_name] = _env_bool(env_key, result[hook_name])
    return result


def is_hook_enabled(hook_name: str) -> bool:
    """Check if a specific lifecycle hook is enabled for event capture."""
    return _get_enabled_hooks().get(hook_name, False)


# ── Event capture helpers ──────────────────────────────────────────────────


def _resolve_task_id() -> str:
    """Resolve the current kanban task id from env, or return empty."""
    return os.environ.get("HERMES_KANBAN_TASK", "")


# Module-level publisher reference — set by ACP adapter or LifecycleBridge
# so hooks can emit ACP events directly without signature changes.
_current_publisher: Any = None
_publisher_lock = threading.Lock()


def set_current_publisher(publisher: Any) -> None:
    """Set the ACP SessionEventPublisher for the current session.

    Plugins and the ACP adapter call this when a session starts so
    hooks can emit ACP events directly alongside bus capture.
    """
    global _current_publisher
    with _publisher_lock:
        _current_publisher = publisher


def get_current_publisher() -> Any:
    """Return the current ACP publisher, or None."""
    with _publisher_lock:
        return _current_publisher


def clear_current_publisher() -> None:
    """Clear the current publisher — called on session teardown."""
    global _current_publisher
    with _publisher_lock:
        _current_publisher = None


def _emit_acp_if_available(
    event_type: str,
    session_id: str,
    task_id: str,
    payload: Dict[str, Any],
) -> None:
    """Emit an ACP event via the current publisher if one is set.

    Fire-and-forget: errors are logged but never raised.
    """
    publisher = get_current_publisher()
    if publisher is None:
        return

    try:
        if event_type == "on_session_end":
            completed = payload.get("completed", False)
            interrupted = payload.get("interrupted", False)
            if interrupted:
                outcome = "cancelled"
            elif not completed:
                outcome = "error"
            else:
                outcome = "completed"
            publisher.session_end(
                outcome=outcome,
                summary=(
                    "Session completed" if completed
                    else "Session interrupted" if interrupted
                    else "Session ended without completion"
                ),
                reason={
                    "model": payload.get("model", ""),
                    "platform": payload.get("platform", ""),
                },
                meta={"task_id": task_id},
            )
        elif event_type == "pre_llm_call":
            publisher.session_heartbeat(
                agent_state="thinking",
                current_tool=None,
                iteration=None,
                meta={"task_id": task_id},
            )
        elif event_type == "post_llm_call":
            publisher.session_heartbeat(
                agent_state="responding",
                current_tool="llm_response",
                iteration=None,
                meta={
                    "task_id": task_id,
                    "tool_call_count": payload.get(
                        "tool_call_count_this_turn", 0
                    ),
                },
            )
        elif event_type == "post_tool_call":
            error = payload.get("error")
            if error:
                publisher.tool_call_result(
                    tool_call_id=payload.get(
                        "tool_call_id", f"unknown-{task_id}"
                    ),
                    tool_name=payload.get("tool_name", "unknown"),
                    success=False,
                    error=str(error)[:500],
                    meta={"task_id": task_id},
                )
    except Exception:
        logger.debug(
            "Failed to emit ACP %s for session %s",
            event_type,
            session_id,
            exc_info=True,
        )


def capture_event(
    session_id: str,
    event_type: str,
    payload: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
) -> Optional[LifecycleEvent]:
    """Capture a lifecycle event and push it to the bus.

    If an ACP publisher is currently registered (via set_current_publisher),
    also emits the event as an ACP session event.

    Returns the event if captured, None if the hook is disabled or
    the event was deduplicated.
    """
    if not is_hook_enabled(event_type):
        return None

    bus = get_event_bus()
    event = LifecycleEvent(
        session_id=session_id or "",
        event_type=event_type,
        task_id=task_id or _resolve_task_id(),
        payload=payload or {},
    )
    enqueued = bus.push(event)
    if enqueued:
        logger.debug(
            "[bmad:lifecycle] %s session=%s task=%s",
            event_type,
            session_id,
            event.task_id,
        )
        # Also emit ACP event if publisher is available
        _emit_acp_if_available(
            event_type=event_type,
            session_id=session_id,
            task_id=event.task_id,
            payload=payload or {},
        )
    return event if enqueued else None
