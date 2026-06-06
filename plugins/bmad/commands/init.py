"""Slash command handler for /bmad:init — scaffold a new BMAD project.

Registered in ``plugins.bmad/__init__.py`` as ``bmad:init``.

Supports two modes:
1. Standard: ``/bmad:init [--force]`` — single-repo project
2. Workspace: ``/bmad:init --workspace --worktree NAME:UPSTREAM:BRANCH ...``

The LLM reads the spec (init.md) and constructs the args from the user's
natural language description. This handler only parses structured args.
"""

from __future__ import annotations

from pathlib import Path

from plugins.bmad.scripts.bmad_init import bootstrap, bootstrap_workspace


def handler(ctx, args: str) -> str:
    """Handle the /bmad:init slash command.

    Parameters
    ----------
    ctx:
        Hermes command context — provides access to ``working_directory``
        (or similar) to determine the project root.
    args:
        Structured arguments.  Supports ``--force``, ``--workspace``,
        and ``--worktree NAME:UPSTREAM:BRANCH`` (repeatable).

    Returns
    -------
    str
        Human-readable result message (success or error) displayed to the user.
    """
    # ── Parse args ──────────────────────────────────────────────────
    args_list = args.strip().split() if args else []
    force = "--force" in args_list
    workspace_mode = "--workspace" in args_list

    # Parse --worktree NAME:UPSTREAM:BRANCH specs
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

    # ── Workspace mode ──────────────────────────────────────────────
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
            config = bootstrap_workspace(
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

        # ── Workspace success ───────────────────────────────────────
        lines = [
            f"✅ **BMAD workspace initialized** at `{project_dir}`",
            "",
            f"  - **Name:** {config['project_name']}",
            f"  - **Type:** {config['project_type']}",
            f"  - **Level:** {config['project_level']}",
            f"  - **User:** {config.get('user_name') or '(not set)'}",
            f"  - **Workspace mode:** enabled",
            "",
            "**Worktrees:**",
        ]
        for wt in config.get("worktrees", []):
            lines.append(f"  - `{wt['name']}` → `{wt['upstream']}` @ `{wt['branch']}`")
        lines.extend([
            "",
            "**Created:**",
            "  - `bmad/config.yaml` — workspace configuration",
            "  - `planning-artifacts/` — canonical plan (workspace root)",
            "  - `worktree/<name>/` — git worktrees",
            "  - `AGENTS.md` — agent orientation",
            "  - `CLAUDE.md` — symlink to AGENTS.md",
            "  - `WORKTREES.md` — session manifest",
            "",
            "Use `/bmad:help` to see available commands.",
        ])
        return "\n".join(lines)

    # ── Standard (non-workspace) mode ───────────────────────────────
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
