"""Slash command handler for /bmad:dashboard — rich project dashboard.

Displays an overview of the BMAD project with phase status,
artifact links, and actionable next steps. Hermes-only feature (FR-16).
"""

from __future__ import annotations

from pathlib import Path


def handler(ctx, args: str) -> str:
    """Show BMAD project dashboard."""
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
        f"## 📊 {project} Dashboard",
        f"**Level:** {level} | **Updated:** {last_updated}",
        "",
        "### Phase Overview",
        "",
    ]

    phases_state = state.get("phases", {})
    rules = phases.PhaseRules(level)
    required = rules.required_slots()
    completed = 0
    total = 0

    for phase_name in phases.PHASE_ORDER:
        slots = phases_state.get(phase_name, {})
        required_slots = required.get(phase_name, [])
        all_slots = set(list(slots.keys()) + required_slots)
        total += len(all_slots)

        line = f"**{phase_name.capitalize()}:**"
        parts = []
        for slot_name in sorted(all_slots):
            status_val = slots.get(slot_name, "not-started")
            emoji = _status_emoji(status_val)
            is_required = slot_name in required_slots
            marker = "★" if is_required else "·"
            parts.append(f"{emoji} {marker}{slot_name}")
            if status_val == "complete":
                completed += 1

        lines.append(f"  {' '.join(parts)}")
        lines.append("")

    # Progress bar
    pct = int((completed / max(total, 1)) * 100) if total else 0
    bar_len = 20
    filled = int(bar_len * pct / 100)
    bar = "█" * filled + "░" * (bar_len - filled)
    lines.append(f"**Progress:** {bar} {completed}/{total} ({pct}%)")
    lines.append("")
    # Discovered artifacts
    lines.append("### Artifacts")
    artifact_count = _count_artifacts(project_dir)
    if artifact_count:
        for path, mtime in artifact_count:
            lines.append(f"  - `{path}` ({mtime})")
    else:
        lines.append("  _No artifacts yet — start with `/bmad:init`_")
    lines.append("")

    # Section 3: Sub-agent activity
    lines.append("### 🤖 Sub-Agent Activity")
    _append_subagent_activity(lines, project_dir)
    lines.append("")

    # Next action
    nxt = phases.next_required_slot(state, level)
    if nxt:
        lines.append(f"➡️ **Next action:** `/bmad:{nxt['command']}`")
    else:
        lines.append("✅ **All required slots complete!**")

    return "\n".join(lines)


def _status_emoji(status: str) -> str:
    return {
        "complete": "✅",
        "in-progress": "🔄",
        "required": "📌",
        "optional": "⬜",
        "not-started": "⬜",
    }.get(status, "❓")


def _count_artifacts(project_dir: Path) -> list[tuple[str, str]]:
    """List discovered BMAD artifacts with their last-modified dates."""
    results = []
    patterns = [
        project_dir / "planning-artifacts",
        project_dir / "implementation-artifacts",
    ]
    for base in patterns:
        if base.exists():
            for f in sorted(base.rglob("*")):
                if f.is_file() and f.suffix in (".md", ".yaml", ".csv"):
                    rel = f.relative_to(project_dir)
                    mtime = _format_mtime(f.stat().st_mtime)
                    results.append((str(rel), mtime))
    return results[:12]  # Top 12


def _format_mtime(st_mtime: float) -> str:
    from datetime import datetime
    return datetime.fromtimestamp(st_mtime).strftime("%m-%d %H:%M")


def _append_subagent_activity(lines: list[str], project_dir: Path) -> None:
    """Append last 10 sub-agent log entries to *lines*.

    Safe to call when the log doesn't exist yet — shows a friendly
    "no activity" message instead of failing.
    """
    from plugins.bmad.lib import subagent_log

    try:
        entries = subagent_log.read_recent(project_dir, limit=10)
    except Exception:
        lines.append("  _Unable to read sub-agent activity log._")
        return

    if not entries:
        lines.append("  _No sub-agent activity yet._")
        return

    for entry in reversed(entries):
        ts = entry.get("timestamp", "?")
        goal = entry.get("goal", "?")
        status = entry.get("status", "?")
        parent = entry.get("parent_skill", "?")
        task_id = entry.get("task_id", "?")[:8]

        emoji = {"success": "✅", "failure": "❌", "timeout": "⏰", "in_progress": "🔄"}.get(
            status, "❓"
        )
        lines.append(
            f"  {emoji} `{ts}` **{goal[:80]}** "
            f"— _{parent}_ (task `{task_id}…`, {status})"
        )
