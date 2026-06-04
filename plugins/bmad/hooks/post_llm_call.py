"""post_llm_call hook — lifecycle event capture.

Fires after the tool-calling loop completes and the assistant has
produced a final text response.  Captures the assistant response
excerpt, message count, and turn metadata.

Wrapped by _catch_all in __init__.py — never raises.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def post_llm_call(
    session_id: str = "",
    user_message: str = "",
    assistant_response: str = "",
    conversation_history: Optional[List[Dict[str, Any]]] = None,
    model: str = "",
    platform: str = "",
    **kwargs,
) -> None:
    """Capture a post-LLM lifecycle event.

    Args match the ``invoke_hook("post_llm_call", ...)`` call site in
    ``agent/conversation_loop.py`` (line ~4499). Called directly (no
    ``_bind_hook_ctx``) — receives only what Hermes passes as kwargs.
    """
    from plugins.bmad.lib.lifecycle_events import capture_event

    history_len = len(conversation_history) if conversation_history else 0
    response_len = len(assistant_response) if assistant_response else 0

    # Count tool calls in the conversation history from this turn
    tool_call_count = 0
    if conversation_history:
        # Walk backwards until hitting a user message to count only this turn
        for msg in reversed(conversation_history):
            role = msg.get("role", "")
            if role == "user":
                break
            if role == "assistant" and msg.get("tool_calls"):
                tool_call_count += len(msg["tool_calls"])
            elif role == "tool":
                tool_call_count += 1

    payload: Dict[str, Any] = {
        "model": model,
        "platform": platform,
        "message_count": history_len,
        "assistant_response_length": response_len,
        "assistant_response_excerpt": assistant_response[:200] if assistant_response else "",
        "tool_call_count_this_turn": tool_call_count,
        "wall_time_s": time.time(),
    }

    capture_event(
        session_id=session_id,
        event_type="post_llm_call",
        payload=payload,
    )
