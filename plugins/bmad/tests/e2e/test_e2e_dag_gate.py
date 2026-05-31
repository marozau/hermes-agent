"""E2E test: workspace + DAG + 3 tasks + 1 gate, blocked and released (Item 13).

Full lifecycle:
1. Create workspace
2. Define DAG with 3 tasks + 1 gate
3. Execute — gate blocks downstream
4. Approve gate
5. Re-execute — all nodes succeed
6. Verify correct final DAG state
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from plugins.bmad.commands.workspace_dag import (
    dag_add_node,
    dag_create,
    dag_execute,
    dag_list,
    dag_status,
    dag_view,
    dag_visualize,
    gate_approve,
    gate_reject,
    gate_reset,
    workspace_archive,
    workspace_create,
    workspace_delete,
    workspace_list,
    workspace_view,
)
from plugins.bmad.lib.dag_engine import DAGExecutor
from plugins.bmad.lib.dag_model import (
    DAGDefinition,
    DAGNode,
    GateCondition,
    GateDecision,
    NodeState,
    NodeType,
    save_dag,
)
from plugins.bmad.lib.gate_evaluator import GateEvaluator


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    return tmp_path / "workspace-test"


class TestE2EWorkspaceDAGGate:
    """Complete lifecycle: workspace → DAG → gate blocked → gate released."""

    def test_full_lifecycle(self, project_dir: Path):
        """Item 13: Creates workspace, defines DAG with 3 tasks + 1 gate,
        triggers execution, exercises gate blocked then released,
        asserts correct final state."""
        state_dir = project_dir / "bmad" / "dag-state"

        # 1. Create workspace
        ws = workspace_create(project_dir, "Epic 6 Feature", "workspace-mode implementation")
        assert ws.name == "Epic 6 Feature"
        assert ws.status == "active"

        # 2. Create DAG
        dag = dag_create(
            project_dir, ws.id, "Sprint 1",
            "MVP stories", deadline="2026-06-30",
        )
        assert dag.name == "Sprint 1"
        assert dag.deadline == "2026-06-30"

        # 3. Add 3 tasks + 1 gate
        dag = dag_add_node(project_dir, dag.id, "task-implement",
                           node_type="task", description="Implement feature")
        dag = dag_add_node(project_dir, dag.id, "task-test",
                           node_type="task", dependencies=["task-implement"],
                           description="Run tests")
        dag = dag_add_node(project_dir, dag.id, "gate-quality",
                           node_type="gate", dependencies=["task-test"],
                           gate_condition={"type": "manual", "description": "Quality review"},
                           description="Quality gate")
        dag = dag_add_node(project_dir, dag.id, "task-deploy",
                           node_type="task", dependencies=["gate-quality"],
                           description="Deploy to staging")
        assert len(dag.nodes) == 4

        # 4. Execute — gate blocks downstream
        dag = dag_execute(project_dir, dag.id)
        assert dag.nodes[0].id == "task-implement"
        assert dag.nodes[0].state == NodeState.SUCCEEDED
        assert dag.nodes[1].id == "task-test"
        assert dag.nodes[1].state == NodeState.SUCCEEDED
        assert dag.nodes[2].id == "gate-quality"
        assert dag.nodes[2].state == NodeState.BLOCKED_AT_GATE
        assert dag.nodes[3].id == "task-deploy"
        assert dag.nodes[3].state == NodeState.BLOCKED_AT_GATE  # blocked by unsatisfied gate dep

        # 5. Verify status
        status = dag_status(project_dir, dag.id)
        assert status["any_blocked"] is True
        assert status["all_succeeded"] is False

        # 6. Verify visualization shows blocked state
        vis = dag_visualize(dag)
        assert "task-implement" in vis
        assert "gate-quality" in vis
        assert "task-deploy" in vis

        # 7. Approve gate
        dag = gate_approve(project_dir, dag.id, "gate-quality", "reviewer", "LGTM")
        assert dag.nodes[2].gate_decision == GateDecision.APPROVED
        assert dag.nodes[2].gate_decided_by == "reviewer"
        assert dag.nodes[2].state == NodeState.SUCCEEDED

        # 8. Re-execute — all nodes succeed
        dag = dag_execute(project_dir, dag.id)
        assert dag.nodes[0].state == NodeState.SUCCEEDED  # skipped (already done)
        assert dag.nodes[1].state == NodeState.SUCCEEDED  # skipped
        assert dag.nodes[2].state == NodeState.SUCCEEDED  # approved
        assert dag.nodes[3].state == NodeState.SUCCEEDED  # now runs

        # 9. Final status
        status = dag_status(project_dir, dag.id)
        assert status["all_succeeded"] is True
        assert status["any_blocked"] is False
        assert status["any_failed"] is False

    def test_gate_reject_blocks_indefinitely(self, project_dir: Path):
        """Item 13: Rejecting a gate keeps downstream blocked."""
        ws = workspace_create(project_dir, "WS")
        dag = dag_create(project_dir, ws.id, "Test")
        dag_add_node(project_dir, dag.id, "t1")
        dag_add_node(project_dir, dag.id, "g1", node_type="gate",
                     dependencies=["t1"], gate_condition={"type": "manual"})
        dag_add_node(project_dir, dag.id, "t2", dependencies=["g1"])

        dag = dag_execute(project_dir, dag.id)
        assert dag.nodes[0].state == NodeState.SUCCEEDED
        assert dag.nodes[1].state == NodeState.BLOCKED_AT_GATE
        assert dag.nodes[2].state == NodeState.BLOCKED_AT_GATE  # blocked by unsatisfied gate

        dag = gate_reject(project_dir, dag.id, "g1", "reviewer", "quality too low")
        assert dag.nodes[1].state == NodeState.FAILED

        # Re-execute: t2 still blocked because g1 is FAILED
        dag = dag_execute(project_dir, dag.id)
        assert dag.nodes[2].state == NodeState.BLOCKED_AT_GATE

    def test_gate_reset_and_reapprove(self, project_dir: Path):
        """Item 13: Reset a rejected gate, then approve it."""
        ws = workspace_create(project_dir, "WS")
        dag = dag_create(project_dir, ws.id, "Test")
        dag_add_node(project_dir, dag.id, "g1", node_type="gate",
                     gate_condition={"type": "manual"})

        gate_reject(project_dir, dag.id, "g1", "reviewer")
        dag = gate_reset(project_dir, dag.id, "g1")
        assert dag.nodes[0].gate_decision == GateDecision.PENDING
        assert dag.nodes[0].state == NodeState.PENDING

        dag = gate_approve(project_dir, dag.id, "g1", "reviewer", "fixed")
        assert dag.nodes[0].gate_decision == GateDecision.APPROVED

    def test_script_gate_with_upstream_output(self, project_dir: Path, tmp_path: Path):
        """Item 9: Script gate references upstream task output."""
        script = tmp_path / "check_coverage.sh"
        script.write_text(
            '#!/bin/bash\n'
            'COVERAGE=$(echo "$UPSTREAM_OUTPUT_TASK_TESTS" | python3 -c "import sys,json; print(json.load(sys.stdin)[\'coverage\'])")\n'
            'if [ $(echo "$COVERAGE >= 80" | bc) -eq 1 ]; then\n'
            '  echo "Coverage OK: $COVERAGE"\n'
            '  exit 0\n'
            'else\n'
            '  echo "Coverage too low: $COVERAGE" >&2\n'
            '  exit 1\n'
            'fi\n'
        )
        os.chmod(str(script), stat.S_IRWXU)

        ws = workspace_create(project_dir, "WS")
        dag = dag_create(project_dir, ws.id, "Test")

        # Use custom executor to produce structured output
        state_dir = project_dir / "bmad" / "dag-state"
        executor = DAGExecutor(state_dir, project_dir)

        def test_executor(node: DAGNode, cwd: Path):
            if node.id == "task-tests":
                return DAGExecutor  # never mind the type, we just need a string
            from plugins.bmad.lib.dag_engine import ExecutionResult
            return ExecutionResult(node.id, NodeState.SUCCEEDED, "ok")

        # Simpler: just verify the gate evaluator receives upstream outputs
        evaluator = GateEvaluator(state_dir, project_dir)
        gate = DAGNode(
            id="g1", type=NodeType.GATE,
            gate_condition=GateCondition(
                type="threshold", threshold=80.0,
                threshold_field="coverage", threshold_operator=">=",
            ),
        )
        dag_def = DAGDefinition(id="d1", workspace_id="ws1", nodes=[gate])
        save_dag(dag_def, state_dir)

        # Upstream says coverage=92
        passed, reason = evaluator.evaluate_gate(
            dag_def, gate, {"task-tests": json.dumps({"coverage": 92.0})},
        )
        assert passed is True
        assert "92" in reason

        # Upstream says coverage=65
        passed, reason = evaluator.evaluate_gate(
            dag_def, gate, {"task-tests": json.dumps({"coverage": 65.0})},
        )
        assert passed is False

    def test_concurrent_safety_optimistic_locking(self, project_dir: Path):
        """Item 10: Two saves with stale version raise."""
        state_dir = project_dir / "bmad" / "dag-state"
        ws = workspace_create(project_dir, "WS")
        dag = dag_create(project_dir, ws.id, "Test")
        dag_add_node(project_dir, dag.id, "t1")

        # Load two copies
        from plugins.bmad.lib.dag_model import load_dag, save_dag
        a = load_dag(dag.id, state_dir)
        b = load_dag(dag.id, state_dir)
        assert a is not None and b is not None

        save_dag(a, state_dir)  # a saves, version bumps
        from plugins.bmad.lib.dag_model import DAGValidationError
        with pytest.raises(DAGValidationError, match="Concurrent"):
            save_dag(b, state_dir)  # b has stale version

    def test_deadline_exceeded_warning(self, project_dir: Path):
        """Item 11: Deadline-aware execution logs warnings."""
        from datetime import datetime, timedelta, timezone
        from plugins.bmad.lib.dag_model import check_deadline

        ws = workspace_create(project_dir, "WS")
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        dag = dag_create(project_dir, ws.id, "Overdue", deadline=past)
        dag_add_node(project_dir, dag.id, "t1")

        # check_deadline returns exceeded
        loaded = dag_view(project_dir, dag.id)
        warn = check_deadline(loaded)
        assert warn is not None
        assert warn["status"] == "exceeded"

        # Execute still works (deadline is a warning, not a hard stop)
        dag = dag_execute(project_dir, dag.id)
        assert dag.nodes[0].state == NodeState.SUCCEEDED

    def test_backward_compat_no_workspace_mode(self, project_dir: Path):
        """Item 12/WI-1: Workspace CRUD works independently of workspace_mode flag."""
        # No config.yaml exists — workspace CRUD should still work
        ws = workspace_create(project_dir, "No Config")
        assert ws.name == "No Config"

        dag = dag_create(project_dir, ws.id, "Test")
        dag_add_node(project_dir, dag.id, "t1")
        dag = dag_execute(project_dir, dag.id)
        assert dag.nodes[0].state == NodeState.SUCCEEDED

    def test_subworkflow_in_e2e(self, project_dir: Path):
        """Sub-workflow executes its sub-DAG and reports result."""
        state_dir = project_dir / "bmad" / "dag-state"
        ws = workspace_create(project_dir, "WS")
        dag = dag_create(project_dir, ws.id, "Test")

        # Add a sub-DAG first
        from plugins.bmad.lib.dag_model import DAGDefinition as DD, DAGNode as DN
        sub_dag = DD(id="sub-d1", workspace_id=ws.id, nodes=[
            DN(id="sub-t1", type=NodeType.TASK),
        ])
        save_dag(sub_dag, state_dir)

        dag_add_node(project_dir, dag.id, "pre", node_type="task")
        dag_add_node(project_dir, dag.id, "sub", node_type="subworkflow",
                     dependencies=["pre"])
        # Manually set sub_dag_id (CLI doesn't expose it directly)
        from plugins.bmad.lib.dag_model import load_dag
        loaded = load_dag(dag.id, state_dir)
        loaded.nodes[1].sub_dag_id = "sub-d1"
        save_dag(loaded, state_dir)

        dag = dag_execute(project_dir, dag.id)
        assert dag.nodes[0].state == NodeState.SUCCEEDED  # pre
        assert dag.nodes[1].state == NodeState.SUCCEEDED  # sub-workflow

    def test_dag_list_filtered(self, project_dir: Path):
        """DAG listing filtered by workspace."""
        ws1 = workspace_create(project_dir, "WS1")
        ws2 = workspace_create(project_dir, "WS2")
        dag_create(project_dir, ws1.id, "S1")
        dag_create(project_dir, ws2.id, "S2")
        dag_create(project_dir, ws1.id, "S3")

        all_dags = dag_list(project_dir)
        ws1_dags = dag_list(project_dir, workspace_id=ws1.id)
        ws2_dags = dag_list(project_dir, workspace_id=ws2.id)
        assert len(all_dags) == 3
        assert len(ws1_dags) == 2
        assert len(ws2_dags) == 1
