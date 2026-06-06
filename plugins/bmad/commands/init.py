"""Slash command handler for /bmad:init — scaffold a new BMAD project.

Registered in ``plugins.bmad/__init__.py`` as ``bmad:init``.

Option B pattern: runs mechanical bootstrap FIRST, then injects the
rendered spec body into the conversation so the LLM continues planning.
"""

from __future__ import annotations

import logging
from pathlib import Path

from plugins.bmad.scripts.bmad_init import bootstrap, bootstrap_workspace

logger = logging.getLogger(__name__)


def _strip_flags(args: str) -> str:
    """Remove --force, --workspace, and --worktree specs from args for rendering."""
    parts = args.strip().split() if args else []
    cleaned: list[str] = []
    skip_next = False
    for part in parts:
        if skip_next:
            skip_next = False
            continue
        if part in ("--force", "--workspace"):
            continue
        if part == "--worktree":
            skip_next = True
            continue
        cleaned.append(part)
    return " ".join(cleaned)


def handler(ctx, args: str) -> str:
    """Handle the /bmad:init slash command.

    1. Parse structured args (--force, --workspace, --worktree)
    2. Run mechanical bootstrap (standard or workspace)
    3. Inject rendered spec body into conversation for LLM continuation
    4. Return short confirmation for overlay display
    """
    # ── Parse args ──────────────────────────────────────────────────
    args_list = args.strip().split() if args else []
    force = "--force" in args_list
    workspace_mode = "--workspace" in args_list

    worktree_specs: list[str] = []
    i = 0
    while i < len(args_list):
        if args_list[i] == "--worktree" and i + 1 < len(args_list):
            worktree_specs.append(args_list[i + 1])
            i += 2
        else:
            i += 1

    # ── Determine project directory ─────────────────────────────────
    raw_dir = getattr(ctx, "working_directory", None) or "."
    project_dir = Path(raw_dir).resolve()

    # ── Gather project info ─────────────────────────────────────────
    project_name = getattr(ctx, "project_name", None) or project_dir.name
    user_name = getattr(ctx, "user_name", None) or ""
    project_level = int(getattr(ctx, "project_level", 1))
    project_type = getattr(ctx, "project_type", "other")

    # ── Phase 1: Mechanical bootstrap ───────────────────────────────
    if workspace_mode:
        if not worktree_specs:
            return (
                "❌ `--workspace` requires at least one `--worktree NAME:UPSTREAM:BRANCH`.\n\n"
                "Example: `/bmad:init --workspace --worktree hermes-agent:~/usr-local/hermes:main`"
            )

        worktrees: list[dict[str, str]] = []
        for spec in worktree_specs:
            parts = spec.split(":")
            if len(parts) != 3:
                return (
                    f"❌ Invalid `--worktree` format: `{spec}`\n\n"
                    "Expected: `NAME:UPSTREAM:BRANCH` (e.g. `hermes-agent:~/usr-local/hermes:main`)"
                )
            worktrees.append({
                "name": parts[0],
                "upstream": parts[1],
                "branch": parts[2],
            })

        try:
            if force:
                config_path = project_dir / "bmad" / "config.yaml"
                if config_path.exists():
                    config_path.unlink()

            bootstrap_workspace(
                project_dir,
                project_name=project_name,
                worktrees=worktrees,
                project_type=project_type,
                project_level=project_level,
                user_name=user_name,
            )
        except RuntimeError as exc:
            return f"⚠️  {exc}\n\nUse `/bmad:init --force --workspace ...` to reinitialize."
        except ValueError as exc:
            return f"❌ {exc}"
        except Exception as exc:
            return f"❌ Failed to initialize workspace: {exc}"
    else:
        try:
            bootstrap(
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

    # ── Phase 2: Inject rendered spec body for LLM continuation ─────
    try:
        from plugins.bmad.lib.spec_parser import parse_command_body
        from plugins.bmad.lib.render import render_command

        spec_path = Path(__file__).with_name("init.md")
        body = spec_path.read_text()
        spec, body_text = parse_command_body(body)
        clean_args = _strip_flags(args)
        rendered = render_command(spec, body_text, args=clean_args, ctx=ctx)

        # Inject into conversation so the LLM continues planning
        ctx.inject_message(rendered)
    except Exception as exc:
        logger.warning("bmad:init: failed to render/inject spec body: %s", exc)

    # Return short confirmation for overlay/pager display
    return f"✅ BMAD project initialized at `{project_dir}`"
