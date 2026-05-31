"""Tests for the gate evaluator (Item 3 — first-class gate type with evaluation logic).

Verifies:
- Gate node type with configurable evaluation
- Manual gate: approve/reject/override/reset
- Script gate: exit code based
- Threshold gate: numeric comparison against upstream output
- Artifact check gate: file existence
- Gate blocks downstream execution
- Gate references upstream task outputs
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

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
def state_dir(tmp_path: Path) -> Path:
    d = tmp_path / "dag-state"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def evaluator(state_dir: Path, tmp_path: Path) -> GateEvaluator:
    return GateEvaluator(state_dir, tmp_path)


def _gate_node(id: str = "g1", **cond_kwargs) -> DAGNode:
    # If script is provided, default type to "script"
    if "script" in cond_kwargs and "type" not in cond_kwargs:
        cond_kwargs["type"] = "script"
    return DAGNode(
        id=id,
        type=NodeType.GATE,
        gate_condition=GateCondition(**cond_kwargs),
    )


def _dag_with(nodes: list[DAGNode]) -> DAGDefinition:
    return DAGDefinition(id="d1", workspace_id="ws1", nodes=nodes)


# ── Item 3a: Manual gate ────────────────────────────────────────────────────


class TestManualGate:
    def test_manual_gate_pending_blocks(self, evaluator):
        node = _gate_node()
        dag = _dag_with([node])
        passed, reason = evaluator.evaluate_gate(dag, node, {})
        assert passed is False
        assert "awaiting manual approval" in reason

    def test_manual_gate_approve_passes(self, evaluator):
        node = _gate_node()
        dag = _dag_with([node])
        evaluator.approve_gate(dag, "g1", "admin", "looks good")
        passed, reason = evaluator.evaluate_gate(dag, node, {})
        assert passed is True
        assert "approved" in reason.lower()

    def test_manual_gate_reject_fails(self, evaluator):
        node = _gate_node()
        dag = _dag_with([node])
        evaluator.reject_gate(dag, "g1", "admin", "quality too low")
        passed, reason = evaluator.evaluate_gate(dag, node, {})
        assert passed is False
        assert "rejected" in reason.lower()

    def test_manual_gate_override_passes(self, evaluator):
        node = _gate_node()
        dag = _dag_with([node])
        evaluator.override_gate(dag, "g1", "admin", "emergency")
        passed, reason = evaluator.evaluate_gate(dag, node, {})
        assert passed is True
        assert "overridden" in reason.lower()

    def test_gate_approve_sets_metadata(self, evaluator):
        node = _gate_node()
        dag = _dag_with([node])
        evaluator.approve_gate(dag, "g1", "alice", "LGTM")
        assert node.gate_decision == GateDecision.APPROVED
        assert node.gate_decided_by == "alice"
        assert node.gate_decided_at is not None
        assert node.state == NodeState.SUCCEEDED

    def test_gate_reject_sets_metadata(self, evaluator):
        node = _gate_node()
        dag = _dag_with([node])
        evaluator.reject_gate(dag, "g1", "bob", "nope")
        assert node.gate_decision == GateDecision.REJECTED
        assert node.gate_decided_by == "bob"
        assert node.state == NodeState.FAILED


# ── Item 3b: Script gate ────────────────────────────────────────────────────


class TestScriptGate:
    def test_script_gate_passes_on_zero_exit(self, evaluator, tmp_path):
        script = tmp_path / "check.sh"
        script.write_text("#!/bin/bash\nexit 0\n")
        os.chmod(str(script), stat.S_IRWXU)

        node = _gate_node(script=str(script))
        dag = _dag_with([node])
        passed, reason = evaluator.evaluate_gate(dag, node, {})
        assert passed is True
        assert "passed" in reason.lower()

    def test_script_gate_fails_on_nonzero_exit(self, evaluator, tmp_path):
        script = tmp_path / "check.sh"
        script.write_text("#!/bin/bash\necho 'quality too low' >&2\nexit 1\n")
        os.chmod(str(script), stat.S_IRWXU)

        node = _gate_node(script=str(script))
        dag = _dag_with([node])
        passed, reason = evaluator.evaluate_gate(dag, node, {})
        assert passed is False
        assert "failed" in reason.lower()

    def test_script_gate_timeout(self, evaluator, tmp_path):
        script = tmp_path / "slow.sh"
        script.write_text("#!/bin/bash\nsleep 60\n")
        os.chmod(str(script), stat.S_IRWXU)

        node = _gate_node(script=str(script), timeout_seconds=1)
        dag = _dag_with([node])
        passed, reason = evaluator.evaluate_gate(dag, node, {})
        assert passed is False
        assert "timed out" in reason.lower()

    def test_script_gate_receives_upstream_outputs_as_env(self, evaluator, tmp_path):
        script = tmp_path / "check_env.sh"
        script.write_text(
            '#!/bin/bash\n'
            'if [ -n "$UPSTREAM_OUTPUT_T1" ]; then\n'
            '  echo "got: $UPSTREAM_OUTPUT_T1"\n'
            '  exit 0\n'
            'else\n'
            '  exit 1\n'
            'fi\n'
        )
        os.chmod(str(script), stat.S_IRWXU)

        node = _gate_node(script=str(script))
        dag = _dag_with([node])
        passed, reason = evaluator.evaluate_gate(dag, node, {"t1": "hello"})
        assert passed is True
        assert "hello" in reason


# ── Item 3c: Threshold gate ─────────────────────────────────────────────────


class TestThresholdGate:
    def test_threshold_pass_ge(self, evaluator):
        node = _gate_node(
            type="threshold",
            threshold=80.0,
            threshold_field="coverage",
            threshold_operator=">=",
        )
        dag = _dag_with([node])
        upstream = {"t1": json.dumps({"coverage": 85.5})}
        passed, reason = evaluator.evaluate_gate(dag, node, upstream)
        assert passed is True
        assert "85.5" in reason

    def test_threshold_fail_ge(self, evaluator):
        node = _gate_node(
            type="threshold",
            threshold=80.0,
            threshold_field="coverage",
            threshold_operator=">=",
        )
        dag = _dag_with([node])
        upstream = {"t1": json.dumps({"coverage": 72.0})}
        passed, reason = evaluator.evaluate_gate(dag, node, upstream)
        assert passed is False

    def test_threshold_pass_le(self, evaluator):
        node = _gate_node(
            type="threshold",
            threshold=5.0,
            threshold_field="error_rate",
            threshold_operator="<=",
        )
        dag = _dag_with([node])
        upstream = {"t1": json.dumps({"error_rate": 3.2})}
        passed, reason = evaluator.evaluate_gate(dag, node, upstream)
        assert passed is True

    def test_threshold_pass_eq(self, evaluator):
        node = _gate_node(
            type="threshold",
            threshold=100.0,
            threshold_field="score",
            threshold_operator="==",
        )
        dag = _dag_with([node])
        upstream = {"t1": json.dumps({"score": 100.0})}
        passed, reason = evaluator.evaluate_gate(dag, node, upstream)
        assert passed is True

    def test_threshold_missing_field_fails(self, evaluator):
        node = _gate_node(
            type="threshold",
            threshold=80.0,
            threshold_field="nonexistent",
        )
        dag = _dag_with([node])
        upstream = {"t1": json.dumps({"other": 42})}
        passed, reason = evaluator.evaluate_gate(dag, node, upstream)
        assert passed is False
        assert "could not extract" in reason.lower()


# ── Item 3d: Artifact check gate ────────────────────────────────────────────


class TestArtifactCheckGate:
    def test_artifact_exists_passes(self, evaluator, tmp_path):
        artifact = tmp_path / "output.bin"
        artifact.write_bytes(b"data")
        script = tmp_path / "check.sh"
        script.write_text(f'#!/bin/bash\ntest -f "{artifact}" && exit 0 || exit 1\n')
        os.chmod(str(script), stat.S_IRWXU)

        node = _gate_node(type="artifact_check", script=str(script))
        dag = _dag_with([node])
        passed, reason = evaluator.evaluate_gate(dag, node, {})
        assert passed is True

    def test_artifact_missing_fails(self, evaluator, tmp_path):
        script = tmp_path / "check.sh"
        script.write_text("#!/bin/bash\ntest -f /nonexistent/path && exit 0 || exit 1\n")
        os.chmod(str(script), stat.S_IRWXU)

        node = _gate_node(type="artifact_check", script=str(script))
        dag = _dag_with([node])
        passed, reason = evaluator.evaluate_gate(dag, node, {})
        assert passed is False


# ── Item 3e: Gate blocks downstream ─────────────────────────────────────────


class TestGateBlocksDownstream:
    def test_gate_not_approved_blocks_downstream(self, evaluator):
        """A manual gate in PENDING state blocks downstream."""
        gate = _gate_node("g1")
        task = DAGNode(id="t1", type=NodeType.TASK, dependencies=["g1"])
        dag = _dag_with([gate, task])

        # Gate is pending → should block
        passed, _ = evaluator.evaluate_gate(dag, gate, {})
        assert passed is False
        # Downstream should see gate as not satisfied
        assert gate.state != NodeState.SUCCEEDED


# ── Item 3f: Gate references upstream outputs ────────────────────────────────


class TestGateReferencesUpstream:
    def test_threshold_reads_upstream_json(self, evaluator):
        t1 = DAGNode(id="t1", type=NodeType.TASK, state=NodeState.SUCCEEDED)
        t1.output = json.dumps({"coverage": 92.3})

        g1 = _gate_node(
            "g1",
            type="threshold",
            threshold=80.0,
            threshold_field="coverage",
            threshold_operator=">=",
        )
        dag = _dag_with([t1, g1])

        passed, reason = evaluator.evaluate_gate(dag, g1, {"t1": t1.output})
        assert passed is True
        assert "92.3" in reason

    def test_script_reads_upstream_env(self, evaluator, tmp_path):
        script = tmp_path / "check.sh"
        script.write_text(
            '#!/bin/bash\n'
            'if [ "$UPSTREAM_OUTPUT_T1" = "all-passed" ]; then exit 0; else exit 1; fi\n'
        )
        os.chmod(str(script), stat.S_IRWXU)

        g1 = _gate_node("g1", script=str(script))
        dag = _dag_with([g1])

        passed, _ = evaluator.evaluate_gate(dag, g1, {"t1": "all-passed"})
        assert passed is True

        passed, _ = evaluator.evaluate_gate(dag, g1, {"t1": "some-failed"})
        assert passed is False


# ── Item 3g: Non-gate node rejected ─────────────────────────────────────────


class TestNonGateRejected:
    def test_evaluate_non_gate_returns_false(self, evaluator):
        task = DAGNode(id="t1", type=NodeType.TASK)
        dag = _dag_with([task])
        passed, reason = evaluator.evaluate_gate(dag, task, {})
        assert passed is False
        assert "not a gate" in reason.lower()


# ── Item 3h: Gate with on_failure behaviors ──────────────────────────────────


class TestGateOnFailure:
    def test_on_failure_block(self):
        cond = GateCondition(on_failure="block")
        assert cond.on_failure == "block"

    def test_on_failure_skip(self):
        cond = GateCondition(on_failure="skip")
        assert cond.on_failure == "skip"

    def test_on_failure_fail(self):
        cond = GateCondition(on_failure="fail")
        assert cond.on_failure == "fail"
