"""hermes preflight plugin — wires autodream.preflight to hermes-agent hooks."""
from __future__ import annotations

import logging
from typing import Any, Optional

from autodream.preflight import should_run_preflight

logger = logging.getLogger(__name__)


def register(ctx: Any) -> None:
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    logger.info("preflight plugin registered pre_llm_call hook")


def on_pre_llm_call(
    *,
    session_id: str = "",
    user_message: str = "",
    session_search_fn: Any = None,
    **_kwargs: Any,
) -> Optional[dict]:
    if not user_message or not isinstance(user_message, str):
        return None
    try:
        gate, reason, heads_up = should_run_preflight(
            session_id=session_id,
            message=user_message,
            session_search_fn=session_search_fn,
        )
    except Exception as e:
        logger.warning("preflight hook failed: %s", e)
        return None
    if heads_up is None:
        return None
    return {"context": heads_up}
