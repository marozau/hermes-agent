"""Handler for /bmad:quick-spec — lightweight tech spec for level 0-1."""

from __future__ import annotations

from pathlib import Path

COMMAND = "quick-spec"


def handler(ctx, args: str) -> str:
    raw_dir = getattr(ctx, "working_directory", None) or "."
    project_dir = Path(raw_dir).resolve()

    config_path = project_dir / "bmad" / "config.yaml"
    if not config_path.exists():
        return "⚠️  Not a BMAD project. Run `/bmad:init` to initialize."

    from plugins.bmad.lib import phases
    from plugins.bmad.lib.status import load

    state = load(project_dir)
    level = state.get("level", 1)
    ok, reason = phases.can_run(COMMAND, state, level)
    if not ok:
        return f"🚫 **{COMMAND} blocked:** {reason}"

    body_path = Path(__file__).with_name(f"{COMMAND}.md")
    body = body_path.read_text(encoding="utf-8")

    # Story 12.9: Parse spec and render
    from plugins.bmad.lib.spec_parser import parse_command_body
    from plugins.bmad.lib.render import render_command

    spec, body_text = parse_command_body(body)
    return render_command(spec, body_text, args=args.strip() if args else "", ctx=ctx)
