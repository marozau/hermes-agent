"""Slash command handler for /bmad:init — scaffold a new BMAD project.

Registered in ``plugins.bmad/__init__.py`` as ``bmad:init``.

Supports two modes:
1. Standard: ``/bmad:init [--force]`` — single-repo project
2. Workspace: ``/bmad:init [--workspace] --worktree NAME:UPSTREAM:BRANCH ...``
   OR natural language: ``/bmad:init i want to work on repo-a and repo-b``

Calls ``plugins.bmad.scripts.bmad_init.bootstrap()`` or
``bootstrap_workspace()`` with the current working directory as the project root.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from plugins.bmad.scripts.bmad_init import bootstrap, bootstrap_workspace

# ── Workspace intent detection ──────────────────────────────────────────────

_WORKSPACE_KEYWORDS = re.compile(
    r"\b(workspace|worktree|multiple\s+repos|both\s+projects|two\s+projects"
    r"|across\s+\w+|several\s+projects)\b",
    re.IGNORECASE,
)

# Pattern: "repo-name (path)" or "repo-name (usr-local/repo-name)"
_REPOWithPath = re.compile(
    r"(\w[\w-]*)\s*\((?:~/)?(?:usr-local/)?([^)]+)\)",
    re.IGNORECASE,
)

# Pattern: "branch feat/xyz" or "on branch main"
_BRANCH_PATTERN = re.compile(
    r"\bbranch\s+([\w/._-]+)",
    re.IGNORECASE,
)

# Pattern: "to improve X" or "for X functionality" — not a repo, it's the goal
_GOAL_PATTERN = re.compile(
    r"\b(?:to\s+improve|for\s+|about\s+|related\s+to)\s+([\w-]+)",
    re.IGNORECASE,
)

_USR_LOCAL = Path.home().parent / "usr-local"  # ~/../usr-local → /Users/im/usr-local


def _detect_workspace_intent(args: str) -> dict[str, Any] | None:
    """Parse natural language to detect workspace intent and extract worktree specs.

    Returns None if no workspace intent detected.
    Returns dict with 'worktrees' and optional 'goal' if workspace intent found.
    """
    if not _WORKSPACE_KEYWORDS.search(args):
        return None

    # Extract goal (not a repo)
    goal_match = _GOAL_PATTERN.search(args)
    goal = goal_match.group(1) if goal_match else None

    # Extract repos with explicit paths: "hermes-agent (usr-local/hermes)"
    repos: list[dict[str, str]] = []
    for m in _REPOWithPath.finditer(args):
        name = m.group(1)
        path_part = m.group(2)
        # Skip if it's the goal
        if goal and name.lower() == goal.lower():
            continue
        upstream = str(_USR_LOCAL / path_part) if not path_part.startswith("/") else path_part
        repos.append({"name": name, "upstream": upstream})

    # Extract repos mentioned by name without explicit path: "hermes-agent and hermes-workspace"
    if len(repos) < 2:
        # Look for "X and Y" or "X, Y" patterns
        and_pattern = re.compile(
            r"\b([\w][\w-]*)\s+(?:and|,)\s+([\w][\w-]*)",
            re.IGNORECASE,
        )
        for m in and_pattern.finditer(args):
            name_a, name_b = m.group(1), m.group(2)
            # Skip if these are common words
            skip_words = {"i", "want", "to", "the", "a", "my", "we", "need", "both", "two", "multiple", "several", "across"}
            if name_a.lower() in skip_words or name_b.lower() in skip_words:
                continue
            if goal and name_a.lower() == goal.lower():
                continue
            if goal and name_b.lower() == goal.lower():
                continue
            # Check if already captured
            existing_names = {r["name"].lower() for r in repos}
            if name_a.lower() not in existing_names:
                upstream_a = str(_USR_LOCAL / name_a)
                repos.append({"name": name_a, "upstream": upstream_a})
            if name_b.lower() not in existing_names:
                upstream_b = str(_USR_LOCAL / name_b)
                repos.append({"name": name_b, "upstream": upstream_b})

    if len(repos) < 2:
        return None  # Need at least 2 repos for workspace mode

    # Extract branch
    branch_match = _BRANCH_PATTERN.search(args)
    branch = branch_match.group(1) if branch_match else "main"

    # Build worktree specs
    worktrees = []
    for r in repos:
        worktrees.append({
            "name": r["name"],
            "upstream": r["upstream"],
            "branch": branch,
        })

    return {"worktrees": worktrees, "goal": goal}


def handler(ctx, args: str) -> str:
    """Handle the /bmad:init slash command.

    Parameters
    ----------
    ctx:
        Hermes command context — provides access to ``working_directory``
        (or similar) to determine the project root.
    args:
        Optional arguments string.  Supports:
        - ``--force`` to bypass existing-config guard
        - ``--workspace`` to enable workspace mode
        - ``--worktree NAME:UPSTREAM:BRANCH`` (repeatable)
        - Natural language describing multiple repos (auto-detected)

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

    # ── Auto-detect workspace intent from natural language ───────────
    worktrees: list[dict[str, str]] = []
    if not workspace_mode and not worktree_specs and args:
        detected = _detect_workspace_intent(args)
        if detected:
            workspace_mode = True
            worktrees = detected["worktrees"]

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
        # Parse explicit --worktree specs if provided
        if worktree_specs:
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

        if not worktrees:
            return (
                "❌ Workspace mode requires at least 2 repos.\n\n"
                "Either use explicit flags:\n"
                "  `/bmad:init --workspace --worktree hermes-agent:~/usr-local/hermes:main --worktree hermes-workspace:~/usr-local/hermes-workspace:main`\n\n"
                "Or describe what you want:\n"
                "  `/bmad:init i want to work on hermes-agent (usr-local/hermes) and hermes-workspace (usr-local/hermes-workspace)`"
            )

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
