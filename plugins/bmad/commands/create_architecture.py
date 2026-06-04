"""Handler for /bmad:create-architecture — system architecture design."""

from __future__ import annotations

from pathlib import Path

COMMAND = "create-architecture"


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

    body_path = Path(__file__).with_name("create-architecture.md")
    body = body_path.read_text(encoding="utf-8")

    # Story 12.9: Parse spec and render
    from plugins.bmad.lib.spec_parser import parse_command_body
    from plugins.bmad.lib.render import render_command

    # Inject reflection-bank watch-outs for recurring patterns.
    from plugins.bmad.lib.phases import COMMAND_PHASE
    from plugins.bmad.judge.phase_gates import inject_adjustments

    phase = COMMAND_PHASE[COMMAND][0]
    body = inject_adjustments(phase=phase, project_root=project_dir, body=body)


    spec, body_text = parse_command_body(body)
    return render_command(spec, body_text, args=args.strip() if args else "", ctx=ctx)
