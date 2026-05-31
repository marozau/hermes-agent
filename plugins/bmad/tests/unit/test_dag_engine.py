"""Tests for the DAG execution engine (Item 5 — topological sort + gate hold/release).

Verifies:
- Topological execution order
- Gate hold: engine blocks when gate is pending/manual
- Gate release: engine proceeds after gate approved
- State transitions: pending → running → succeeded/failed/blocked-at-gate
- Resumability: engine skips already-succeeded nodes
- Sub-workflow execution
- Node executor callback
- Execution summary
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from plugins.bmad.lib.dag_model import (
    DAGDefinition,
    DAGNode,
    DAGValidationError,
    GateCondition,
    GateDecision,
    NodeState,
    NodeType,
    save_dag,
)
from plugins.bmad.lib.dag_engine import DAGExecutor, ExecutionResult


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    d = tmp_path / "dag-state"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def executor(state_dir: Path, tmp_path: Path) -> DAGExecutor:
    return DAGExecutor(state_dir, tmp_path)


def _task(id: str, deps: list[str] | None = None, **kw) -> DAGNode:
    return DAGNode(id=id, type=NodeType.TASK, dependencies=deps or [], **kw)


def _gate(id: str, deps: list[str] | None = None, **kw) -> DAGNode:
    cond = GateCondition(**kw) if kw else GateCondition()
    return DAGNode(id=id, type=NodeType.GATE, dependencies=deps or [], gate_condition=cond)


def _dag(nodes: list[DAGNode]) -> DAGDefinition:
    return DAGDefinition(id="d1", workspace_id="ws1", nodes=nodes)


# ── Item 5a: Topological execution order ─────────────────────────────────────


class TestTopologicalExecution:
    def test_linear_chain_executes_in_order(self, executor):
        """t0 → t1 → t2 must execute in order."""
        execution_order: list[str] = []

        def track_executor(node: DAGNode, cwd: Path) -> ExecutionResult:
            execution_order.append(node.id)
            return ExecutionResult(node.id, NodeState.SUCCEEDED, "ok")

        t0 = _task("t0")
        t1 = _task("t1", ["t0"])
        t2 = _task("t2", ["t1"])
        dag = _dag([t0, t1, t2])

        executor.execute_dag(dag, node_executor=track_executor)
        assert execution_order == ["t0", "t1", "t2"]

    def test_diamond_executes_respecting_deps(self, executor):
        """t0 → (t1, t2) → t3."""
        execution_order: list[str] = []

        def track_executor(node: DAGNode, cwd: Path) -> ExecutionResult:
            execution_order.append(node.id)
            return ExecutionResult(node.id, NodeState.SUCCEEDED, "ok")

        t0 = _task("t0")
        t1 = _task("t1", ["t0"])
        t2 = _task("t2", ["t0"])
        t3 = _task("t3", ["t1", "t2"])
        dag = _dag([t0, t1, t2, t3])

        executor.execute_dag(dag, node_executor=track_executor)
        assert execution_order[0] == "t0"
        assert execution_order.index("t3") > execution_order.index("t1")
        assert execution_order.index("t3") > execution_order.index("t2")


# ── Item 5b: Gate hold/release ───────────────────────────────────────────────


class TestGateHoldRelease:
    def test_manual_gate_blocks_when_pending(self, executor):
        """Manual gate blocks downstream when not yet approved."""
        execution_order: list[str] = []

        def track_executor(node: DAGNode, cwd: Path) -> ExecutionResult:
            execution_order.append(node.id)
            return ExecutionResult(node.id, NodeState.SUCCEEDED, "ok")

        t0 = _task("t0")
        g0 = _gate("g0", ["t0"])
        t1 = _task("t1", ["g0"])
        dag = _dag([t0, g0, t1])

        executor.execute_dag(dag, node_executor=track_executor)

        # t0 ran, g0 was evaluated (and blocked), t1 was NOT reached
        assert "t0" in execution_order
        assert g0.state == NodeState.BLOCKED_AT_GATE
        assert t1.state == NodeState.BLOCKED_AT_GATE  # blocked by unsatisfied gate dep

    def test_manual_gate_release_after_approval(self, executor, state_dir):
        """After approving a gate and re-running, downstream proceeds."""
        execution_order: list[str] = []

        def track_executor(node: DAGNode, cwd: Path) -> ExecutionResult:
            execution_order.append(node.id)
            return ExecutionResult(node.id, NodeState.SUCCEEDED, "ok")

        t0 = _task("t0")
        g0 = _gate("g0", ["t0"])
        t1 = _task("t1", ["g0"])
        dag = _dag([t0, g0, t1])

        # First run: gate blocks
        executor.execute_dag(dag, node_executor=track_executor)
        assert g0.state == NodeState.BLOCKED_AT_GATE
        assert t1.state == NodeState.BLOCKED_AT_GATE  # blocked by unsatisfied gate

        # Approve gate
        from plugins.bmad.lib.gate_evaluator import GateEvaluator
        evaluator = GateEvaluator(state_dir, Path("/tmp"))
        evaluator.approve_gate(dag, "g0", "admin", "approved")

        # Second run: gate passes, t1 executes
        execution_order.clear()
        executor2 = DAGExecutor(state_dir, Path("/tmp"))
        executor2.execute_dag(dag, node_executor=track_executor)
        assert "t1" in execution_order
        assert t1.state == NodeState.SUCCEEDED

    def test_script_gate_passes_automatically(self, executor, tmp_path):
        """Script gate that exits 0 passes without manual intervention."""
        import os, stat
        script = tmp_path / "ok.sh"
        script.write_text("#!/bin/bash\nexit 0\n")
        os.chmod(str(script), stat.S_IRWXU)

        execution_order: list[str] = []

        def track_executor(node: DAGNode, cwd: Path) -> ExecutionResult:
            execution_order.append(node.id)
            return ExecutionResult(node.id, NodeState.SUCCEEDED, "ok")

        t0 = _task("t0")
        g0 = _gate("g0", ["t0"], script=str(script), type="script")
        t1 = _task("t1", ["g0"])
        dag = _dag([t0, g0, t1])

        executor.execute_dag(dag, node_executor=track_executor)
        assert "t1" in execution_order
        assert g0.state == NodeState.SUCCEEDED

    def test_script_gate_fails_blocks_downstream(self, executor, tmp_path):
        """Script gate that exits 1 blocks downstream."""
        import os, stat
        script = tmp_path / "fail.sh"
        script.write_text("#!/bin/bash\nexit 1\n")
        os.chmod(str(script), stat.S_IRWXU)

        execution_order: list[str] = []

        def track_executor(node: DAGNode, cwd: Path) -> ExecutionResult:
            execution_order.append(node.id)
            return ExecutionResult(node.id, NodeState.SUCCEEDED, "ok")

        t0 = _task("t0")
        g0 = _gate("g0", ["t0"], script=str(script), type="script")
        t1 = _task("t1", ["g0"])
        dag = _dag([t0, g0, t1])

        executor.execute_dag(dag, node_executor=track_executor)
        assert g0.state == NodeState.BLOCKED_AT_GATE
        assert t1.state == NodeState.BLOCKED_AT_GATE  # blocked by unsatisfied gate


# ── Item 5c: State transitions ───────────────────────────────────────────────


class TestStateTransitions:
    def test_task_state_lifecycle(self, executor):
        def ok_executor(node: DAGNode, cwd: Path) -> ExecutionResult:
            return ExecutionResult(node.id, NodeState.SUCCEEDED, "done")

        t0 = _task("t0")
        dag = _dag([t0])
        executor.execute_dag(dag, node_executor=ok_executor)
        assert t0.state == NodeState.SUCCEEDED
        assert t0.started_at is not None
        assert t0.finished_at is not None
        assert t0.output == "done"

    def test_task_failure_state(self, executor):
        def fail_executor(node: DAGNode, cwd: Path) -> ExecutionResult:
            return ExecutionResult(node.id, NodeState.FAILED, error="boom")

        t0 = _task("t0")
        dag = _dag([t0])
        executor.execute_dag(dag, node_executor=fail_executor)
        assert t0.state == NodeState.FAILED
        assert t0.error == "boom"


# ── Item 5d: Resumability ────────────────────────────────────────────────────


class TestResumability:
    def test_skips_already_succeeded(self, executor):
        """Already-succeeded nodes are not re-executed."""
        executed: list[str] = []

        def track(node: DAGNode, cwd: Path) -> ExecutionResult:
            executed.append(node.id)
            return ExecutionResult(node.id, NodeState.SUCCEEDED, "ok")

        t0 = _task("t0")
        t0.state = NodeState.SUCCEEDED
        t0.output = "already done"
        t1 = _task("t1", ["t0"])
        dag = _dag([t0, t1])

        executor.execute_dag(dag, node_executor=track)
        assert "t0" not in executed  # skipped
        assert "t1" in executed

    def test_resumes_from_partial_execution(self, executor):
        """Engine resumes from a partially completed DAG."""
        executed: list[str] = []

        def track(node: DAGNode, cwd: Path) -> ExecutionResult:
            executed.append(node.id)
            return ExecutionResult(node.id, NodeState.SUCCEEDED, "ok")

        t0 = _task("t0")
        t0.state = NodeState.SUCCEEDED
        t0.output = "done"
        t1 = _task("t1", ["t0"])
        t1.state = NodeState.SUCCEEDED
        t1.output = "done"
        t2 = _task("t2", ["t1"])  # pending
        dag = _dag([t0, t1, t2])

        executor.execute_dag(dag, node_executor=track)
        assert "t0" not in executed
        assert "t1" not in executed
        assert "t2" in executed


# ── Item 5e: Sub-workflow execution ──────────────────────────────────────────


class TestSubworkflowExecution:
    def test_subworkflow_loads_and_runs_sub_dag(self, executor, state_dir):
        """Sub-workflow node loads and executes a sub-DAG."""
        # Create sub-DAG
        sub_t0 = _task("sub-t0")
        sub_dag = DAGDefinition(
            id="sub-d1", workspace_id="ws1", nodes=[sub_t0],
        )
        save_dag(sub_dag, state_dir)

        def ok_executor(node: DAGNode, cwd: Path) -> ExecutionResult:
            return ExecutionResult(node.id, NodeState.SUCCEEDED, "ok")

        # Parent DAG with sub-workflow
        s0 = _task("s0")
        sub_node = DAGNode(
            id="sub", type=NodeType.SUBWORKFLOW,
            dependencies=["s0"], sub_dag_id="sub-d1",
        )
        s1 = _task("s1", ["sub"])
        dag = _dag([s0, sub_node, s1])

        executor.execute_dag(dag, node_executor=ok_executor)
        assert s0.state == NodeState.SUCCEEDED
        assert sub_node.state == NodeState.SUCCEEDED
        assert s1.state == NodeState.SUCCEEDED

    def test_subworkflow_without_sub_dag_id_fails(self, executor):
        sub_node = DAGNode(id="sub", type=NodeType.SUBWORKFLOW)
        dag = _dag([sub_node])
        executor.execute_dag(dag)
        assert sub_node.state == NodeState.FAILED
        assert "no sub_dag_id" in sub_node.error


# ── Item 5f: Execution summary ───────────────────────────────────────────────


class TestExecutionSummary:
    def test_summary_counts_states(self, executor):
        def ok_executor(node: DAGNode, cwd: Path) -> ExecutionResult:
            return ExecutionResult(node.id, NodeState.SUCCEEDED, "ok")

        t0 = _task("t0")
        t1 = _task("t1", ["t0"])
        dag = _dag([t0, t1])
        executor.execute_dag(dag, node_executor=ok_executor)

        summary = executor.get_execution_summary(dag)
        assert summary["total_nodes"] == 2
        assert summary["all_succeeded"] is True
        assert summary["any_failed"] is False
        assert summary["any_blocked"] is False

    def test_summary_reports_blocked_gate(self, executor):
        t0 = _task("t0")
        g0 = _gate("g0", ["t0"])
        dag = _dag([t0, g0])
        executor.execute_dag(dag)

        summary = executor.get_execution_summary(dag)
        assert summary["any_blocked"] is True

    def test_dry_run_does_not_modify_state(self, executor):
        t0 = _task("t0")
        dag = _dag([t0])
        executor.execute_dag(dag, dry_run=True)
        assert t0.state == NodeState.PENDING  # unchanged


# ── Item 5g: Validation ─────────────────────────────────────────────────────


class TestEngineValidation:
    def test_cyclic_dag_raises(self, executor):
        t1 = _task("t1", ["t2"])
        t2 = _task("t2", ["t1"])
        dag = _dag([t1, t2])
        with pytest.raises(DAGValidationError):
            executor.execute_dag(dag)

    def test_dependency_on_failed_node_blocks(self, executor):
        def fail_executor(node: DAGNode, cwd: Path) -> ExecutionResult:
            if node.id == "t0":
                return ExecutionResult(node.id, NodeState.FAILED, error="crash")
            return ExecutionResult(node.id, NodeState.SUCCEEDED, "ok")

        t0 = _task("t0")
        t1 = _task("t1", ["t0"])
        dag = _dag([t0, t1])

        executor.execute_dag(dag, node_executor=fail_executor)
        assert t0.state == NodeState.FAILED
        assert t1.state == NodeState.BLOCKED_AT_GATE
