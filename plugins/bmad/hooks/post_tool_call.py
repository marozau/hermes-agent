"""post_tool_call hook — BMAD auto-status tracking.

Per architecture A-9 (FR-10): after every Write/Edit tool call that
matches a known BMAD artifact path pattern, automatically update
workflow-status.yaml to mark the corresponding slot as complete.

Idempotent: won't bump ``last_updated`` if the slot already has the
same value.

Wrapped by _catch_all in __init__.py — never raises.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Ordered: most specific first
PATH_RULES: list[tuple[re.Pattern, str, str]] = [
    # Ordered: most specific first (first-match wins). solutioning-gate-check
    # must precede the generic "planning-artifacts/*-.md" patterns.
    (re.compile(r"planning-artifacts/solutioning-gate-check.*\.md$"), "solutioning", "solutioning-gate-check"),
    (re.compile(r"planning-artifacts/(epics-stories|epics)[-_/].*"), "solutioning", "epics-stories"),
    (re.compile(r"planning-artifacts/(architecture|tech-spec)[-_].*\.md$"), "solutioning", "architecture"),
    (re.compile(r"planning-artifacts/prd[-_].*\.md$"), "planning", "prd"),
    (re.compile(r"planning-artifacts/product-brief.*\.md$"), "analysis", "product-brief"),
    (re.compile(r"planning-artifacts/research/.*"), "analysis", "research"),
    (re.compile(r"implementation-artifacts/stories/.*\.md$"), "implementation", "story"),
]


def post_tool_call(ctx, tool_name: str, tool_args: dict, tool_result: dict | None) -> None:
    """Auto-update workflow-status.yaml after Write/Edit of BMAD artifacts.

    If the written file matches a known artifact pattern and the slot
    isn't already marked as complete, atomically update the status.
    """
    if tool_name not in ("Write", "Edit", "write_file", "edit_file"):
        return

    project_dir = _resolve_project_dir(ctx)
    if project_dir is None:
        return

    config_path = project_dir / "bmad" / "config.yaml"
    if not config_path.exists():
        return  # Not a BMAD project

    file_path = tool_args.get("file_path", tool_args.get("path", ""))
    if not file_path:
        return

    rel = _relative_to_project(file_path, project_dir)
    if rel is None:
        return  # Write outside project

    phase_slot = _match_path(rel)
    if phase_slot is None:
        return  # Not a known artifact

    phase, slot = phase_slot

    from plugins.bmad.lib import status as s

    try:
        state = s.load(project_dir)
        current = state.get("phases", {}).get(phase, {}).get(slot)
        if current != rel:  # idempotency check
            logger.info("[bmad:post_tool_call] %s → %s/%s complete", rel, phase, slot)
            s.mark_complete(project_dir, phase, slot, rel)
    except Exception:
        logger.exception("[bmad:post_tool_call] Status update failed — allowing through")


def _relative_to_project(file_path: str, project_dir: Path) -> str | None:
    path = Path(file_path).resolve()
    try:
        return str(path.relative_to(project_dir.resolve()))
    except ValueError:
        return None


def _match_path(rel_path: str) -> tuple[str, str] | None:
    for pattern, phase, slot in PATH_RULES:
        if pattern.search(rel_path):
            return (phase, slot)
    return None


def _resolve_project_dir(ctx) -> Path | None:
    if hasattr(ctx, "project_dir") and ctx.project_dir:
        return Path(ctx.project_dir)
    if hasattr(ctx, "working_directory") and ctx.working_directory:
        return Path(ctx.working_directory)
    return None
