"""Handler for /bmad:sprint-planning — create sprint plan."""

from __future__ import annotations

from pathlib import Path

COMMAND = "sprint-planning"


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

    body_path = Path(__file__).with_name("sprint-planning.md")
    body = body_path.read_text(encoding="utf-8")
    return body
