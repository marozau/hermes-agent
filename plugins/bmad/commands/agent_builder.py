"""Handler for /bmad:agent-builder — build and refine BMAD agents."""

from __future__ import annotations

from pathlib import Path

COMMAND = "agent-builder"


def handler(ctx, args: str) -> str:
    """Return the .md body for agent-builder."""
    from plugins.bmad.commands import __file__ as _cmd_file
    cmd_dir = Path(_cmd_file).parent
    body_file = cmd_dir / f"{COMMAND}.md"
    if body_file.exists():
        return body_file.read_text()
    return "# {COMMAND}\n\nBody file not found."
