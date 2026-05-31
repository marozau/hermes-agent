"""DAG runner with workspace-mode worktree dispatch (Story 6.5).

Extends the DAG orchestrator to support a ``worktree:`` field on DAG nodes.
When a node declares a worktree, the orchestrator executes it with cwd set
to the worktree directory.

Hard invariants enforced:
- WI-3: One worktree → one branch → one agent at a time (concurrency cap = 1)
- WI-4: Agents never merge upstream (no git push/merge/rebase from orchestrator)
"""

from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path
from typing import Any, Optional

from ..lib.config import WorkspaceConfig, load_workspace_config

logger = logging.getLogger(__name__)

# Per-worktree locks for WI-3 concurrency cap
_worktree_locks: dict[str, threading.Lock] = {}
_locks_mutex = threading.Lock()


def _get_worktree_lock(worktree_name: str) -> threading.Lock:
    """Get or create a lock for a worktree name (WI-3)."""
    with _locks_mutex:
        if worktree_name not in _worktree_locks:
            _worktree_locks[worktree_name] = threading.Lock()
        return _worktree_locks[worktree_name]


def validate_dag_node(
    node: dict[str, Any],
    ws_config: WorkspaceConfig,
) -> list[str]:
    """Validate a DAG node's worktree reference.

    Returns a list of error messages (empty if valid).
    """
    errors: list[str] = []
    worktree_name = node.get("worktree")

    if worktree_name is None:
        return errors  # No worktree = workspace root (AC-6.5.3)

    if not ws_config.workspace_mode:
        errors.append(
            f"Node '{node.get('id', '?')}' declares worktree '{worktree_name}' "
            f"but workspace_mode is false"
        )
        return errors

    # Check worktree exists in config
    wt_names = {wt.name for wt in ws_config.worktrees}
    if worktree_name not in wt_names:
        errors.append(
            f"Node '{node.get('id', '?')}' references unknown worktree '{worktree_name}'. "
            f"Known worktrees: {sorted(wt_names)}"
        )

    return errors


def resolve_node_cwd(
    node: dict[str, Any],
    workspace_root: Path,
    ws_config: WorkspaceConfig,
) -> Path:
    """Resolve the cwd for a DAG node execution.

    If the node has a ``worktree:`` field, returns ``workspace_root/worktree/<name>``.
    Otherwise returns the workspace root.
    """
    worktree_name = node.get("worktree")
    if worktree_name is None:
        return workspace_root

    for wt in ws_config.worktrees:
        if wt.name == worktree_name:
            return workspace_root / wt.path

    # Should not happen if validate_dag_node passed
    return workspace_root


def run_node(
    node: dict[str, Any],
    workspace_root: Path,
    ws_config: WorkspaceConfig,
    invocation: list[str],
    *,
    capture_output: bool = True,
    timeout: int = 300,
) -> subprocess.CompletedProcess:
    """Execute a DAG node with proper cwd and worktree locking.

    Parameters
    ----------
    node:
        DAG node dict with at least 'id' and optionally 'worktree'.
    workspace_root:
        Absolute path to the workspace root.
    ws_config:
        Workspace configuration.
    invocation:
        Command to execute (list of strings).
    capture_output:
        Whether to capture stdout/stderr.
    timeout:
        Timeout in seconds.

    Returns
    -------
    subprocess.CompletedProcess

    Notes
    -----
    - WI-3: If the node targets a worktree, acquires a per-worktree lock
      to enforce concurrency cap of 1.
    - WI-4: The orchestrator wrapper never emits git push/merge/rebase.
    """
    worktree_name = node.get("worktree")
    cwd = resolve_node_cwd(node, workspace_root, ws_config)

    if worktree_name:
        lock = _get_worktree_lock(worktree_name)
        logger.info(
            "[bmad:dag_runner] Node '%s' acquiring lock on worktree '%s'",
            node.get("id", "?"), worktree_name,
        )
        lock.acquire()
        try:
            logger.info(
                "[bmad:dag_runner] Node '%s' running in %s",
                node.get("id", "?"), cwd,
            )
            return subprocess.run(
                invocation,
                cwd=str(cwd),
                capture_output=capture_output,
                text=True,
                timeout=timeout,
            )
        finally:
            lock.release()
            logger.info(
                "[bmad:dag_runner] Node '%s' released lock on worktree '%s'",
                node.get("id", "?"), worktree_name,
            )
    else:
        logger.info(
            "[bmad:dag_runner] Node '%s' running in workspace root %s",
            node.get("id", "?"), cwd,
        )
        return subprocess.run(
            invocation,
            cwd=str(cwd),
            capture_output=capture_output,
            text=True,
            timeout=timeout,
        )


def validate_dag(
    dag: dict[str, Any],
    ws_config: WorkspaceConfig,
) -> list[str]:
    """Validate an entire DAG's worktree references.

    Returns a list of all error messages (empty if valid).
    """
    errors: list[str] = []
    for node in dag.get("nodes", []):
        errors.extend(validate_dag_node(node, ws_config))
    return errors
