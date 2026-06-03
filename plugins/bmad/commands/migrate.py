"""Handler for /bmad:migrate — per-wave BMAD project migration."""

from __future__ import annotations

import re
from pathlib import Path

COMMAND = "migrate"


def handler(ctx, args: str) -> str:
    raw_dir = ""
    flags = {"plan": False, "apply": False, "dry_run": False, "wave": None, "resume": False}

    if args:
        parts = args.split()
        i = 0
        while i < len(parts):
            if parts[i] == "--plan":
                flags["plan"] = True
            elif parts[i] == "--apply":
                flags["apply"] = True
            elif parts[i] == "--dry-run":
                flags["dry_run"] = True
            elif parts[i] == "--wave" and i + 1 < len(parts):
                try:
                    flags["wave"] = int(parts[i + 1])
                except ValueError:
                    pass
                i += 1
            elif parts[i] == "--resume":
                flags["resume"] = True
            elif not parts[i].startswith("--"):
                raw_dir = parts[i]
            i += 1

    project_dir = Path(raw_dir).resolve() if raw_dir else Path.cwd()

    # Find BMAD project root
    if not (project_dir / "bmad").exists() and not (project_dir / "planning-artifacts").exists():
        for parent in project_dir.parents:
            if (parent / "bmad").exists() or (parent / "planning-artifacts").exists():
                project_dir = parent
                break

    from plugins.bmad.lib.spec_parser import parse_command_body
    from plugins.bmad.lib.render import render_command
    body_path = Path(__file__).with_name("migrate.md")
    spec, body = parse_command_body(body_path.read_text(encoding="utf-8"))
    rendered = render_command(spec, body, args=args, ctx=ctx)

    from plugins.bmad.lib.migrate import create_migration_plan, execute_migration

    plan = create_migration_plan(project_dir)

    if flags["plan"]:
        return f"{rendered}\n\n---\n\n{plan.to_markdown()}"

    if flags["apply"] or flags["dry_run"]:
        waves = [flags["wave"]] if flags["wave"] else None
        plan = execute_migration(plan, project_dir, waves=waves, dry_run=flags["dry_run"])
        return f"{rendered}\n\n---\n\n{plan.to_markdown()}"

    return f"{rendered}\n\nUse `--plan` to see the plan, or `--apply` to execute."
