"""Handler for /bmad:module-builder — create and scaffold BMAD modules."""

from __future__ import annotations

from pathlib import Path

COMMAND = "module-builder"


def handler(ctx, args: str) -> str:
    """Return the .md body for module-builder."""
    from plugins.bmad.commands import __file__ as _cmd_file
    cmd_dir = Path(_cmd_file).parent
    body_file = cmd_dir / f"{COMMAND}.md"
    if body_file.exists():
        return body_file.read_text()
    return "# {COMMAND}\n\nBody file not found."
