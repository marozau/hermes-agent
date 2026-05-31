"""DAG execution engine with topological sort, gate hold/release, and state tracking.

Topologically sorts nodes, respects gate semantics, only proceeds past
a gate after its condition evaluates to true or manual override.
Persists state for resumability.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .dag_model import (
    DAGDefinition,
    DAGNode,
    DAGValidationError,
    GateDecision,
    NodeState,
    NodeType,
    check_deadline,
    check_node_deadline,
    load_dag,
    save_dag,
    topological_sort,
    validate_dag_acyclicity,
)
from .gate_evaluator import GateEvaluator

logger = logging.getLogger(__name__)


class ExecutionResult:
    """Result of a DAG execution step."""

    def __init__(self, node_id: str, state: NodeState, output: str = "", error: str = ""):
        self.node_id = node_id
        self.state = state
        self.output = output
        self.error = error
        self.timestamp = datetime.now(timezone.utc).isoformat()


class DAGExecutor:
    """Executes DAGs with topological ordering and gate semantics."""

    def __init__(
        self,
        state_dir: Path,
        workspace_root: Path,
        worktree_resolver: Optional[Callable[[str], Path]] = None,
    ):
        self.state_dir = state_dir
        self.workspace_root = workspace_root
        self.worktree_resolver = worktree_resolver
        self.gate_evaluator = GateEvaluator(state_dir, workspace_root)
        self._node_locks: dict[str, threading.Lock] = {}
        self._locks_mutex = threading.Lock()

    def get_node_lock(self, node_id: str) -> threading.Lock:
        """Get or create a lock for a node (concurrent safety)."""
        with self._locks_mutex:
            if node_id not in self._node_locks:
                self._node_locks[node_id] = threading.Lock()
            return self._node_locks[node_id]

    def execute_dag(
        self,
        dag: DAGDefinition,
        *,
        dry_run: bool = False,
        node_executor: Optional[Callable[[DAGNode, Path], ExecutionResult]] = None,
    ) -> DAGDefinition:
        """Execute all nodes in topological order, respecting gate semantics.

        Parameters
        ----------
        dag:
            The DAG to execute.
        dry_run:
            If True, only validate and plan execution order.
        node_executor:
            Custom executor for task nodes. If None, uses subprocess.

        Returns
        -------
        Updated DAG with final node states.
        """
        # Validate acyclicity
        errors = validate_dag_acyclicity(dag.nodes)
        if errors:
            raise DAGValidationError(f"DAG validation failed: {'; '.join(errors)}")

        # Topological sort
        sorted_nodes = topological_sort(dag.nodes)
        node_map = {n.id: n for n in dag.nodes}

        # Track upstream outputs for gate evaluation
        upstream_outputs: dict[str, str] = {}

        # Check DAG deadline
        deadline_warn = check_deadline(dag)
        if deadline_warn:
            logger.warning("[dag:executor] %s", deadline_warn["message"])

        if dry_run:
            logger.info("[dag:executor] Dry run — execution order: %s", [n.id for n in sorted_nodes])
            return dag

        # Execute in topological order
        for node in sorted_nodes:
            # Skip already completed nodes (for resumability)
            if node.state in (NodeState.SUCCEEDED, NodeState.SKIPPED):
                upstream_outputs[node.id] = node.output or ""
                continue

            # Check if all dependencies are satisfied
            deps_satisfied = self._check_dependencies(node, node_map)
            if not deps_satisfied:
                node.state = NodeState.BLOCKED_AT_GATE
                node.error = "Blocked: upstream dependency not satisfied"
                save_dag(dag, self.state_dir)
                continue

            # Execute based on node type
            if node.type == NodeType.GATE:
                self._execute_gate_node(dag, node, upstream_outputs)
            elif node.type == NodeType.SUBWORKFLOW:
                self._execute_subworkflow_node(dag, node, upstream_outputs)
            else:
                self._execute_task_node(dag, node, upstream_outputs, node_executor)

            upstream_outputs[node.id] = node.output or ""
            save_dag(dag, self.state_dir)

        return dag

    def _check_dependencies(self, node: DAGNode, node_map: dict[str, DAGNode]) -> bool:
        """Check if all dependencies are satisfied."""
        for dep_id in node.dependencies:
            dep = node_map.get(dep_id)
            if dep is None:
                return False
            if dep.state not in (NodeState.SUCCEEDED, NodeState.SKIPPED):
                return False
        return True

    def _execute_gate_node(
        self,
        dag: DAGDefinition,
        node: DAGNode,
        upstream_outputs: dict[str, str],
    ) -> None:
        """Execute a gate node with hold/release semantics."""
        node.state = NodeState.RUNNING
        node.started_at = datetime.now(timezone.utc).isoformat()

        logger.info("[dag:executor] Evaluating gate '%s'", node.id)

        passed, reason = self.gate_evaluator.evaluate_gate(dag, node, upstream_outputs)

        if passed:
            node.state = NodeState.SUCCEEDED
            node.output = reason
            logger.info("[dag:executor] Gate '%s' PASSED: %s", node.id, reason)
        else:
            node.state = NodeState.BLOCKED_AT_GATE
            node.error = reason
            logger.info("[dag:executor] Gate '%s' BLOCKED: %s", node.id, reason)

            # Check on_failure behavior
            if node.gate_condition and node.gate_condition.on_failure == "fail":
                node.state = NodeState.FAILED
            elif node.gate_condition and node.gate_condition.on_failure == "skip":
                node.state = NodeState.SKIPPED

        node.finished_at = datetime.now(timezone.utc).isoformat()

    def _execute_task_node(
        self,
        dag: DAGDefinition,
        node: DAGNode,
        upstream_outputs: dict[str, str],
        node_executor: Optional[Callable[[DAGNode, Path], ExecutionResult]] = None,
    ) -> None:
        """Execute a task node."""
        node.state = NodeState.RUNNING
        node.started_at = datetime.now(timezone.utc).isoformat()

        # Resolve working directory
        cwd = self.workspace_root
        if node.worktree and self.worktree_resolver:
            cwd = self.worktree_resolver(node.worktree)

        logger.info("[dag:executor] Running task '%s' in %s", node.id, cwd)

        if node_executor:
            result = node_executor(node, cwd)
            node.state = result.state
            node.output = result.output
            node.error = result.error
        else:
            try:
                proc = subprocess.run(
                    ["echo", f"task {node.id} placeholder"],
                    capture_output=True, text=True, timeout=300,
                    cwd=str(cwd),
                )
                if proc.returncode == 0:
                    node.state = NodeState.SUCCEEDED
                    node.output = proc.stdout.strip()
                else:
                    node.state = NodeState.FAILED
                    node.error = proc.stderr.strip()
            except Exception as e:
                node.state = NodeState.FAILED
                node.error = str(e)

        node.finished_at = datetime.now(timezone.utc).isoformat()

    def _execute_subworkflow_node(
        self,
        dag: DAGDefinition,
        node: DAGNode,
        upstream_outputs: dict[str, str],
    ) -> None:
        """Execute a sub-workflow node (loads and runs a sub-DAG)."""
        node.state = NodeState.RUNNING
        node.started_at = datetime.now(timezone.utc).isoformat()

        if not node.sub_dag_id:
            node.state = NodeState.FAILED
            node.error = f"Sub-workflow node '{node.id}' has no sub_dag_id"
            node.finished_at = datetime.now(timezone.utc).isoformat()
            return

        sub_dag = load_dag(node.sub_dag_id, self.state_dir)
        if sub_dag is None:
            node.state = NodeState.FAILED
            node.error = f"Sub-DAG '{node.sub_dag_id}' not found"
            node.finished_at = datetime.now(timezone.utc).isoformat()
            return

        try:
            sub_executor = DAGExecutor(self.state_dir, self.workspace_root, self.worktree_resolver)
            sub_dag = sub_executor.execute_dag(sub_dag)

            # Check if all sub-nodes succeeded
            all_ok = all(
                n.state in (NodeState.SUCCEEDED, NodeState.SKIPPED)
                for n in sub_dag.nodes
            )
            if all_ok:
                node.state = NodeState.SUCCEEDED
                node.output = f"Sub-workflow '{node.sub_dag_id}' completed successfully"
            else:
                node.state = NodeState.FAILED
                failed = [n.id for n in sub_dag.nodes if n.state == NodeState.FAILED]
                node.error = f"Sub-workflow '{node.sub_dag_id}' failed at: {failed}"
        except Exception as e:
            node.state = NodeState.FAILED
            node.error = f"Sub-workflow error: {e}"

        node.finished_at = datetime.now(timezone.utc).isoformat()

    def get_execution_summary(self, dag: DAGDefinition) -> dict[str, Any]:
        """Get a summary of DAG execution state."""
        states = {}
        for node in dag.nodes:
            states[node.state.value] = states.get(node.state.value, 0) + 1

        return {
            "dag_id": dag.id,
            "total_nodes": len(dag.nodes),
            "states": states,
            "all_succeeded": all(
                n.state in (NodeState.SUCCEEDED, NodeState.SKIPPED)
                for n in dag.nodes
            ),
            "any_failed": any(n.state == NodeState.FAILED for n in dag.nodes),
            "any_blocked": any(n.state == NodeState.BLOCKED_AT_GATE for n in dag.nodes),
            "deadline": check_deadline(dag),
        }
