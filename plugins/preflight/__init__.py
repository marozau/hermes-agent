"""hermes preflight plugin — wires lib.hermes_preflight to hermes-agent hooks."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

# Ensure ~/.hermes/lib/ is importable regardless of HERMES_HOME value.
# HERMES_HOME may be scoped to a profile dir (e.g. .../profiles/engineer/),
# so walk upward until we find the lib/ directory.
_candidate = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))).resolve()
for _anchor in [_candidate, _candidate.parent, _candidate.parent.parent, _candidate.parent.parent.parent]:
    _lib = _anchor / "lib"
    if _lib.is_dir() and str(_anchor) not in sys.path:
        sys.path.insert(0, str(_anchor))
        break
else:
    # Fallback: try the real home
    _real_root = Path(os.path.expanduser("~/.hermes")).resolve()
    if str(_real_root) not in sys.path:
        sys.path.insert(0, str(_real_root))

from lib.hermes_preflight import should_run_preflight

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
