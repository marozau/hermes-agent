"""Tests for workspace CRUD + CLI commands + visualization (Items 4, 7, 8).

Verifies:
- Workspace: create, list, view, update, archive, delete
- DAG CLI: create, list, view, add_node, execute, status
- Gate CLI: approve, reject, override, reset
- Visualization: ASCII graph output
"""

from __future__ import annotations

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
    gate_override,
    gate_reject,
    gate_reset,
    workspace_archive,
    workspace_create,
    workspace_delete,
    workspace_list,
    workspace_update,
    workspace_view,
)
from plugins.bmad.lib.dag_model import (
    GateCondition,
    GateDecision,
    NodeState,
    NodeType,
    save_dag,
)


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    return tmp_path / "project"


# ── Item 4: Workspace CRUD ───────────────────────────────────────────────────


class TestWorkspaceCRUD:
    def test_create(self, project_dir):
        ws = workspace_create(project_dir, "My Workspace", "test workspace")
        assert ws.name == "My Workspace"
        assert ws.description == "test workspace"
        assert ws.status == "active"
        assert ws.id.startswith("ws-")

    def test_list(self, project_dir):
        workspace_create(project_dir, "WS 1")
        workspace_create(project_dir, "WS 2")
        workspace_create(project_dir, "WS 3")
        wss = workspace_list(project_dir)
        assert len(wss) == 3

    def test_view(self, project_dir):
        ws = workspace_create(project_dir, "Viewable")
        loaded = workspace_view(project_dir, ws.id)
        assert loaded is not None
        assert loaded.name == "Viewable"

    def test_view_nonexistent(self, project_dir):
        assert workspace_view(project_dir, "nonexistent") is None

    def test_update_name(self, project_dir):
        ws = workspace_create(project_dir, "Old Name")
        updated = workspace_update(project_dir, ws.id, name="New Name")
        assert updated.name == "New Name"

    def test_update_description(self, project_dir):
        ws = workspace_create(project_dir, "WS")
        updated = workspace_update(project_dir, ws.id, description="new desc")
        assert updated.description == "new desc"

    def test_update_nonexistent(self, project_dir):
        result = workspace_update(project_dir, "nonexistent", name="X")
        assert result is None

    def test_archive(self, project_dir):
        ws = workspace_create(project_dir, "To Archive")
        archived = workspace_archive(project_dir, ws.id)
        assert archived.status == "archived"

    def test_archive_nonexistent(self, project_dir):
        assert workspace_archive(project_dir, "nonexistent") is None

    def test_delete(self, project_dir):
        ws = workspace_create(project_dir, "To Delete")
        assert workspace_delete(project_dir, ws.id) is True
        assert workspace_view(project_dir, ws.id) is None

    def test_delete_nonexistent(self, project_dir):
        assert workspace_delete(project_dir, "nonexistent") is False


# ── Item 7: DAG CLI commands ─────────────────────────────────────────────────


