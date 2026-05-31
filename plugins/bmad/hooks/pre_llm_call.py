"""pre_llm_call hook — lifecycle event capture.

Fires before every LLM API call in the conversation loop.
Captures metadata about the turn: message count, model, token estimate.

Wrapped by _catch_all in __init__.py — never raises.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def pre_llm_call(
    ctx,
    session_id: str = "",
    user_message: str = "",
    conversation_history: Optional[List[Dict[str, Any]]] = None,
    is_first_turn: bool = False,
    model: str = "",
    platform: str = "",
    sender_id: str = "",
    session_search_fn: Optional[callable] = None,
    **kwargs,
) -> Optional[Dict[str, Any]]:
    """Capture a pre-LLM lifecycle event.  Always returns None (observer only).

    Args match the ``invoke_hook("pre_llm_call", ...)`` call site in
    ``agent/conversation_loop.py`` (line ~706). *ctx* is injected by
    ``_bind_hook_ctx`` in ``__init__.py``.

    Returns None so the agent core does NOT inject extra context from
    this hook — we are a pure observer.
    """
    from plugins.bmad.lib.lifecycle_events import capture_event

    history_len = len(conversation_history) if conversation_history else 0

    # Estimate token count from message lengths (rough heuristic)
    approx_chars = sum(
        len(str(m.get("content", "")))
        for m in (conversation_history or [])
    ) + len(user_message)
    approx_tokens = max(1, approx_chars // 4)  # ~4 chars per token

    # Detect if this looks like a question (heuristic)
    is_question = _detect_question(user_message)

    payload: Dict[str, Any] = {
        "model": model,
        "platform": platform,
        "sender_id": sender_id,
        "is_first_turn": is_first_turn,
        "message_count": history_len,
        "approx_input_tokens": approx_tokens,
        "user_message_excerpt": user_message[:200] if user_message else "",
        "is_question_detected": is_question,
        "wall_time_s": time.time(),
    }

    capture_event(
        session_id=session_id,
        event_type="pre_llm_call",
        payload=payload,
    )

    # Pure observer — no context injection
    return None


def _detect_question(text: str) -> bool:
    """Heuristic question detection.

    Checks for question marks, common interrogative words at sentence
    start, and common question patterns.
    """
    if not text:
        return False
    if "?" in text:
        return True
    text_lower = text.lower().strip()
    interrogatives = (
        "what", "how", "why", "when", "where", "who",
        "can you", "could you", "would you", "will you",
        "please", "tell me", "show me", "explain",
        "is it", "are there", "do you", "does it",
    )
    for prefix in interrogatives:
        if text_lower.startswith(prefix):
            return True
    return False
