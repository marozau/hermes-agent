"""Handler for /bmad:doctor — read-only BMAD project diagnostic."""

from __future__ import annotations

from pathlib import Path

COMMAND = "doctor"


def handler(ctx, args: str) -> str:
    raw_dir = args.strip() if args else ""
    project_dir = Path(raw_dir).resolve() if raw_dir else Path.cwd()

    if not (project_dir / "bmad").exists() and not (project_dir / "planning-artifacts").exists():
        # Try to find BMAD project root
        for parent in project_dir.parents:
            if (parent / "bmad").exists() or (parent / "planning-artifacts").exists():
                project_dir = parent
                break

    from plugins.bmad.lib.spec_parser import parse_command_body
    from plugins.bmad.lib.render import render_command
    body_path = Path(__file__).with_name("doctor.md")
    spec, body = parse_command_body(body_path.read_text(encoding="utf-8"))
    rendered = render_command(spec, body, args=args, ctx=ctx)

    # Run doctor and append report
    from plugins.bmad.lib.doctor import run_doctor
    report = run_doctor(project_dir)

    return f"{rendered}\n\n---\n\n{report.to_markdown()}"
