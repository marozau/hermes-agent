"""Gate evaluation engine — configurable evaluation logic for gate nodes.

Supports: manual, script, threshold, artifact_check evaluation types.
Can reference upstream task outputs and external data sources.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .dag_model import (
    DAGDefinition,
    DAGNode,
    GateCondition,
    GateDecision,
    NodeState,
    NodeType,
    check_deadline,
    check_node_deadline,
    save_dag,
)

logger = logging.getLogger(__name__)


class GateEvaluator:
    """Evaluates gate conditions and manages gate lifecycle."""

    def __init__(self, state_dir: Path, workspace_root: Path):
        self.state_dir = state_dir
        self.workspace_root = workspace_root

    def evaluate_gate(
        self,
        dag: DAGDefinition,
        node: DAGNode,
        upstream_outputs: dict[str, str],
    ) -> tuple[bool, str]:
        """Evaluate a gate node's condition.

        Parameters
        ----------
        dag:
            The DAG definition.
        node:
            The gate node to evaluate.
        upstream_outputs:
            Map of node_id → output from completed upstream nodes.

        Returns
        -------
        (passed: bool, reason: str)
        """
        if node.type != NodeType.GATE:
            return False, f"Node '{node.id}' is not a gate node"

        if node.gate_condition is None:
            return False, f"Gate node '{node.id}' has no condition configured"

        condition = node.gate_condition

        # Check deadline first
        deadline_warn = check_node_deadline(node)
        if deadline_warn and deadline_warn["status"] == "exceeded":
            if condition.on_failure == "fail":
                return False, f"Gate deadline exceeded: {deadline_warn['message']}"
            elif condition.on_failure == "skip":
                return True, f"Gate skipped due to deadline: {deadline_warn['message']}"

        # Check DAG-level deadline
        dag_deadline = check_deadline(dag)
        if dag_deadline and dag_deadline["status"] == "exceeded":
            logger.warning("[gate] %s", dag_deadline["message"])

        # Evaluate based on condition type
        if condition.type == "manual":
            return self._evaluate_manual(node)
        elif condition.type == "script":
            return self._evaluate_script(node, condition, upstream_outputs)
        elif condition.type == "threshold":
            return self._evaluate_threshold(node, condition, upstream_outputs)
        elif condition.type == "artifact_check":
            return self._evaluate_artifact_check(node, condition, upstream_outputs)
        else:
            return False, f"Unknown gate condition type: {condition.type}"

    def _evaluate_manual(self, node: DAGNode) -> tuple[bool, str]:
        """Manual gate: requires explicit approval."""
        if node.gate_decision == GateDecision.APPROVED:
            return True, f"Gate '{node.id}' manually approved by {node.gate_decided_by}"
        elif node.gate_decision == GateDecision.REJECTED:
            return False, f"Gate '{node.id}' manually rejected by {node.gate_decided_by}"
        elif node.gate_decision == GateDecision.OVERRIDE:
            return True, f"Gate '{node.id}' overridden by {node.gate_decided_by}"
        return False, f"Gate '{node.id}' awaiting manual approval"

    def _evaluate_script(
        self,
        node: DAGNode,
        condition: GateCondition,
        upstream_outputs: dict[str, str],
    ) -> tuple[bool, str]:
        """Script gate: runs a shell command and checks exit code."""
        if not condition.script:
            return False, f"Gate '{node.id}' has no script configured"

        env = os.environ.copy()
        env["GATE_NODE_ID"] = node.id
        env["WORKSPACE_ROOT"] = str(self.workspace_root)

        # Inject upstream outputs as env vars
        for node_id, output in upstream_outputs.items():
            safe_name = node_id.replace("-", "_").replace(" ", "_").upper()
            env[f"UPSTREAM_OUTPUT_{safe_name}"] = output

        try:
            result = subprocess.run(
                ["bash", "-c", condition.script],
                capture_output=True,
                text=True,
                timeout=condition.timeout_seconds,
                cwd=str(self.workspace_root),
                env=env,
            )
            if result.returncode == 0:
                return True, f"Gate '{node.id}' script passed: {result.stdout.strip()}"
            else:
                return False, f"Gate '{node.id}' script failed (rc={result.returncode}): {result.stderr.strip()}"
        except subprocess.TimeoutExpired:
            return False, f"Gate '{node.id}' script timed out after {condition.timeout_seconds}s"
        except Exception as e:
            return False, f"Gate '{node.id}' script error: {e}"

    def _evaluate_threshold(
        self,
        node: DAGNode,
        condition: GateCondition,
        upstream_outputs: dict[str, str],
    ) -> tuple[bool, str]:
        """Threshold gate: compares a numeric value against a threshold."""
        if condition.threshold is None:
            return False, f"Gate '{node.id}' has no threshold configured"

        # Get the value from upstream outputs
        value = None
        if condition.threshold_field:
            for node_id, output in upstream_outputs.items():
                if condition.threshold_field in output:
                    try:
                        # Try to extract numeric value
                        import json
                        data = json.loads(output)
                        if isinstance(data, dict) and condition.threshold_field in data:
                            value = float(data[condition.threshold_field])
                            break
                    except (json.JSONDecodeError, ValueError, TypeError):
                        pass

        if value is None:
            return False, f"Gate '{node.id}' could not extract '{condition.threshold_field}' from upstream outputs"

        op = condition.threshold_operator
        threshold = condition.threshold

        if op == ">=":
            passed = value >= threshold
        elif op == "<=":
            passed = value <= threshold
        elif op == "==":
            passed = abs(value - threshold) < 1e-9
        elif op == "!=":
            passed = abs(value - threshold) >= 1e-9
        elif op == ">":
            passed = value > threshold
        elif op == "<":
            passed = value < threshold
        else:
            return False, f"Gate '{node.id}' unknown operator: {op}"

        reason = f"Gate '{node.id}': {value} {op} {threshold} → {'PASS' if passed else 'FAIL'}"
        return passed, reason

    def _evaluate_artifact_check(
        self,
        node: DAGNode,
        condition: GateCondition,
        upstream_outputs: dict[str, str],
    ) -> tuple[bool, str]:
        """Artifact check gate: verifies an artifact exists and meets criteria."""
        if not condition.script:
            return False, f"Gate '{node.id}' has no artifact check script"

        # Reuse script evaluation with artifact-specific env
        return self._evaluate_script(node, condition, upstream_outputs)

    def approve_gate(
        self,
        dag: DAGDefinition,
        node_id: str,
        approver: str,
        reason: str = "",
    ) -> DAGDefinition:
        """Manually approve a gate node."""
        node = self._find_node(dag, node_id)
        if node is None:
            raise ValueError(f"Node '{node_id}' not found in DAG '{dag.id}'")
        if node.type != NodeType.GATE:
            raise ValueError(f"Node '{node_id}' is not a gate node")

        node.gate_decision = GateDecision.APPROVED
        node.gate_decided_by = approver
        node.gate_decided_at = datetime.now(timezone.utc).isoformat()
        node.state = NodeState.SUCCEEDED
        node.output = reason or f"Approved by {approver}"

        save_dag(dag, self.state_dir)
        return dag

    def reject_gate(
        self,
        dag: DAGDefinition,
        node_id: str,
        rejector: str,
        reason: str = "",
    ) -> DAGDefinition:
        """Manually reject a gate node."""
        node = self._find_node(dag, node_id)
        if node is None:
            raise ValueError(f"Node '{node_id}' not found in DAG '{dag.id}'")
        if node.type != NodeType.GATE:
            raise ValueError(f"Node '{node_id}' is not a gate node")

        node.gate_decision = GateDecision.REJECTED
        node.gate_decided_by = rejector
        node.gate_decided_at = datetime.now(timezone.utc).isoformat()
        node.state = NodeState.FAILED
        node.output = reason or f"Rejected by {rejector}"

        save_dag(dag, self.state_dir)
        return dag

    def override_gate(
        self,
        dag: DAGDefinition,
        node_id: str,
        overrider: str,
        reason: str = "",
    ) -> DAGDefinition:
        """Override a gate (force pass)."""
        node = self._find_node(dag, node_id)
        if node is None:
            raise ValueError(f"Node '{node_id}' not found in DAG '{dag.id}'")
        if node.type != NodeType.GATE:
            raise ValueError(f"Node '{node_id}' is not a gate node")

        node.gate_decision = GateDecision.OVERRIDE
        node.gate_decided_by = overrider
        node.gate_decided_at = datetime.now(timezone.utc).isoformat()
        node.state = NodeState.SUCCEEDED
        node.output = f"Overridden by {overrider}: {reason}" if reason else f"Overridden by {overrider}"

        save_dag(dag, self.state_dir)
        return dag

    def _find_node(self, dag: DAGDefinition, node_id: str) -> Optional[DAGNode]:
        for node in dag.nodes:
            if node.id == node_id:
                return node
        return None
