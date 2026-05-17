"""Handler for /bmad:ci — CI/CD pipeline configuration."""

from __future__ import annotations

from pathlib import Path

COMMAND = "ci"


def handler(ctx, args: str) -> str:
    """Return the .md body for ci."""
    raw_dir = getattr(ctx, "working_directory", None) or getattr(ctx, "project_dir", "")
    body_path = Path(raw_dir)
    return _read_body(body_path, COMMAND)


def _read_body(project_dir: Path, command: str) -> str:
    """Read slash command body from commands/<name>.md."""
    from plugins.bmad.commands import __file__ as _cmd_file
    cmd_dir = Path(_cmd_file).parent
    body_file = cmd_dir / f"{command}.md"
    if body_file.exists():
        return body_file.read_text()
    return "# {command}\n\nBody file not found."
