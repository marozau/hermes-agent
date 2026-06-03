"""Handler for /bmad:atdd — acceptance test-driven development."""

from __future__ import annotations

from pathlib import Path

COMMAND = "atdd"


def handler(ctx, args: str) -> str:
    """Return the .md body for atdd."""
    raw_dir = getattr(ctx, "working_directory", None) or getattr(ctx, "project_dir", "")
    body_path = Path(raw_dir)
    return _read_body(body_path, COMMAND)


def _read_body(project_dir: Path, command: str) -> str:
    """Read slash command body from commands/<name>.md."""
    from plugins.bmad.commands import __file__ as _cmd_file
    cmd_dir = Path(_cmd_file).parent
    body_file = cmd_dir / f"{command}.md"
    if body_file.exists():
        body = body_file.read_text()
        from plugins.bmad.lib.spec_parser import parse_command_body
        from plugins.bmad.lib.render import render_command
        spec, body_text = parse_command_body(body)
        return render_command(spec, body_text, args=args.strip() if args else "", ctx=ctx)
    return "# {command}\n\nBody file not found."
