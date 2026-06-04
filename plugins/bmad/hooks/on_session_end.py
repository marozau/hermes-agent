"""on_session_end hook — lifecycle event capture.

Fires at the end of every ``run_conversation`` call (natural or
interrupted).  Captures session metadata into the lifecycle event bus.

Wrapped by _catch_all in __init__.py — never raises.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def on_session_end(
    session_id: str = "",
    completed: bool = False,
    interrupted: bool = False,
    model: str = "",
    platform: str = "",
    **kwargs,
) -> None:
    """Capture a session-end lifecycle event.

    Args match the ``invoke_hook("on_session_end", ...)`` call site in
    ``agent/conversation_loop.py`` (line ~4618). Called directly (no
    ``_bind_hook_ctx``) — receives only what Hermes passes as kwargs.
    """
    from plugins.bmad.lib.lifecycle_events import capture_event

    payload: Dict[str, Any] = {
        "completed": completed,
        "interrupted": interrupted,
        "model": model,
        "platform": platform,
        "wall_time_s": time.time(),
    }

    capture_event(
        session_id=session_id,
        event_type="on_session_end",
        payload=payload,
    )
