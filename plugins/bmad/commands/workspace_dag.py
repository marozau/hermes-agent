"""CLI commands for workspace, DAG, and gate management.

Commands:
- bmad-workspace: create, list, view, update, archive, delete workspaces
- bmad-dag: create, list, view, execute, status DAGs
- bmad-gate: approve, reject, reset gates
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from ..lib.dag_model import (
    DAGDefinition,
    DAGNode,
    DAGValidationError,
    GateCondition,
    GateDecision,
    NodeState,
    NodeType,
    Workspace,
    add_node,
    list_dags,
    list_workspaces,
    load_dag,
    load_workspace,
    save_dag,
    save_workspace,
    topological_sort,
    validate_dag_acyclicity,
)
from ..lib.dag_engine import DAGExecutor
from ..lib.gate_evaluator import GateEvaluator

logger = logging.getLogger(__name__)


def get_state_dir(project_dir: Path) -> Path:
    """Get the DAG state directory for a project."""
    return project_dir / "bmad" / "dag-state"


# ── Workspace Commands ───────────────────────────────────────────────────────


def workspace_create(
    project_dir: Path,
    name: str,
    description: str = "",
) -> Workspace:
    """Create a new workspace."""
    state_dir = get_state_dir(project_dir)
    ws_id = f"ws-{uuid.uuid4().hex[:12]}"

    ws = Workspace(
        id=ws_id,
        name=name,
        path=str(project_dir),
        description=description,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    save_workspace(ws, state_dir)
    return ws


def workspace_list(project_dir: Path) -> list[Workspace]:
    """List all workspaces."""
    return list_workspaces(get_state_dir(project_dir))


def workspace_view(project_dir: Path, ws_id: str) -> Optional[Workspace]:
    """View a workspace."""
    return load_workspace(ws_id, get_state_dir(project_dir))


def workspace_update(
    project_dir: Path,
    ws_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> Optional[Workspace]:
    """Update a workspace."""
    ws = load_workspace(ws_id, get_state_dir(project_dir))
    if ws is None:
        return None
    if name:
        ws.name = name
    if description is not None:
        ws.description = description
    save_workspace(ws, get_state_dir(project_dir))
    return ws


def workspace_archive(project_dir: Path, ws_id: str) -> Optional[Workspace]:
    """Archive a workspace."""
    ws = load_workspace(ws_id, get_state_dir(project_dir))
    if ws is None:
        return None
    ws.status = "archived"
    save_workspace(ws, get_state_dir(project_dir))
    return ws


def workspace_delete(project_dir: Path, ws_id: str) -> bool:
    """Delete a workspace and its state file."""
    state_dir = get_state_dir(project_dir)
    ws_file = state_dir / f"workspace-{ws_id}.yaml"
    if ws_file.exists():
        ws_file.unlink()
        return True
    return False


# ── DAG Commands ─────────────────────────────────────────────────────────────


def dag_create(
    project_dir: Path,
    workspace_id: str,
    name: str,
    description: str = "",
    deadline: Optional[str] = None,
) -> DAGDefinition:
    """Create a new DAG in a workspace."""
    state_dir = get_state_dir(project_dir)
    dag_id = f"dag-{uuid.uuid4().hex[:12]}"

    dag = DAGDefinition(
        id=dag_id,
        workspace_id=workspace_id,
        name=name,
        description=description,
        deadline=deadline,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    save_dag(dag, state_dir)

    # Link to workspace
    ws = load_workspace(workspace_id, state_dir)
    if ws:
        ws.dag_ids.append(dag_id)
        save_workspace(ws, state_dir)

    return dag


def dag_list(project_dir: Path, workspace_id: Optional[str] = None) -> list[DAGDefinition]:
    """List all DAGs, optionally filtered by workspace."""
    dags = list_dags(get_state_dir(project_dir))
    if workspace_id:
        dags = [d for d in dags if d.workspace_id == workspace_id]
    return dags


def dag_view(project_dir: Path, dag_id: str) -> Optional[DAGDefinition]:
    """View a DAG."""
    return load_dag(dag_id, get_state_dir(project_dir))


def dag_add_node(
    project_dir: Path,
    dag_id: str,
    node_id: str,
    node_type: str = "task",
    worktree: Optional[str] = None,
    dependencies: Optional[list[str]] = None,
    description: str = "",
    gate_condition: Optional[dict] = None,
    deadline: Optional[str] = None,
) -> DAGDefinition:
    """Add a node to a DAG."""
    state_dir = get_state_dir(project_dir)
    dag = load_dag(dag_id, state_dir)
    if dag is None:
        raise ValueError(f"DAG '{dag_id}' not found")

    gate_cond = None
    if node_type == "gate" and gate_condition:
        gate_cond = GateCondition(**gate_condition)

    node = DAGNode(
        id=node_id,
        type=NodeType(node_type),
        worktree=worktree,
        dependencies=dependencies or [],
        description=description,
        gate_condition=gate_cond,
        deadline=deadline,
    )

    dag = add_node(dag, node)
    save_dag(dag, state_dir)
    return dag


def dag_execute(
    project_dir: Path,
    dag_id: str,
    *,
    dry_run: bool = False,
) -> DAGDefinition:
    """Execute a DAG."""
    state_dir = get_state_dir(project_dir)
    dag = load_dag(dag_id, state_dir)
    if dag is None:
        raise ValueError(f"DAG '{dag_id}' not found")

    executor = DAGExecutor(state_dir, project_dir)
    return executor.execute_dag(dag, dry_run=dry_run)


def dag_status(project_dir: Path, dag_id: str) -> dict[str, Any]:
    """Get DAG execution status."""
    state_dir = get_state_dir(project_dir)
    dag = load_dag(dag_id, state_dir)
    if dag is None:
        return {"error": f"DAG '{dag_id}' not found"}

    executor = DAGExecutor(state_dir, project_dir)
    return executor.get_execution_summary(dag)


# ── Gate Commands ────────────────────────────────────────────────────────────


def gate_approve(
    project_dir: Path,
    dag_id: str,
    node_id: str,
    approver: str = "user",
    reason: str = "",
) -> DAGDefinition:
    """Approve a gate node."""
    state_dir = get_state_dir(project_dir)
    dag = load_dag(dag_id, state_dir)
    if dag is None:
        raise ValueError(f"DAG '{dag_id}' not found")

    evaluator = GateEvaluator(state_dir, project_dir)
    return evaluator.approve_gate(dag, node_id, approver, reason)


def gate_reject(
    project_dir: Path,
    dag_id: str,
    node_id: str,
    rejector: str = "user",
    reason: str = "",
) -> DAGDefinition:
    """Reject a gate node."""
    state_dir = get_state_dir(project_dir)
    dag = load_dag(dag_id, state_dir)
    if dag is None:
        raise ValueError(f"DAG '{dag_id}' not found")

    evaluator = GateEvaluator(state_dir, project_dir)
    return evaluator.reject_gate(dag, node_id, rejector, reason)


def gate_override(
    project_dir: Path,
    dag_id: str,
    node_id: str,
    overrider: str = "user",
    reason: str = "",
) -> DAGDefinition:
    """Override a gate node (force pass)."""
    state_dir = get_state_dir(project_dir)
    dag = load_dag(dag_id, state_dir)
    if dag is None:
        raise ValueError(f"DAG '{dag_id}' not found")

    evaluator = GateEvaluator(state_dir, project_dir)
    return evaluator.override_gate(dag, node_id, overrider, reason)


def gate_reset(
    project_dir: Path,
    dag_id: str,
    node_id: str,
) -> DAGDefinition:
    """Reset a gate node to pending state."""
    state_dir = get_state_dir(project_dir)
    dag = load_dag(dag_id, state_dir)
    if dag is None:
        raise ValueError(f"DAG '{dag_id}' not found")

    for node in dag.nodes:
        if node.id == node_id:
            if node.type != NodeType.GATE:
                raise ValueError(f"Node '{node_id}' is not a gate node")
            node.gate_decision = GateDecision.PENDING
            node.gate_decided_by = None
            node.gate_decided_at = None
            node.state = NodeState.PENDING
            node.output = None
            node.error = None
            break
    else:
        raise ValueError(f"Node '{node_id}' not found")

    save_dag(dag, state_dir)
    return dag


# ── Visualization ────────────────────────────────────────────────────────────


def dag_visualize(dag: DAGDefinition) -> str:
    """Render a DAG as an ASCII graph."""
    if not dag.nodes:
        return f"DAG '{dag.id}' (empty)"

    lines = [f"DAG: {dag.name or dag.id}"]
    if dag.deadline:
        lines.append(f"Deadline: {dag.deadline}")
    lines.append("")

    # Build adjacency for display
    node_map = {n.id: n for n in dag.nodes}

    # Topological order for display
    try:
        sorted_nodes = topological_sort(dag.nodes)
    except DAGValidationError:
        sorted_nodes = dag.nodes

    state_icons = {
        NodeState.PENDING: "○",
        NodeState.RUNNING: "◐",
        NodeState.SUCCEEDED: "●",
        NodeState.FAILED: "✗",
        NodeState.BLOCKED_AT_GATE: "◆",
        NodeState.SKIPPED: "◌",
    }

    type_labels = {
        NodeType.TASK: "task",
        NodeType.GATE: "gate",
        NodeType.SUBWORKFLOW: "sub",
    }

    for i, node in enumerate(sorted_nodes):
        icon = state_icons.get(node.state, "?")
        type_label = type_labels.get(node.type, "?")
        deps = f" ← {','.join(node.dependencies)}" if node.dependencies else ""
        wt = f" [{node.worktree}]" if node.worktree else ""
        desc = f" — {node.description}" if node.description else ""

        line = f"  {icon} {node.id} ({type_label}){wt}{deps}{desc}"
        lines.append(line)

        # Show gate decision if applicable
        if node.type == NodeType.GATE:
            decision = node.gate_decision.value
            if node.gate_decided_by:
                decision += f" by {node.gate_decided_by}"
            lines.append(f"    └─ gate: {decision}")

        # Show error/output if failed
        if node.state == NodeState.FAILED and node.error:
            lines.append(f"    └─ error: {node.error}")
        elif node.state == NodeState.BLOCKED_AT_GATE and node.error:
            lines.append(f"    └─ blocked: {node.error}")

    # Summary
    lines.append("")
    states = {}
    for node in dag.nodes:
        states[node.state.value] = states.get(node.state.value, 0) + 1
    lines.append(f"Summary: {', '.join(f'{v} {k}' for k, v in sorted(states.items()))}")

    return "\n".join(lines)
