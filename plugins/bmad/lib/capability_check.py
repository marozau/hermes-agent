"""Workspace-mode capability checker for provision-profiles (Story 6.6).

Extends the provisioning protocol to inventory each worktree's capabilities
and cross-check against DAG node requirements.

Hard invariant: WI-3 — prevents assigning a node to a worktree that can't
satisfy its required_capabilities.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..lib.config import WorkspaceConfig, WorktreeSpec

logger = logging.getLogger(__name__)

# Default threshold for stale-worktree warning (AC-6.6.3)
DEFAULT_STALE_THRESHOLD = 50


def inventory_worktree_capabilities(worktree_path: Path) -> list[str]:
    """Detect capabilities available in a worktree directory.

    Checks for common tools and languages by probing the filesystem
    and running version commands.
    """
    caps: list[str] = []

    # Python
    if shutil.which("python3") or shutil.which("python"):
        caps.append("python3")
    if (worktree_path / "requirements.txt").exists() or \
       (worktree_path / "pyproject.toml").exists() or \
       (worktree_path / "setup.py").exists():
        caps.append("python-project")
    if shutil.which("pytest"):
        caps.append("pytest")

    # Node/JS
    if shutil.which("node"):
        caps.append("node")
    if (worktree_path / "package.json").exists():
        caps.append("node-project")
    if shutil.which("npm"):
        caps.append("npm")
    if shutil.which("pnpm"):
        caps.append("pnpm")

    # Go
    if shutil.which("go"):
        caps.append("go")
    if (worktree_path / "go.mod").exists():
        caps.append("go-project")
    if shutil.which("golangci-lint"):
        caps.append("golangci-lint")

    # Rust
    if shutil.which("cargo"):
        caps.append("rust")
    if (worktree_path / "Cargo.toml").exists():
        caps.append("rust-project")

    # Git (always available in worktrees)
    caps.append("git")

    return sorted(set(caps))


def check_worktree_capabilities(
    worktree_name: str,
    worktree_path: Path,
    required_caps: list[str],
) -> list[str]:
    """Check if a worktree satisfies required capabilities.

    Returns a list of missing capabilities (empty if all satisfied).
    """
    if not worktree_path.exists():
        return [f"worktree '{worktree_name}' path does not exist: {worktree_path}"]

    available = set(inventory_worktree_capabilities(worktree_path))
    missing = [cap for cap in required_caps if cap not in available]
    return missing


def check_dag_worktree_capabilities(
    dag: dict[str, Any],
    workspace_root: Path,
    ws_config: WorkspaceConfig,
) -> list[dict[str, Any]]:
    """Cross-check DAG nodes' required_capabilities against worktree inventory.

    Returns a list of mismatch reports. Each report is a dict with:
    - node_id: the DAG node ID
    - worktree: the worktree name
    - required: list of required capabilities
    - missing: list of missing capabilities
    """
    mismatches: list[dict[str, Any]] = []

    for node in dag.get("nodes", []):
        worktree_name = node.get("worktree")
        if worktree_name is None:
            continue  # No worktree = workspace root, skip

        required = node.get("required_capabilities", [])
        if not required:
            continue

        # Find worktree path
        wt_path = None
        for wt in ws_config.worktrees:
            if wt.name == worktree_name:
                wt_path = workspace_root / wt.path
                break

        if wt_path is None:
            mismatches.append({
                "node_id": node.get("id", "?"),
                "worktree": worktree_name,
                "required": required,
                "missing": required,
                "error": f"unknown worktree '{worktree_name}'",
            })
            continue

        missing = check_worktree_capabilities(worktree_name, wt_path, required)
        if missing:
            mismatches.append({
                "node_id": node.get("id", "?"),
                "worktree": worktree_name,
                "required": required,
                "missing": missing,
            })

    return mismatches


def check_stale_worktrees(
    workspace_root: Path,
    ws_config: WorkspaceConfig,
    stale_threshold: int = DEFAULT_STALE_THRESHOLD,
) -> list[dict[str, Any]]:
    """Check for stale worktree branches (AC-6.6.3).

    Returns a list of warnings for worktrees whose branch has diverged
    from upstream main by more than *stale_threshold* commits.
    """
    warnings: list[dict[str, Any]] = []

    for wt in ws_config.worktrees:
        wt_path = workspace_root / wt.path
        if not wt_path.exists():
            continue

        try:
            # Get commit count between worktree branch and upstream main
            result = subprocess.run(
                ["git", "rev-list", "--count", f"HEAD..origin/main"],
                cwd=str(wt_path),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                behind = int(result.stdout.strip())
                if behind > stale_threshold:
                    warnings.append({
                        "worktree": wt.name,
                        "branch": wt.branch,
                        "commits_behind": behind,
                        "threshold": stale_threshold,
                        "message": (
                            f"Worktree '{wt.name}' is {behind} commits behind "
                            f"upstream main (threshold: {stale_threshold}). "
                            f"Consider: git fetch && git status"
                        ),
                    })
        except (subprocess.SubprocessError, ValueError):
            pass  # Non-fatal

    return warnings


def generate_capability_report(
    workspace_root: Path,
    ws_config: WorkspaceConfig,
) -> dict[str, Any]:
    """Generate a deterministic capability report for all worktrees (AC-6.6.5).

    Returns a dict with per-worktree capabilities. No timestamps in body.
    """
    report: dict[str, Any] = {
        "worktrees": {},
    }

    for wt in ws_config.worktrees:
        wt_path = workspace_root / wt.path
        caps = inventory_worktree_capabilities(wt_path) if wt_path.exists() else []
        report["worktrees"][wt.name] = {
            "path": str(wt.path),
            "capabilities": caps,
        }

    return report
