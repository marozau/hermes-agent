"""DAG data model with multi-type nodes, edges, and acyclicity enforcement.

Supports three node types: task, gate, sub-workflow.
Enforces acyclicity at insertion time via cycle detection.
Persists state to YAML for resumability.
"""

from __future__ import annotations

import enum
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)


# ── Enums ────────────────────────────────────────────────────────────────────


class NodeType(str, enum.Enum):
    TASK = "task"
    GATE = "gate"
    SUBWORKFLOW = "subworkflow"


class NodeState(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED_AT_GATE = "blocked-at-gate"
    SKIPPED = "skipped"


class GateDecision(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    OVERRIDE = "override"


# ── Pydantic Models ──────────────────────────────────────────────────────────


class GateCondition(BaseModel):
    """Configurable gate evaluation logic."""
    model_config = ConfigDict(frozen=False)

    type: str = "manual"  # manual | script | threshold | artifact_check
    description: str = ""
    script: Optional[str] = None  # Shell command to evaluate
    threshold: Optional[float] = None  # Numeric threshold
    threshold_field: Optional[str] = None  # Field to compare against
    threshold_operator: str = ">="  # >= | <= | == | != | > | <
    required_approvals: int = 1  # Number of approvals needed
    timeout_seconds: int = 86400  # 24h default
    on_failure: str = "block"  # block | skip | fail


class DAGNode(BaseModel):
    """A single node in the DAG."""
    model_config = ConfigDict(frozen=False)

    id: str
    type: NodeType = NodeType.TASK
    worktree: Optional[str] = None
    state: NodeState = NodeState.PENDING
    dependencies: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    description: str = ""

    # Gate-specific fields
    gate_condition: Optional[GateCondition] = None
    gate_decision: GateDecision = GateDecision.PENDING
    gate_decided_by: Optional[str] = None
    gate_decided_at: Optional[str] = None

    # Execution tracking
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    output: Optional[str] = None

    # Sub-workflow specific
    sub_dag_id: Optional[str] = None

    # Deadline
    deadline: Optional[str] = None  # ISO 8601


class DAGDefinition(BaseModel):
    """A complete DAG definition with nodes and metadata."""
    model_config = ConfigDict(frozen=False)

    id: str
    workspace_id: str
    name: str = ""
    description: str = ""
    nodes: list[DAGNode] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    deadline: Optional[str] = None  # Per-DAG deadline
    version: int = 0  # Optimistic locking version


class Workspace(BaseModel):
    """A workspace that owns one or more DAG instances."""
    model_config = ConfigDict(frozen=False)

    id: str
    name: str
    path: str
    description: str = ""
    created_at: str = ""
    updated_at: str = ""
    status: str = "active"  # active | archived
    dag_ids: list[str] = Field(default_factory=list)


# ── DAG Graph Operations ─────────────────────────────────────────────────────


class DAGValidationError(Exception):
    """Raised when DAG validation fails."""
    pass


def validate_dag_acyclicity(nodes: list[DAGNode]) -> list[str]:
    """Validate that the DAG has no cycles using Kahn's algorithm.

    Returns list of error messages (empty if valid).
    """
    errors: list[str] = []
    node_ids = {n.id for n in nodes}

    # Check all dependency references exist
    for node in nodes:
        for dep in node.dependencies:
            if dep not in node_ids:
                errors.append(f"Node '{node.id}' depends on unknown node '{dep}'")

    if errors:
        return errors

    # Kahn's algorithm for cycle detection
    in_degree: dict[str, int] = {n.id: 0 for n in nodes}
    adj: dict[str, list[str]] = {n.id: [] for n in nodes}

    for node in nodes:
        for dep in node.dependencies:
            adj[dep].append(node.id)
            in_degree[node.id] += 1

    queue = deque(nid for nid, deg in in_degree.items() if deg == 0)
    visited = []

    while queue:
        nid = queue.popleft()
        visited.append(nid)
        for neighbor in adj[nid]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(visited) != len(nodes):
        cycle_nodes = [n.id for n in nodes if n.id not in visited]
        errors.append(f"Cycle detected involving nodes: {cycle_nodes}")

    return errors


def topological_sort(nodes: list[DAGNode]) -> list[DAGNode]:
    """Topological sort of DAG nodes. Raises DAGValidationError on cycle."""
    errors = validate_dag_acyclicity(nodes)
    if errors:
        raise DAGValidationError("; ".join(errors))

    node_map = {n.id: n for n in nodes}
    in_degree: dict[str, int] = {n.id: 0 for n in nodes}
    adj: dict[str, list[str]] = {n.id: [] for n in nodes}

    for node in nodes:
        for dep in node.dependencies:
            adj[dep].append(node.id)
            in_degree[node.id] += 1

    queue = deque(nid for nid, deg in in_degree.items() if deg == 0)
    result: list[DAGNode] = []

    while queue:
        nid = queue.popleft()
        result.append(node_map[nid])
        for neighbor in adj[nid]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    return result


def add_node(dag: DAGDefinition, node: DAGNode) -> DAGDefinition:
    """Add a node to a DAG, validating acyclicity.

    Raises DAGValidationError if adding the node would create a cycle.
    """
    # Check for duplicate ID
    existing_ids = {n.id for n in dag.nodes}
    if node.id in existing_ids:
        raise DAGValidationError(f"Duplicate node ID: '{node.id}'")

    # Check all dependencies exist
    for dep in node.dependencies:
        if dep not in existing_ids:
            raise DAGValidationError(f"Node '{node.id}' depends on unknown node '{dep}'")

    # Validate gate node has condition
    if node.type == NodeType.GATE and node.gate_condition is None:
        raise DAGValidationError(f"Gate node '{node.id}' must have a gate_condition")

    # Test acyclicity with the new node
    test_nodes = dag.nodes + [node]
    errors = validate_dag_acyclicity(test_nodes)
    if errors:
        raise DAGValidationError("; ".join(errors))

    dag.nodes.append(node)
    return dag


# ── State Persistence ─────────────────────────────────────────────────────────


def save_dag(dag: DAGDefinition, state_dir: Path) -> None:
    """Persist DAG state to YAML with atomic write and optimistic locking."""
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / f"{dag.id}.yaml"

    # Optimistic locking: read current version
    if state_file.exists():
        current = yaml.safe_load(state_file.read_text()) or {}
        if current.get("version", 0) > dag.version:
            raise DAGValidationError(
                f"Concurrent modification detected: file version {current['version']} "
                f"> in-memory version {dag.version}. Reload and retry."
            )

    dag.version += 1
    dag.updated_at = datetime.now(timezone.utc).isoformat()

    # Atomic write
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(state_dir), prefix=f".{dag.id}.", suffix=".yaml.tmp",
    )
    try:
        os.write(tmp_fd, yaml.safe_dump(dag.model_dump(mode="json"), sort_keys=False).encode())
        os.fsync(tmp_fd)
        os.close(tmp_fd)
        tmp_fd = -1
        os.replace(tmp_path, str(state_file))
        tmp_path = None
    finally:
        if tmp_fd >= 0:
            os.close(tmp_fd)
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def load_dag(dag_id: str, state_dir: Path) -> Optional[DAGDefinition]:
    """Load DAG state from YAML."""
    state_file = state_dir / f"{dag_id}.yaml"
    if not state_file.exists():
        return None
    raw = yaml.safe_load(state_file.read_text())
    return DAGDefinition(**raw)


def save_workspace(ws: Workspace, state_dir: Path) -> None:
    """Persist workspace state."""
    state_dir.mkdir(parents=True, exist_ok=True)
    ws_file = state_dir / f"workspace-{ws.id}.yaml"
    ws.updated_at = datetime.now(timezone.utc).isoformat()

    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(state_dir), prefix=f".ws-{ws.id}.", suffix=".yaml.tmp",
    )
    try:
        os.write(tmp_fd, yaml.safe_dump(ws.model_dump(mode="json"), sort_keys=False).encode())
        os.fsync(tmp_fd)
        os.close(tmp_fd)
        tmp_fd = -1
        os.replace(tmp_path, str(ws_file))
        tmp_path = None
    finally:
        if tmp_fd >= 0:
            os.close(tmp_fd)
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def load_workspace(ws_id: str, state_dir: Path) -> Optional[Workspace]:
    """Load workspace state."""
    ws_file = state_dir / f"workspace-{ws_id}.yaml"
    if not ws_file.exists():
        return None
    raw = yaml.safe_load(ws_file.read_text())
    return Workspace(**raw)


