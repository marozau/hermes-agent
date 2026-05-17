"""on_session_start hook — BMAD project detection & context setup.

Per architecture A-5: loads the workflow status for the project
so it's ready for subsequent hooks and slash commands.

Wrapped by _catch_all in __init__.py — never raises.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def on_session_start(ctx) -> None:
    """Detect BMAD project and warm the status cache.

    If the session's working directory contains ``bmad/config.yaml``
    we consider it a BMAD project and pre-load workflow-status.yaml
    into the lib/status module cache so subsequent hooks/commands
    don't pay the first-read I/O latency.

    Silent no-op outside a BMAD project.
    """
    project_dir = _resolve_project_dir(ctx)
    if project_dir is None:
        return

    config_path = project_dir / "bmad" / "config.yaml"
    if not config_path.exists():
        return

    from plugins.bmad.lib import status

    try:
        state = status.load(project_dir)
        level = state.get("level", 1)
        logger.info(
            "[bmad:on_session_start] BMAD project detected: %s (level=%s)",
            state.get("project", project_dir.name),
            level,
        )
    except Exception:
        logger.exception("[bmad:on_session_start] Failed to load workflow status")
        return


def _resolve_project_dir(ctx) -> Path | None:
    """Try to get the project directory from the session context."""
    # ctx.project_dir is the canonical property per Hermes plugin API
    if hasattr(ctx, "project_dir") and ctx.project_dir:
        return Path(ctx.project_dir)
    # Fallback: working directory
    if hasattr(ctx, "working_directory") and ctx.working_directory:
        return Path(ctx.working_directory)
    return None
