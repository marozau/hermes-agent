"""transform_terminal_output hook — BMAD status header.

Prepends a compact BMAD status line to every prompt within a BMAD
project. Suppressible via ``display.bmad_header: false`` in the
user's profile config.

Per architecture A-10: hard 120-char cap, ASCII-only, no emoji.

Wrapped by _catch_all in __init__.py — never raises.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def transform_terminal_output(ctx, text: str, **kwargs) -> str | None:
    """Prepend BMAD status header to *text* if inside a BMAD project.

    Returns a new string with the header prepended, or ``None`` to
    pass through unchanged (when not in a BMAD project or when
    suppressed by config).
    """
    project_dir = _resolve_project_dir(ctx)
    if project_dir is None:
        return None

    config_path = project_dir / "bmad" / "config.yaml"
    if not config_path.exists():
        return None

    # Check suppression flag
    if ctx.profile_config.get("display", {}).get("bmad_header") is False:
        return None

    from plugins.bmad.lib import status as s
    from plugins.bmad.lib import phases

    try:
        state = s.load(project_dir)
        nxt = phases.next_required_slot(state, level=state.get("level", 1))
        next_cmd = nxt["command"] if nxt else "all-complete"
        cur_phase = _current_phase(state)

        header = (
            f"BMAD: {state.get('project', project_dir.name)} | "
            f"level={state.get('level', 1)} | "
            f"phase={cur_phase} | next: {next_cmd}"
        )

        if len(header) > 120:
            header = header[:117] + "..."

        return f"{header}\n{text}"
    except Exception:
        logger.exception("[bmad:transform_terminal_output] Header render failed")
        return None  # Hooks never raise


def _current_phase(state: dict) -> str:
    """Determine the current phase from state.

    Returns the last phase with any complete slot, or 'not-started'.
    """
    from plugins.bmad.lib.phases import PHASE_ORDER

    phases_state = state.get("phases", {})
    last_active = PHASE_ORDER[0]

    for phase in PHASE_ORDER:
        slots = phases_state.get(phase, {})
        for _slot, val in slots.items():
            if val == "complete":
                last_active = phase
                break

    return last_active


def _resolve_project_dir(ctx) -> Path | None:
    if hasattr(ctx, "project_dir") and ctx.project_dir:
        return Path(ctx.project_dir)
    if hasattr(ctx, "working_directory") and ctx.working_directory:
        return Path(ctx.working_directory)
    return None