def list_workspaces(state_dir: Path) -> list[Workspace]:
    """List all workspaces in the state directory."""
    if not state_dir.exists():
        return []
    results = []
    for f in sorted(state_dir.glob("workspace-*.yaml")):
        raw = yaml.safe_load(f.read_text())
        results.append(Workspace(**raw))
    return results


def list_dags(state_dir: Path) -> list[DAGDefinition]:
    """List all DAGs in the state directory."""
    if not state_dir.exists():
        return []
    results = []
    for f in sorted(state_dir.glob("*.yaml")):
        if f.name.startswith("workspace-"):
            continue
        raw = yaml.safe_load(f.read_text())
        if "workspace_id" in raw:  # It's a DAG, not a workspace
            results.append(DAGDefinition(**raw))
    return results


# ── Deadline Awareness ────────────────────────────────────────────────────────


def check_deadline(dag: DAGDefinition) -> Optional[dict[str, Any]]:
    """Check if a DAG is approaching or has exceeded its deadline.

    Returns None if no deadline or within bounds, otherwise a warning dict.
    """
    if not dag.deadline:
        return None

    now = datetime.now(timezone.utc)
    try:
        deadline_dt = datetime.fromisoformat(dag.deadline)
        if deadline_dt.tzinfo is None:
            deadline_dt = deadline_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None

    remaining = (deadline_dt - now).total_seconds()

    if remaining < 0:
        return {
            "status": "exceeded",
            "message": f"DAG '{dag.id}' exceeded deadline by {abs(remaining):.0f}s",
            "deadline": dag.deadline,
            "remaining_seconds": remaining,
        }
    elif remaining < 3600:  # < 1 hour
        return {
            "status": "approaching",
            "message": f"DAG '{dag.id}' deadline in {remaining:.0f}s",
            "deadline": dag.deadline,
            "remaining_seconds": remaining,
        }

    return None


def check_node_deadline(node: DAGNode) -> Optional[dict[str, Any]]:
    """Check if a node is approaching or has exceeded its deadline."""
    if not node.deadline:
        return None

    now = datetime.now(timezone.utc)
    try:
        deadline_dt = datetime.fromisoformat(node.deadline)
        if deadline_dt.tzinfo is None:
            deadline_dt = deadline_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None

    remaining = (deadline_dt - now).total_seconds()

    if remaining < 0:
        return {
            "status": "exceeded",
            "node_id": node.id,
            "message": f"Node '{node.id}' exceeded deadline by {abs(remaining):.0f}s",
        }
    elif remaining < 3600:
        return {
            "status": "approaching",
            "node_id": node.id,
            "message": f"Node '{node.id}' deadline in {remaining:.0f}s",
        }

    return None
