"""Slash command handler for /bmad:init — scaffold a new BMAD project.

Registered in ``plugins.bmad/__init__.py`` as ``bmad:init``.

Calls ``plugins.bmad.scripts.bmad_init.bootstrap()`` with the current
working directory as the project root.
"""

from __future__ import annotations

from pathlib import Path

from plugins.bmad.scripts.bmad_init import bootstrap


def handler(ctx, args: str) -> str:
    """Handle the /bmad:init slash command.

    Parameters
    ----------
    ctx:
        Hermes command context — provides access to ``working_directory``
        (or similar) to determine the project root.
    args:
        Optional arguments string.  Supports ``--force`` to bypass the
        existing-config guard.  All other args are passed through.

    Returns
    -------
    str
        Human-readable result message (success or error) displayed to the user.
    """
    # ── Parse args ──────────────────────────────────────────────────
    args_list = args.strip().split() if args else []
    force = "--force" in args_list

    # ── Determine project directory ─────────────────────────────────
    raw_dir = getattr(ctx, "working_directory", None) or "."
    project_dir = Path(raw_dir).resolve()

    # ── Gather project info ─────────────────────────────────────────
    # Try to obtain project name from the context (e.g. session metadata).
    # Fall back to the directory basename.
    project_name = getattr(ctx, "project_name", None) or project_dir.name
    user_name = getattr(ctx, "user_name", None) or ""

    # Default to level 1, other type
    project_level = int(getattr(ctx, "project_level", 1))
    project_type = getattr(ctx, "project_type", "other")

    # ── Bootstrap ───────────────────────────────────────────────────
    try:
        config = bootstrap(
            project_dir,
            project_name=project_name,
            project_type=project_type,
            project_level=project_level,
            user_name=user_name,
            force=force,
            interactive=False,
        )
    except RuntimeError:
        return (
            "⚠️  **bmad/config.yaml** already exists in this directory.\n\n"
            "Use `/bmad:init --force` to overwrite the existing configuration."
        )
    except Exception as exc:
        return f"❌ Failed to initialize BMAD project: {exc}"

    # ── Success ─────────────────────────────────────────────────────
    lines = [
        f"✅ **BMAD project initialized** at `{project_dir}`",
        "",
        f"  - **Name:** {config['project_name']}",
        f"  - **Type:** {config['project_type']}",
        f"  - **Level:** {config['project_level']}",
        f"  - **User:** {config['user_name'] or '(not set)'}",
        "",
        "**Created:**",
        "  - `bmad/config.yaml` — project configuration",
        "  - `planning-artifacts/workflow-status.yaml` — state ledger",
        "  - `planning-artifacts/research/`",
        "  - `implementation-artifacts/stories/`",
        "",
        "Use `/bmad:help` to see available commands.",
    ]
    return "\n".join(lines)
