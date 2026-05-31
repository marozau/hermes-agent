"""Handler for /bmad:orchestrate — wave-based epic execution (Stories 7.4, 7.5).

Parses args, calls orchestrator.orchestrate_epic(), returns formatted report.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

COMMAND = "orchestrate"


def _parse_args(args: str) -> dict:
    """Parse orchestrate command arguments.

    Supports:
        /bmad:orchestrate <epic-number-or-path> [--resume] [--dry-run]
            [--story X.Y] [--wave N] [--max-retries N] [--no-halt]
            [--no-telemetry] [--prefect]
    """
    result: dict = {
        "epic": "",
        "resume": False,
        "dry_run": False,
        "story": "",
        "wave": -1,
        "max_retries": 2,
        "no_halt": False,
        "no_telemetry": False,
        "prefect": False,
        # V2 flags
        "ralph_loop": False,
        "next_epic": "",
        "background": False,
        "replan_on_failure": False,
    }

    tokens = args.strip().split()
    positional_done = False

    i = 0
    while i < len(tokens):
        tok = tokens[i]

        if tok == "--resume":
            result["resume"] = True
        elif tok == "--dry-run":
            result["dry_run"] = True
        elif tok == "--story" and i + 1 < len(tokens):
            i += 1
            result["story"] = tokens[i]
        elif tok.startswith("--story="):
            result["story"] = tok.split("=", 1)[1]
        elif tok == "--wave" and i + 1 < len(tokens):
            i += 1
            result["wave"] = int(tokens[i])
        elif tok.startswith("--wave="):
            result["wave"] = int(tok.split("=", 1)[1])
        elif tok == "--max-retries" and i + 1 < len(tokens):
            i += 1
            result["max_retries"] = int(tokens[i])
        elif tok.startswith("--max-retries="):
            result["max_retries"] = int(tok.split("=", 1)[1])
        elif tok == "--no-halt":
            result["no_halt"] = True
        elif tok == "--no-telemetry":
            result["no_telemetry"] = True
        elif tok == "--prefect":
            result["prefect"] = True
        # V2 flags
        elif tok == "--ralph-loop":
            result["ralph_loop"] = True
        elif tok == "--next-epic" and i + 1 < len(tokens):
            i += 1
            result["next_epic"] = tokens[i]
        elif tok == "--background":
            result["background"] = True
        elif tok == "--replan-on-failure":
            result["replan_on_failure"] = True
        elif not tok.startswith("--") and not positional_done:
            result["epic"] = tok
            positional_done = True

        i += 1

    return result


def _resolve_epic_path(project_dir: Path, epic_arg: str) -> Path | None:
    """Resolve epic argument to a file path.

    Accepts:
    - Direct path to epic file
    - Epic number (e.g. "7") → searches planning-artifacts/ for epic-7.md
    """
    if not epic_arg:
        return None

    # Direct path
    direct = Path(epic_arg)
    if not direct.is_absolute():
        direct = project_dir / epic_arg
    if direct.exists():
        return direct

    # Try as epic number
    if re.match(r"^\d+$", epic_arg):
        pa = project_dir / "planning-artifacts"
        patterns = [
            f"epic-{epic_arg}.md",
            f"epic_{epic_arg}.md",
            f"Epic-{epic_arg}.md",
        ]
        for pattern in patterns:
            candidate = pa / pattern
            if candidate.exists():
                return candidate

    return None


def handler(ctx, args: str) -> str:
    """Handle /bmad:orchestrate <epic-number-or-path> [flags].

    Orchestrates an epic's stories in wave-topological order with
    halt-on-failure, predicate evaluation, and resume support.
    """
    raw_dir = getattr(ctx, "working_directory", None) or "."
    project_dir = Path(raw_dir).resolve()

    config_path = project_dir / "bmad" / "config.yaml"
    if not config_path.exists():
        return "⚠️  Not a BMAD project. Run `/bmad:init` to initialize."

    parsed = _parse_args(args)
    epic_arg = parsed["epic"]

    if not epic_arg:
        return (
            "⚠️  Usage: `/bmad:orchestrate <epic-number-or-path>`\n"
            "  e.g. `/bmad:orchestrate 7` or `/bmad:orchestrate planning-artifacts/epic-7.md`\n"
            "\nFlags: [--resume] [--dry-run] [--story X.Y] [--wave N] "
            "[--max-retries N] [--no-halt] [--no-telemetry] [--prefect]"
        )

    epic_path = _resolve_epic_path(project_dir, epic_arg)
    if epic_path is None:
        return (
            f"⚠️  Epic not found: `{epic_arg}`\n"
            f"Searched: {project_dir / epic_arg}, "
            f"{project_dir / 'planning-artifacts' / f'epic-{epic_arg}.md'}"
        )

    from plugins.bmad.lib.orchestrator import (
        OrchestrateFlags,
        orchestrate_epic,
    )

    flags = OrchestrateFlags(
        resume=parsed["resume"],
        dry_run=parsed["dry_run"],
        story_filter=parsed["story"],
        wave_filter=parsed["wave"],
        max_retries=parsed["max_retries"],
        no_halt=parsed["no_halt"],
        no_telemetry=parsed["no_telemetry"],
        # V2 flags
        ralph_loop=parsed.get("ralph_loop", False),
        next_epic=parsed.get("next_epic", ""),
        background=parsed.get("background", False),
        replan_on_failure=parsed.get("replan_on_failure", False),
    )

    try:
        report = orchestrate_epic(ctx, project_dir, epic_path, flags)
    except RuntimeError as exc:
        return f"🚫 **Orchestrate blocked:** {exc}"
    except Exception as exc:
        logger.exception("[orchestrate] Unexpected error")
        return f"⚠️  Orchestrate failed: {exc.__class__.__name__}: {exc}"

    # Prefect export (optional)
    prefect_info = ""
    if parsed["prefect"]:
        try:
            from plugins.bmad.lib.prefect_bridge import export_prefect_flow

            output_path = project_dir / "orchestration" / f"epic-{report.epic_id}-flow.py"
            flow_path = export_prefect_flow(
                _reload_epic(epic_path), report, output_path
            )
            prefect_info = f"\n📦 Prefect flow exported: `{flow_path}`"
        except Exception as exc:
            prefect_info = f"\n⚠️  Prefect export failed: {exc}"

    # Format report
    return _format_report(report, flags) + prefect_info


def _reload_epic(epic_path: Path):
    """Reload epic spec for Prefect bridge."""
    from plugins.bmad.lib.epic_anchor import parse_epic_file

    return parse_epic_file(epic_path)


def _format_report(report, flags) -> str:
    """Format OrchestrateReport as markdown."""
    lines = [
        f"# Orchestrate Report: Epic {report.epic_id}",
        "",
    ]

    if flags.dry_run:
        lines.append("🔍 **DRY RUN** — no workers were dispatched\n")

    if report.halted:
        lines.append(f"🛑 **HALTED:** {report.halt_reason}\n")

    # Summary
    statuses = {}
    for r in report.results.values():
        statuses[r.status] = statuses.get(r.status, 0) + 1

    lines.append(f"**Stories:** {report.total_stories}  ")
    for status, count in sorted(statuses.items()):
        icon = {"succeeded": "✅", "failed": "❌", "skipped": "⏭️"}.get(status, "❓")
        lines.append(f"  {icon} {status}: {count}")
    lines.append("")

    # Wave breakdown
    for wave_idx, wave in enumerate(report.waves):
        lines.append(f"## Wave {wave_idx}")
        for story_id in wave:
            result = report.results.get(story_id)
            if result:
                icon = {
                    "succeeded": "✅",
                    "failed": "❌",
                    "skipped": "⏭️",
                    "halted": "🛑",
                }.get(result.status, "❓")
                pred_str = (
                    f" ({result.predicates_passed}/{result.predicates_total} predicates)"
                    if result.predicates_total > 0
                    else ""
                )
                attempt_str = f" [{result.attempts} attempts]" if result.attempts > 0 else ""
                error_str = f" — {result.error}" if result.error else ""
                lines.append(
                    f"- {icon} **{story_id}**{pred_str}{attempt_str}{error_str}"
                )
            else:
                lines.append(f"- ❓ **{story_id}** (no result)")
        lines.append("")

    return "\n".join(lines)
