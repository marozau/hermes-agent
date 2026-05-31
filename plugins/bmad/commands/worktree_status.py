"""Worktree status command — /bmad:worktree-status (Story 6.7).

Manages the WORKTREES.md live session manifest. Supports:
- Read-only status display
- --claim to reserve a worktree for an agent
- --release to free a worktree
- --force to override a claim

Hard invariant: WI-3 — one worktree → one agent at a time.
"""

from __future__ import annotations

import fcntl
import logging
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)


def worktree_status(
    project_dir: Path,
    *,
    claim: Optional[str] = None,
    release: Optional[str] = None,
    task: str = "",
    force: bool = False,
    agent_id: str = "",
) -> dict[str, Any]:
    """Execute worktree-status command.

    Parameters
    ----------
    project_dir:
        Workspace root directory.
    claim:
        Worktree name to claim.
    release:
        Worktree name to release.
    task:
        Task description (used with --claim).
    force:
        Force override an existing claim.
    agent_id:
        Identifier for the claiming agent.

    Returns
    -------
    dict with keys:
        success: bool
        exit_code: int (0=ok, 2=collision)
        message: str
        table: str (markdown table for display)
    """
    worktrees_md = project_dir / "WORKTREES.md"
    config_path = project_dir / "bmad" / "config.yaml"

    if not config_path.exists():
        return {
            "success": False,
            "exit_code": 1,
            "message": "Not a BMAD project (no bmad/config.yaml)",
            "table": "",
        }

    raw_config = yaml.safe_load(config_path.read_text()) or {}
    if not raw_config.get("workspace_mode"):
        return {
            "success": False,
            "exit_code": 1,
            "message": "Not a workspace-mode project",
            "table": "",
        }

    worktrees_config = raw_config.get("worktrees", [])
    if not worktrees_config:
        return {
            "success": False,
            "exit_code": 1,
            "message": "No worktrees configured",
            "table": "",
        }

    # Read current WORKTREES.md state
    state = _read_worktrees_md(worktrees_md, worktrees_config)

    if claim:
        return _handle_claim(
            state, worktrees_md, claim, task, force, agent_id,
        )
    elif release:
        return _handle_release(
            state, worktrees_md, release, agent_id,
        )
    else:
        # Read-only display
        table = _render_table(state)
        return {
            "success": True,
            "exit_code": 0,
            "message": table,
            "table": table,
        }


def _read_worktrees_md(
    path: Path,
    worktrees_config: list[dict],
) -> list[dict[str, str]]:
    """Parse WORKTREES.md into a list of row dicts.

    If the file doesn't exist or is unparseable, returns default state
    from the config.
    """
    if not path.exists():
        return _default_state(worktrees_config)

    content = path.read_text()
    rows: list[dict[str, str]] = []

    # Parse markdown table rows
    for line in content.splitlines():
        line = line.strip()
        if not line.startswith("|") or line.startswith("|---"):
            continue
        cells = [c.strip().strip("`") for c in line.split("|")[1:-1]]
        if len(cells) >= 6:
            rows.append({
                "worktree": cells[0],
                "branch": cells[1],
                "agent": cells[2],
                "task": cells[3],
                "status": cells[4],
                "last_commit": cells[5],
            })

    if not rows:
        return _default_state(worktrees_config)

    return rows


def _default_state(worktrees_config: list[dict]) -> list[dict[str, str]]:
    """Generate default state from config."""
    rows = []
    for wt in worktrees_config:
        rows.append({
            "worktree": f"worktree/{wt['name']}",
            "branch": wt.get("branch", "-"),
            "agent": "-",
            "task": "-",
            "status": "idle",
            "last_commit": "-",
        })
    return rows


def _handle_claim(
    state: list[dict[str, str]],
    worktrees_md: Path,
    worktree_name: str,
    task: str,
    force: bool,
    agent_id: str,
) -> dict[str, Any]:
    """Handle --claim operation with atomic file locking (AC-6.7.6)."""
    target = f"worktree/{worktree_name}"

    # Find the row
    row = None
    for r in state:
        if r["worktree"] == target:
            row = r
            break

    if row is None:
        return {
            "success": False,
            "exit_code": 1,
            "message": f"Worktree '{worktree_name}' not found in WORKTREES.md",
            "table": "",
        }

    # Check collision (AC-6.7.5)
    if row["status"] == "in-progress" and row["agent"] != agent_id and row["agent"] != "-":
        if not force:
            return {
                "success": False,
                "exit_code": 2,
                "message": (
                    f"worktree already claimed by {row['agent']} "
                    f"for task {row['task']}"
                ),
                "table": _render_table(state),
            }
        else:
            logger.warning(
                "[bmad:worktree-status] Force-claiming '%s' from '%s'",
                worktree_name, row["agent"],
            )

    # Update the row
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    row["agent"] = agent_id or "agent"
    row["task"] = task or "-"
    row["status"] = "in-progress"

    # Atomic write with file lock (AC-6.7.6)
    _atomic_write_worktrees_md(worktrees_md, state)

    return {
        "success": True,
        "exit_code": 0,
        "message": f"Claimed '{worktree_name}' for task '{task}'",
        "table": _render_table(state),
    }


def _handle_release(
    state: list[dict[str, str]],
    worktrees_md: Path,
    worktree_name: str,
    agent_id: str,
) -> dict[str, Any]:
    """Handle --release operation."""
    target = f"worktree/{worktree_name}"

    row = None
    for r in state:
        if r["worktree"] == target:
            row = r
            break

    if row is None:
        return {
            "success": False,
            "exit_code": 1,
            "message": f"Worktree '{worktree_name}' not found in WORKTREES.md",
            "table": "",
        }

    row["agent"] = "-"
    row["task"] = "-"
    row["status"] = "idle"

    _atomic_write_worktrees_md(worktrees_md, state)

    return {
        "success": True,
        "exit_code": 0,
        "message": f"Released '{worktree_name}'",
        "table": _render_table(state),
    }


def _render_table(state: list[dict[str, str]]) -> str:
    """Render state as a markdown table."""
    lines = [
        "| Worktree | Branch | Agent | Task | Status | Last commit |",
        "|---|---|---|---|---|---|",
    ]
    for row in state:
        lines.append(
            f"| `{row['worktree']}` | `{row['branch']}` | "
            f"{row['agent']} | {row['task']} | "
            f"{row['status']} | {row['last_commit']} |"
        )
    return "\n".join(lines)


def _atomic_write_worktrees_md(
    path: Path,
    state: list[dict[str, str]],
) -> None:
    """Atomically write WORKTREES.md with file locking (AC-6.7.6).

    Uses fcntl.flock for inter-process coordination.
    """
    header = "# WORKTREES.md\n\n> Live session manifest.\n\n"
    table = _render_table(state)
    content = header + table + "\n"

    # Write to temp file, then atomic replace
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=".worktrees.", suffix=".md.tmp",
    )
    try:
        os.write(tmp_fd, content.encode())
        os.fsync(tmp_fd)
        os.close(tmp_fd)
        tmp_fd = -1

        # Lock and replace
        lock_path = path.with_suffix(".md.lock")
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            os.replace(tmp_path, str(path))
            tmp_path = None  # Don't clean up
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
    finally:
        if tmp_fd >= 0:
            os.close(tmp_fd)
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
