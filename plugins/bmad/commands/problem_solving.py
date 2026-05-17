"""Handler for /bmad:problem-solving — creative problem-solving with Dr. Quinn."""

from __future__ import annotations

from pathlib import Path

COMMAND = "problem-solving"


def handler(ctx, args: str) -> str:
    """Return the .md body for problem-solving."""
    from plugins.bmad.commands import __file__ as _cmd_file
    cmd_dir = Path(_cmd_file).parent
    body_file = cmd_dir / f"{COMMAND}.md"
    if body_file.exists():
        return body_file.read_text()
    return "# {COMMAND}\n\nBody file not found."