class TestDAGCommands:
    def test_create_dag(self, project_dir):
        ws = workspace_create(project_dir, "WS")
        dag = dag_create(project_dir, ws.id, "Sprint 1", "first sprint")
        assert dag.name == "Sprint 1"
        assert dag.workspace_id == ws.id

    def test_create_dag_with_deadline(self, project_dir):
        ws = workspace_create(project_dir, "WS")
        dag = dag_create(project_dir, ws.id, "Sprint 1", deadline="2026-06-30")
        assert dag.deadline == "2026-06-30"

    def test_dag_linked_to_workspace(self, project_dir):
        ws = workspace_create(project_dir, "WS")
        dag_create(project_dir, ws.id, "Sprint 1")
        dag_create(project_dir, ws.id, "Sprint 2")
        ws = workspace_view(project_dir, ws.id)
        assert len(ws.dag_ids) == 2

    def test_list_dags(self, project_dir):
        ws = workspace_create(project_dir, "WS")
        dag_create(project_dir, ws.id, "S1")
        dag_create(project_dir, ws.id, "S2")
        assert len(dag_list(project_dir)) == 2

    def test_list_dags_filtered_by_workspace(self, project_dir):
        ws1 = workspace_create(project_dir, "WS1")
        ws2 = workspace_create(project_dir, "WS2")
        dag_create(project_dir, ws1.id, "S1")
        dag_create(project_dir, ws2.id, "S2")
        dag_create(project_dir, ws1.id, "S3")
        assert len(dag_list(project_dir, workspace_id=ws1.id)) == 2

    def test_view_dag(self, project_dir):
        ws = workspace_create(project_dir, "WS")
        dag = dag_create(project_dir, ws.id, "Test")
        loaded = dag_view(project_dir, dag.id)
        assert loaded is not None
        assert loaded.name == "Test"

    def test_add_node_to_dag(self, project_dir):
        ws = workspace_create(project_dir, "WS")
        dag = dag_create(project_dir, ws.id, "Test")
        dag = dag_add_node(project_dir, dag.id, "t1", node_type="task")
        assert len(dag.nodes) == 1
        assert dag.nodes[0].type == NodeType.TASK

    def test_add_gate_node(self, project_dir):
        ws = workspace_create(project_dir, "WS")
        dag = dag_create(project_dir, ws.id, "Test")
        dag = dag_add_node(project_dir, dag.id, "t1")
        dag = dag_add_node(
            project_dir, dag.id, "g1",
            node_type="gate",
            dependencies=["t1"],
            gate_condition={"type": "manual"},
        )
        assert dag.nodes[1].type == NodeType.GATE
        assert dag.nodes[1].gate_condition.type == "manual"

    def test_add_node_rejects_unknown_dep(self, project_dir):
        ws = workspace_create(project_dir, "WS")
        dag = dag_create(project_dir, ws.id, "Test")
        with pytest.raises(Exception, match="unknown"):
            dag_add_node(project_dir, dag.id, "t1", dependencies=["nonexistent"])

    def test_dag_execute(self, project_dir):
        ws = workspace_create(project_dir, "WS")
        dag = dag_create(project_dir, ws.id, "Test")
        dag_add_node(project_dir, dag.id, "t1")
        result = dag_execute(project_dir, dag.id)
        assert result.nodes[0].state == NodeState.SUCCEEDED

    def test_dag_status(self, project_dir):
        ws = workspace_create(project_dir, "WS")
        dag = dag_create(project_dir, ws.id, "Test")
        dag_add_node(project_dir, dag.id, "t1")
        dag_execute(project_dir, dag.id)
        status = dag_status(project_dir, dag.id)
        assert status["all_succeeded"] is True

    def test_dag_not_found(self, project_dir):
        assert dag_view(project_dir, "nonexistent") is None
        assert dag_status(project_dir, "nonexistent").get("error")


# ── Item 8: Gate CLI commands ────────────────────────────────────────────────


class TestGateCommands:
    def test_gate_approve(self, project_dir):
        ws = workspace_create(project_dir, "WS")
        dag = dag_create(project_dir, ws.id, "Test")
        dag_add_node(project_dir, dag.id, "t1")
        dag_add_node(
            project_dir, dag.id, "g1",
            node_type="gate", dependencies=["t1"],
            gate_condition={"type": "manual"},
        )
        dag = gate_approve(project_dir, dag.id, "g1", "admin", "LGTM")
        assert dag.nodes[1].gate_decision == GateDecision.APPROVED
        assert dag.nodes[1].gate_decided_by == "admin"

    def test_gate_reject(self, project_dir):
        ws = workspace_create(project_dir, "WS")
        dag = dag_create(project_dir, ws.id, "Test")
        dag_add_node(
            project_dir, dag.id, "g1",
            node_type="gate",
            gate_condition={"type": "manual"},
        )
        dag = gate_reject(project_dir, dag.id, "g1", "admin", "nope")
        assert dag.nodes[0].gate_decision == GateDecision.REJECTED
        assert dag.nodes[0].state == NodeState.FAILED

    def test_gate_override(self, project_dir):
        ws = workspace_create(project_dir, "WS")
        dag = dag_create(project_dir, ws.id, "Test")
        dag_add_node(
            project_dir, dag.id, "g1",
            node_type="gate",
            gate_condition={"type": "manual"},
        )
        dag = gate_override(project_dir, dag.id, "g1", "admin", "emergency")
        assert dag.nodes[0].gate_decision == GateDecision.OVERRIDE
        assert dag.nodes[0].state == NodeState.SUCCEEDED

    def test_gate_reset(self, project_dir):
        ws = workspace_create(project_dir, "WS")
        dag = dag_create(project_dir, ws.id, "Test")
        dag_add_node(
            project_dir, dag.id, "g1",
            node_type="gate",
            gate_condition={"type": "manual"},
        )
        gate_approve(project_dir, dag.id, "g1", "admin")
        dag = gate_reset(project_dir, dag.id, "g1")
        assert dag.nodes[0].gate_decision == GateDecision.PENDING
        assert dag.nodes[0].state == NodeState.PENDING

    def test_gate_reset_non_gate_raises(self, project_dir):
        ws = workspace_create(project_dir, "WS")
        dag = dag_create(project_dir, ws.id, "Test")
        dag_add_node(project_dir, dag.id, "t1")
        with pytest.raises(ValueError, match="not a gate"):
            gate_reset(project_dir, dag.id, "t1")

    def test_gate_approve_nonexistent_dag(self, project_dir):
        with pytest.raises(ValueError, match="not found"):
            gate_approve(project_dir, "nonexistent", "g1")

    def test_gate_approve_nonexistent_node(self, project_dir):
        ws = workspace_create(project_dir, "WS")
        dag = dag_create(project_dir, ws.id, "Test")
        with pytest.raises(ValueError, match="not found"):
            gate_approve(project_dir, dag.id, "nonexistent")


