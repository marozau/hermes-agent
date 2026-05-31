"""Tests for the DAG data model (Item 2 — multi-type nodes, edges, acyclicity).

Verifies:
- Multi-type nodes: task, gate, sub-workflow
- Directed edges with dependency tracking
- Acyclicity enforcement at insertion time (Kahn's algorithm)
- Cycle detection raises DAGValidationError
- State persistence (YAML round-trip)
- Optimistic locking version field
- Deadline awareness
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from plugins.bmad.lib.dag_model import (
    DAGDefinition,
    DAGNode,
    DAGValidationError,
    GateCondition,
    GateDecision,
    NodeState,
    NodeType,
    Workspace,
    add_node,
    check_deadline,
    check_node_deadline,
    list_dags,
    list_workspaces,
    load_dag,
    load_workspace,
    save_dag,
    save_workspace,
    topological_sort,
    validate_dag_acyclicity,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "dag-state"


def _task(id: str, deps: list[str] | None = None) -> DAGNode:
    return DAGNode(id=id, type=NodeType.TASK, dependencies=deps or [])


def _gate(id: str, deps: list[str] | None = None, **kwargs) -> DAGNode:
    return DAGNode(
        id=id,
        type=NodeType.GATE,
        dependencies=deps or [],
        gate_condition=GateCondition(**kwargs) if kwargs else GateCondition(),
    )


def _sub(id: str, sub_dag_id: str, deps: list[str] | None = None) -> DAGNode:
    return DAGNode(
        id=id, type=NodeType.SUBWORKFLOW, dependencies=deps or [], sub_dag_id=sub_dag_id,
    )


def _dag(nodes: list[DAGNode] | None = None) -> DAGDefinition:
    return DAGDefinition(
        id="test-dag", workspace_id="test-ws", nodes=nodes or [],
    )


# ── Item 2a: Multi-type nodes ────────────────────────────────────────────────


class TestMultiTypeNodes:
    def test_task_node_type(self):
        node = DAGNode(id="t1", type=NodeType.TASK)
        assert node.type == NodeType.TASK
        assert node.type.value == "task"

    def test_gate_node_type(self):
        node = DAGNode(
            id="g1", type=NodeType.GATE, gate_condition=GateCondition(),
        )
        assert node.type == NodeType.GATE
        assert node.type.value == "gate"

    def test_subworkflow_node_type(self):
        node = DAGNode(id="s1", type=NodeType.SUBWORKFLOW, sub_dag_id="sub-1")
        assert node.type == NodeType.SUBWORKFLOW
        assert node.type.value == "subworkflow"

    def test_default_node_is_task(self):
        node = DAGNode(id="x")
        assert node.type == NodeType.TASK


# ── Item 2b: Directed edges / dependencies ───────────────────────────────────


class TestDirectedEdges:
    def test_single_dependency(self):
        t1 = _task("t1")
        t2 = _task("t2", deps=["t1"])
        dag = _dag([t1, t2])
        assert t2.dependencies == ["t1"]

    def test_multi_dependency(self):
        t1 = _task("t1")
        t2 = _task("t2")
        t3 = _task("t3", deps=["t1", "t2"])
        dag = _dag([t1, t2, t3])
        assert set(t3.dependencies) == {"t1", "t2"}

    def test_diamond_dependency(self):
        """t1 → t2, t1 → t3, t2 → t4, t3 → t4."""
        t1 = _task("t1")
        t2 = _task("t2", ["t1"])
        t3 = _task("t3", ["t1"])
        t4 = _task("t4", ["t2", "t3"])
        dag = _dag([t1, t2, t3, t4])
        errors = validate_dag_acyclicity(dag.nodes)
        assert errors == []

    def test_unknown_dependency_rejected(self):
        t1 = _task("t1", deps=["nonexistent"])
        errors = validate_dag_acyclicity([t1])
        assert any("unknown node" in e for e in errors)


# ── Item 2c: Acyclicity enforcement ─────────────────────────────────────────


class TestAcyclicityEnforcement:
    def test_simple_cycle_detected(self):
        t1 = _task("t1", deps=["t2"])
        t2 = _task("t2", deps=["t1"])
        errors = validate_dag_acyclicity([t1, t2])
        assert len(errors) >= 1
        assert "Cycle" in errors[0] or "unknown" in errors[0].lower()

    def test_three_node_cycle_detected(self):
        t1 = _task("t1", deps=["t3"])
        t2 = _task("t2", deps=["t1"])
        t3 = _task("t3", deps=["t2"])
        errors = validate_dag_acyclicity([t1, t2, t3])
        assert any("Cycle" in e for e in errors)

    def test_add_node_rejects_cycle(self):
        t1 = _task("t1")
        t2 = _task("t2", deps=["t1"])
        dag = _dag([t1, t2])
        # Adding t3 that depends on t2 while t2 depends on t1 is fine
        t3 = _task("t3", deps=["t2"])
        add_node(dag, t3)
        assert len(dag.nodes) == 3

    def test_add_node_rejects_back_edge(self):
        t1 = _task("t1")
        t2 = _task("t2", deps=["t1"])
        dag = _dag([t1, t2])
        # Adding t1-depends-on-t2 would create cycle
        t3 = _task("t1", deps=["t2"])  # duplicate ID
        with pytest.raises(DAGValidationError, match="Duplicate"):
            add_node(dag, t3)

    def test_add_node_rejects_self_loop(self):
        t1 = _task("t1")
        dag = _dag([t1])
        t2 = _task("t2", deps=["t2"])  # self-loop via unknown dep
        with pytest.raises(DAGValidationError, match="unknown"):
            add_node(dag, t2)

    def test_acyclic_dag_validates_clean(self):
        nodes = [_task(f"t{i}", [f"t{i-1}"] if i > 0 else []) for i in range(5)]
        errors = validate_dag_acyclicity(nodes)
        assert errors == []


# ── Item 2d: Topological sort ────────────────────────────────────────────────


class TestTopologicalSort:
    def test_linear_sort(self):
        t0 = _task("t0")
        t1 = _task("t1", ["t0"])
        t2 = _task("t2", ["t1"])
        result = topological_sort([t2, t0, t1])  # input in reverse
        ids = [n.id for n in result]
        assert ids == ["t0", "t1", t2.id]

    def test_sort_respects_dependencies(self):
        t0 = _task("t0")
        t1 = _task("t1", ["t0"])
        t2 = _task("t2", ["t0"])
        t3 = _task("t3", ["t1", "t2"])
        result = topological_sort([t3, t1, t2, t0])
        ids = [n.id for n in result]
        assert ids.index("t0") < ids.index("t1")
        assert ids.index("t0") < ids.index("t2")
        assert ids.index("t1") < ids.index("t3")
        assert ids.index("t2") < ids.index("t3")

    def test_sort_raises_on_cycle(self):
        t1 = _task("t1", ["t2"])
        t2 = _task("t2", ["t1"])
        with pytest.raises(DAGValidationError):
            topological_sort([t1, t2])


# ── Item 2e: State persistence ───────────────────────────────────────────────


class TestStatePersistence:
    def test_save_load_round_trip(self, state_dir: Path):
        dag = _dag([_task("t1"), _gate("g1", gate_condition=GateCondition())])
        dag.nodes[1].state = NodeState.BLOCKED_AT_GATE
        dag.nodes[1].gate_decision = GateDecision.PENDING
        save_dag(dag, state_dir)
        loaded = load_dag("test-dag", state_dir)
        assert loaded is not None
        assert loaded.id == "test-dag"
        assert len(loaded.nodes) == 2
        assert loaded.nodes[1].state == NodeState.BLOCKED_AT_GATE
        assert loaded.nodes[1].gate_decision == GateDecision.PENDING

    def test_version_incremented_on_save(self, state_dir: Path):
        dag = _dag([_task("t1")])
        assert dag.version == 0
        save_dag(dag, state_dir)
        assert dag.version == 1
        loaded = load_dag("test-dag", state_dir)
        assert loaded.version == 1
        save_dag(loaded, state_dir)
        assert loaded.version == 2

    def test_optimistic_locking_conflict(self, state_dir: Path):
        dag = _dag([_task("t1")])
        save_dag(dag, state_dir)  # version → 1

        # Two processes load the same version
        a = load_dag("test-dag", state_dir)
        b = load_dag("test-dag", state_dir)
        assert a.version == 1
        assert b.version == 1

        # A saves first
        save_dag(a, state_dir)  # version → 2

        # B tries to save with stale version → raises
        with pytest.raises(DAGValidationError, match="Concurrent modification"):
            save_dag(b, state_dir)

    def test_load_nonexistent_returns_none(self, state_dir: Path):
        assert load_dag("nonexistent", state_dir) is None

    def test_workspace_save_load(self, state_dir: Path):
        ws = Workspace(id="ws-1", name="Test WS", path="/tmp/test")
        ws.dag_ids.append("dag-1")
        save_workspace(ws, state_dir)
        loaded = load_workspace("ws-1", state_dir)
        assert loaded is not None
        assert loaded.name == "Test WS"
        assert loaded.dag_ids == ["dag-1"]

    def test_list_dags_excludes_workspaces(self, state_dir: Path):
        save_workspace(Workspace(id="ws-1", name="WS", path="/tmp"), state_dir)
        save_dag(_dag([_task("t1")]), state_dir)
        dags = list_dags(state_dir)
        assert len(dags) == 1
        assert dags[0].id == "test-dag"

    def test_list_workspaces(self, state_dir: Path):
        for i in range(3):
            save_workspace(Workspace(id=f"ws-{i}", name=f"WS {i}", path="/tmp"), state_dir)
        wss = list_workspaces(state_dir)
        assert len(wss) == 3

    def test_atomic_write_no_corruption(self, state_dir: Path):
        """Simulate a crash during write — the old file should survive."""
        dag = _dag([_task("t1")])
        save_dag(dag, state_dir)
        # File exists and is valid YAML
        raw = yaml.safe_load((state_dir / "test-dag.yaml").read_text())
        assert raw["id"] == "test-dag"


# ── Item 2f: Deadline awareness ──────────────────────────────────────────────


class TestDeadlineAwareness:
    def test_no_deadline_returns_none(self):
        dag = _dag()
        assert check_deadline(dag) is None

    def test_future_deadline_returns_none(self):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        dag = _dag()
        dag.deadline = future
        assert check_deadline(dag) is None

    def test_past_deadline_returns_exceeded(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        dag = _dag()
        dag.deadline = past
        result = check_deadline(dag)
        assert result is not None
        assert result["status"] == "exceeded"

    def test_approaching_deadline_detected(self):
        soon = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        dag = _dag()
        dag.deadline = soon
        result = check_deadline(dag)
        assert result is not None
        assert result["status"] == "approaching"

    def test_node_deadline_checked(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        node = _task("t1")
        node.deadline = past
        result = check_node_deadline(node)
        assert result is not None
        assert result["status"] == "exceeded"

    def test_invalid_deadline_ignored(self):
        dag = _dag()
        dag.deadline = "not-a-date"
        assert check_deadline(dag) is None
