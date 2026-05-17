"""Slash command handler for /bmad:status — show current workflow status.

Displays the current BMAD workflow phase state from
planning-artifacts/workflow-status.yaml.
"""

from __future__ import annotations

from pathlib import Path


def handler(ctx, args: str) -> str:
    """Show BMAD workflow status."""
    raw_dir = getattr(ctx, "working_directory", None) or "."
    project_dir = Path(raw_dir).resolve()

    config_path = project_dir / "bmad" / "config.yaml"
    if not config_path.exists():
        return "⚠️  Not a BMAD project. Run `/bmad:init` to initialize."

    from plugins.bmad.lib import status as s
    from plugins.bmad.lib import phases

    try:
        state = s.load(project_dir)
    except FileNotFoundError:
        return "⚠️  No workflow status found. Run `/bmad:init` to initialize."

    level = state.get("level", 1)
    project = state.get("project", project_dir.name)
    last_updated = state.get("last_updated", "unknown")

    lines = [
        f"📊 **BMAD Status — {project}** (level {level})",
        f"Last updated: {last_updated}",
        "",
    ]

    phases_state = state.get("phases", {})
    phase_order = phases.PHASE_ORDER

    rules = phases.PhaseRules(level)
    required = rules.required_slots()

    for phase in phase_order:
        slots = phases_state.get(phase, {})
        required_slots = required.get(phase, [])
        
        line_parts = [f"**{phase.capitalize()}:**"]
        if not slots and not required_slots:
            line_parts.append(" _no slots_")
        else:
            # Show required slots first
            for slot_name in required_slots:
                status_val = slots.get(slot_name, "not-started")
                emoji = _status_emoji(status_val)
                line_parts.append(f" {emoji} {slot_name}")
            # Show optional/extra slots
            for slot_name, status_val in slots.items():
                if slot_name not in required_slots:
                    emoji = _status_emoji(status_val)
                    line_parts.append(f" {emoji} {slot_name}")

        lines.append(" ".join(line_parts))

    # Next step
    nxt = phases.next_required_slot(state, level)
    if nxt:
        lines.append("")
        lines.append(f"➡️  **Next:** `/bmad:{nxt['command']}`")
    else:
        lines.append("")
        lines.append("✅ **All slots complete!**")

    return "\n".join(lines)


def _status_emoji(status: str) -> str:
    return {
        "complete": "✅",
        "in-progress": "🔄",
        "required": "📌",
        "optional": "⬜",
        "not-started": "⬜",
    }.get(status, "❓")