# ── Item 8b: Visualization ───────────────────────────────────────────────────


class TestVisualization:
    def test_empty_dag(self, project_dir):
        ws = workspace_create(project_dir, "WS")
        dag = dag_create(project_dir, ws.id, "Empty")
        vis = dag_visualize(dag)
        assert "empty" in vis.lower()

    def test_visualization_shows_nodes(self, project_dir):
        ws = workspace_create(project_dir, "WS")
        dag = dag_create(project_dir, ws.id, "Test")
        dag_add_node(project_dir, dag.id, "t1", description="first task")
        dag_add_node(
            project_dir, dag.id, "g1",
            node_type="gate", dependencies=["t1"],
            gate_condition={"type": "manual"},
            description="quality gate",
        )
        dag_add_node(project_dir, dag.id, "t2", dependencies=["g1"])
        loaded = dag_view(project_dir, dag.id)
        vis = dag_visualize(loaded)
        assert "t1" in vis
        assert "g1" in vis
        assert "t2" in vis
        assert "quality gate" in vis

    def test_visualization_shows_deadline(self, project_dir):
        ws = workspace_create(project_dir, "WS")
        dag = dag_create(project_dir, ws.id, "Test", deadline="2026-06-30")
        dag_add_node(project_dir, dag.id, "t1")
        loaded = dag_view(project_dir, dag.id)
        vis = dag_visualize(loaded)
        assert "2026-06-30" in vis

    def test_visualization_shows_gate_decision(self, project_dir):
        ws = workspace_create(project_dir, "WS")
        dag = dag_create(project_dir, ws.id, "Test")
        dag_add_node(
            project_dir, dag.id, "g1",
            node_type="gate",
            gate_condition={"type": "manual"},
        )
        gate_approve(project_dir, dag.id, "g1", "alice")
        loaded = dag_view(project_dir, dag.id)
        vis = dag_visualize(loaded)
        assert "approved" in vis.lower()
        assert "alice" in vis

    def test_visualization_shows_worktree(self, project_dir):
        ws = workspace_create(project_dir, "WS")
        dag = dag_create(project_dir, ws.id, "Test")
        dag_add_node(project_dir, dag.id, "t1", worktree="hermes-agent")
        loaded = dag_view(project_dir, dag.id)
        vis = dag_visualize(loaded)
        assert "hermes-agent" in vis

    def test_visualization_shows_summary(self, project_dir):
        ws = workspace_create(project_dir, "WS")
        dag = dag_create(project_dir, ws.id, "Test")
        dag_add_node(project_dir, dag.id, "t1")
        dag_add_node(project_dir, dag.id, "t2")
        dag_execute(project_dir, dag.id)
        loaded = dag_view(project_dir, dag.id)
        vis = dag_visualize(loaded)
        assert "2 succeeded" in vis or "succeeded" in vis
