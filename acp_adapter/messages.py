"""Hermes session event message types sent over ACP via ext_notification.

These are Hermes-proprietary session lifecycle messages that plugin hooks
and the agent runtime can send to connected editors to convey session state
changes beyond what the standard ACP session_update API supports.

Each message is a Pydantic model that serializes to a JSON dict. Messages
are sent via ``conn.ext_notification("hermes/session_event", payload)``
using the ACP Client protocol's extension mechanism.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, ConfigDict


def _utc_now_iso() -> str:
    """Return current UTC timestamp as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Discriminated event type enum
# ---------------------------------------------------------------------------

SessionEventType = str  # "session_start" | "session_heartbeat" | ...


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class HermesSessionEvent(BaseModel):
    """Base for all Hermes session event messages."""

    model_config = ConfigDict(populate_by_name=True)

    # Unique event id (UUIDv4 hex)
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    # Event type discriminator
    event_type: SessionEventType
    # Session this event pertains to
    session_id: str = Field(alias="sessionId")
    # ISO 8601 timestamp of event generation
    timestamp: str = Field(default_factory=_utc_now_iso)
    # Arbitrary metadata (reserved for future use)
    meta: Optional[Dict[str, Any]] = Field(default=None, alias="_meta")

    def to_json_dict(self) -> Dict[str, Any]:
        """Serialize to a dict suitable for sending over ACP ext_notification."""
        return self.model_dump(by_alias=True, exclude_none=True)


# ---------------------------------------------------------------------------
# SessionStart
# ---------------------------------------------------------------------------

class SessionStart(HermesSessionEvent):
    """Emitted when a new ACP session is created and agent is ready."""

    event_type: SessionEventType = Field(
        default="session_start", alias="eventType", frozen=True
    )
    # Working directory for the session
    cwd: str
    # Client info from the initialize handshake (if available)
    client_info: Optional[Dict[str, str]] = Field(default=None, alias="clientInfo")


# ---------------------------------------------------------------------------
# SessionHeartbeat
# ---------------------------------------------------------------------------

class SessionHeartbeat(HermesSessionEvent):
    """Periodic heartbeat indicating the agent session is still alive."""

    event_type: SessionEventType = Field(
        default="session_heartbeat", alias="eventType", frozen=True
    )
    # Current agent state summary
    agent_state: Optional[str] = Field(default=None, alias="agentState")
    # Name of the currently executing tool, if any
    current_tool: Optional[str] = Field(default=None, alias="currentTool")
    # API call iteration count
    iteration: Optional[int] = None


# ---------------------------------------------------------------------------
# SessionEnd
# ---------------------------------------------------------------------------

class SessionEnd(HermesSessionEvent):
    """Emitted when an agent session terminates."""

    event_type: SessionEventType = Field(
        default="session_end", alias="eventType", frozen=True
    )
    # Outcome: "completed", "error", "cancelled", "timeout"
    outcome: str
    # Human-readable summary of what happened
    summary: Optional[str] = None
    # Structured reason info (error message, timeout details, etc.)
    reason: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# ToolCallResult
# ---------------------------------------------------------------------------

class ToolCallResult(HermesSessionEvent):
    """Emitted when a single tool call completes with structured result info."""

    event_type: SessionEventType = Field(
        default="tool_call_result", alias="eventType", frozen=True
    )
    # ACP tool call id
    tool_call_id: str = Field(alias="toolCallId")
    # Hermes tool name (e.g. "terminal", "read_file")
    tool_name: str = Field(alias="toolName")
    # Whether the tool call succeeded
    success: bool
    # Wall-clock duration in milliseconds
    duration_ms: Optional[float] = Field(default=None, alias="durationMs")
    # Error message if the tool failed
    error: Optional[str] = None
    # Optional structured result payload
    result_summary: Optional[Dict[str, Any]] = Field(
        default=None, alias="resultSummary"
    )


# ---------------------------------------------------------------------------
# UserQuestion
# ---------------------------------------------------------------------------

class UserQuestion(HermesSessionEvent):
    """Emitted when the agent needs to ask the user a question (e.g. clarify)."""

    event_type: SessionEventType = Field(
        default="user_question", alias="eventType", frozen=True
    )
    # Unique id for this question (can be used to correlate with answer)
    question_id: str = Field(alias="questionId")
    # The question text
    question_text: str = Field(alias="questionText")
    # Optional list of pre-defined choices
    options: Optional[List[str]] = None


# ---------------------------------------------------------------------------
# SessionStalled
# ---------------------------------------------------------------------------

class SessionStalled(HermesSessionEvent):
    """Emitted when the session appears to be stuck or inactive."""

    event_type: SessionEventType = Field(
        default="session_stalled", alias="eventType", frozen=True
    )
    # Seconds since last agent activity
    last_activity_age_seconds: float = Field(alias="lastActivityAgeSeconds")
    # The tool that is currently running (if known)
    current_tool: Optional[str] = Field(default=None, alias="currentTool")
    # Additional diagnostic details
    diagnostic: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# SessionCancelled
# ---------------------------------------------------------------------------

class SessionCancelled(HermesSessionEvent):
    """Emitted when a session is cancelled by the user or system."""

    event_type: SessionEventType = Field(
        default="session_cancelled", alias="eventType", frozen=True
    )
    # Who or what cancelled the session: "user", "system", "timeout"
    cancelled_by: str = Field(alias="cancelledBy")
    # Optional reason message
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Union type for all session events
# ---------------------------------------------------------------------------

SessionEvent = (
    SessionStart
    | SessionHeartbeat
    | SessionEnd
    | ToolCallResult
    | UserQuestion
    | SessionStalled
    | SessionCancelled
)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

# Map event_type string -> class for deserialization
_EVENT_TYPE_REGISTRY: Dict[str, type[HermesSessionEvent]] = {
    "session_start": SessionStart,
    "session_heartbeat": SessionHeartbeat,
    "session_end": SessionEnd,
    "tool_call_result": ToolCallResult,
    "user_question": UserQuestion,
    "session_stalled": SessionStalled,
    "session_cancelled": SessionCancelled,
}

# The ACP ext_notification method name
SESSION_EVENT_METHOD = "hermes/session_event"


def deserialize_session_event(data: Dict[str, Any]) -> SessionEvent | None:
    """Deserialize a JSON dict back into a HermesSessionEvent.

    Args:
        data: Raw dict from ACP ext_notification params.

    Returns:
        The deserialized event, or None if event_type is unknown.
    """
    event_type = data.get("eventType") or data.get("event_type")
    if not event_type or event_type not in _EVENT_TYPE_REGISTRY:
        return None
    cls = _EVENT_TYPE_REGISTRY[event_type]
    return cls.model_validate(data)
