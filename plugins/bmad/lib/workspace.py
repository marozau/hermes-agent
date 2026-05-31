"""Workspace-mode helpers for BMAD plugin (Stories 6.4, 6.5, 6.8).

Shared utilities for write-boundary enforcement, worktree resolution,
and runtime mirroring.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .config import WorkspaceConfig, WorktreeSpec, load_workspace_config

logger = logging.getLogger(__name__)


def get_workspace_config(ctx: Any) -> WorkspaceConfig:
    """Load workspace config from the project dir in ctx."""
    project_dir = _resolve_project_dir(ctx)
    if project_dir is None:
        return WorkspaceConfig()
    return load_workspace_config(project_dir)


def is_write_allowed(
    file_path: str,
    project_dir: Path,
    ws_config: WorkspaceConfig,
) -> bool:
    """Check if a write to file_path is within workspace boundaries.

    In workspace mode, writes are only allowed to:
    - planning-artifacts/ at the workspace root
    - worktree/<name>/ for any declared worktree
    - bmad/ at the workspace root (config files)

    Returns True if allowed, False if blocked (WI-2).
    """
    if not ws_config.workspace_mode:
        return True

    resolved = Path(file_path).resolve()
    root = project_dir.resolve()

    # Check planning-artifacts
    planning = root / "planning-artifacts"
    try:
        resolved.relative_to(planning)
        return True
    except ValueError:
        pass

    # Check bmad/ (config)
    bmad_dir = root / "bmad"
    try:
        resolved.relative_to(bmad_dir)
        return True
    except ValueError:
        pass

    # Check each worktree path
    for wt in ws_config.worktrees:
        wt_root = root / wt.path
        try:
            rel_to_wt = resolved.relative_to(wt_root.resolve())
            # R3-m5: Block writes to .git/ inside worktrees. The worktree's
            # .git is a FILE pointing to upstream's .git/worktrees/<name>/ —
            # unrestricted writes corrupt upstream.
            rel_str = str(rel_to_wt)
            if rel_str.startswith(".git") or rel_str == ".git":
                logger.warning(
                    "[bmad:workspace] Blocked write to .git/ inside worktree: %s",
                    file_path,
                )
                return False
            return True
        except ValueError:
            continue

    # Check workspace root files (AGENTS.md, CLAUDE.md, WORKTREES.md)
    if resolved.parent == root:
        return True

    return False


def find_worktree_for_path(
    file_path: str,
    project_dir: Path,
    ws_config: WorkspaceConfig,
) -> WorktreeSpec | None:
    """Find which worktree file_path belongs to, if any."""
    resolved = Path(file_path).resolve()
    root = project_dir.resolve()

    for wt in ws_config.worktrees:
        wt_root = (root / wt.path).resolve()
        try:
            resolved.relative_to(wt_root)
            return wt
        except ValueError:
            continue
    return None


def get_worktree_root(
    project_dir: Path,
    worktree_name: str,
    ws_config: WorkspaceConfig,
) -> Path | None:
    """Get the absolute path to a named worktree's root directory."""
    for wt in ws_config.worktrees:
        if wt.name == worktree_name:
            return (project_dir / wt.path).resolve()
    return None


def _resolve_project_dir(ctx: Any) -> Path | None:
    """Resolve project directory from a Hermes hook context."""
    if hasattr(ctx, "project_dir") and ctx.project_dir:
        return Path(ctx.project_dir)
    if hasattr(ctx, "working_directory") and ctx.working_directory:
        return Path(ctx.working_directory)
    return None
