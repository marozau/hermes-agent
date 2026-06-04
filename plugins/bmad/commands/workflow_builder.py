"""Handler for /bmad:workflow-builder — design and build BMAD workflows."""

from __future__ import annotations

from pathlib import Path

COMMAND = "workflow-builder"


def handler(ctx, args: str) -> str:
    """Return the .md body for workflow-builder."""
    from plugins.bmad.commands import __file__ as _cmd_file
    cmd_dir = Path(_cmd_file).parent
    body_file = cmd_dir / f"{COMMAND}.md"
    if body_file.exists():
        body = body_file.read_text()
        from plugins.bmad.lib.spec_parser import parse_command_body
        from plugins.bmad.lib.render import render_command
        spec, body_text = parse_command_body(body)
        return render_command(spec, body_text, args=args.strip() if args else "", ctx=ctx)
    return "# {COMMAND}\n\nBody file not found."
