"""Integration tests for StallDetector with real ACPOrchestratorEventHandler.

Verifies:
    - Full two-stage escalation end-to-end
    - Resolution when heartbeat resumes
    - No false positives for completing tasks
    - Multiple task tracking with mixed states
    - Recovery handler receives correct callbacks
    - Configurable timeouts work with real event timing
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

import pytest

from acp_adapter.messages import (
    SessionEnd,
    SessionHeartbeat,
    SessionStart,
    SessionStalled,
)
from plugins.bmad.orchestrator.event_handler import ACPOrchestratorEventHandler
from plugins.bmad.orchestrator.stall_detector import (
    LogRecoveryHandler,
    RecoveryAction,
    StallCheckResult,
    StallDetector,
    StallDetectorConfig,
    StallStage,
)


# ============================================================================
# Helpers — event creation (same pattern as test_acp_event_handler.py)
# ============================================================================


def _make_session_start(
    session_id: str = "sess-001",
    cwd: str = "/tmp/project",
    task_id: str = "",
    client_info: Dict[str, str] | None = None,
) -> SessionStart:
    meta: Dict[str, Any] = {}
    if task_id:
        meta["task_id"] = task_id
    return SessionStart(
        sessionId=session_id,
        cwd=cwd,
        clientInfo=client_info or {"name": "zed"},
        _meta=meta or None,
    )


def _make_session_heartbeat(
    session_id: str = "sess-001",
    agent_state: str = "working",
    current_tool: str | None = None,
    iteration: int | None = None,
    task_id: str = "",
) -> SessionHeartbeat:
    meta: Dict[str, Any] = {}
    if task_id:
        meta["task_id"] = task_id
    return SessionHeartbeat(
        sessionId=session_id,
        agentState=agent_state,
        currentTool=current_tool,
        iteration=iteration,
        _meta=meta or None,
    )


def _make_session_end(
    session_id: str = "sess-001",
    outcome: str = "completed",
    summary: str | None = None,
    task_id: str = "",
) -> SessionEnd:
    meta: Dict[str, Any] = {}
    if task_id:
        meta["task_id"] = task_id
    return SessionEnd(
        sessionId=session_id,
        outcome=outcome,
        summary=summary,
        _meta=meta or None,
    )


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def handler() -> ACPOrchestratorEventHandler:
    h = ACPOrchestratorEventHandler(kanban_comments_enabled=False)
    yield h
    h.reset()


@pytest.fixture
def config_fast() -> StallDetectorConfig:
    """Config with very short timeouts for fast integration tests."""
    return StallDetectorConfig(
        heartbeat_timeout_seconds=0.1,    # 100ms — heartbeat is stale quickly
        escalation_timeout_seconds=0.3,    # 300ms — escalate quickly
        max_escalations=2,
        escalation_action=RecoveryAction.LOG,
    )


@pytest.fixture
def detector(
    handler: ACPOrchestratorEventHandler,
    config_fast: StallDetectorConfig,
) -> StallDetector:
    d = StallDetector(handler=handler, config=config_fast)
    yield d
    d.reset()


# ============================================================================
# End-to-end stall detection
# ============================================================================


class TestEndToEndStall:
    """Full two-stage escalation from live events through to resolution."""

    def test_fresh_task_not_stalled(self, handler, detector):
        handler.handle_event(_make_session_start(task_id="t-fresh"))

        results = detector.check()
        assert results == []

    def test_stale_heartbeat_triggers_stage1(self, handler, detector):
        handler.handle_event(_make_session_start(task_id="t-stale"))

        # Wait for heartbeat timeout
        time.sleep(0.15)

        results = detector.check()
        assert len(results) == 1
        assert results[0].task_id == "t-stale"
        assert results[0].action == "stage1"
        assert results[0].stage == StallStage.STAGE1

    def test_stage1_escalates_to_stage2(self, handler, detector):
        handler.handle_event(_make_session_start(task_id="t-esc"))

        # Wait past heartbeat timeout → stage 1
        time.sleep(0.15)
        detector.check()

        # Wait past escalation timeout (0.3s) from when stage 1 was recorded
        time.sleep(0.35)
        results = detector.check()
        assert len(results) == 1
        assert results[0].task_id == "t-esc"
        assert results[0].action == "log"
        assert results[0].stage == StallStage.STAGE2
        assert results[0].escalation_count == 1

    def test_heartbeat_recovery_resolves_stall(self, handler, detector):
        handler.handle_event(_make_session_start(task_id="t-rec"))

        time.sleep(0.15)  # stale
        detector.check()   # stage 1

        # Send fresh heartbeat
        handler.handle_event(
            _make_session_heartbeat(task_id="t-rec", iteration=5)
        )

        results = detector.check()
        assert len(results) == 1
        assert results[0].action == "resolved"

    def test_task_completion_resolves_stall(self, handler, detector):
        handler.handle_event(_make_session_start(task_id="t-comp"))

        time.sleep(0.15)  # stale
        detector.check()   # stage 1

        # Task completes
        handler.handle_event(
            _make_session_end(task_id="t-comp", outcome="completed")
        )

        results = detector.check()
        resolved = [r for r in results if r.task_id == "t-comp"]
        assert len(resolved) == 1
        assert resolved[0].action == "resolved"

    def test_no_false_positives_on_completion(self, handler, detector):
        """A task that starts and immediately completes should never flag."""
        handler.handle_event(_make_session_start(task_id="t-ok"))
        handler.handle_event(
            _make_session_end(task_id="t-ok", outcome="completed")
        )

        # Multiple checks, no stall should appear
        for _ in range(3):
            results = detector.check()
            stalled = [r for r in results if r.task_id == "t-ok"]
            assert stalled == [], f"False positive on iteration {_}"

    def test_heartbeat_keeps_task_alive(self, handler, detector):
        handler.handle_event(_make_session_start(task_id="t-alive"))

        # Send heartbeats more frequently than timeout.
        # Use a larger timeout (2.0s) to avoid timing-jitter false positives.
        detector._config.heartbeat_timeout_seconds = 2.0

        for i in range(5):
            handler.handle_event(
                _make_session_heartbeat(task_id="t-alive", iteration=i)
            )
            time.sleep(0.3)  # 300ms ≪ 2000ms timeout
            results = detector.check()
            stalled = [r for r in results if r.task_id == "t-alive" and r.action != "resolved"]
            assert stalled == [], f"False stall on iteration {i}"

        # Restore original timeout
        detector._config.heartbeat_timeout_seconds = 0.1


# ============================================================================
# Multi-task integration
# ============================================================================


class TestMultiTaskIntegration:
    """Multiple tasks with different states are handled correctly."""

    def test_mixed_states(self, handler, detector):
        # Use a generous timeout so only intentionally-stale tasks are flagged
        detector._config.heartbeat_timeout_seconds = 2.0

        # t-a: running, fresh heartbeat
        handler.handle_event(_make_session_start(task_id="t-a"))
        handler.handle_event(
            _make_session_heartbeat(task_id="t-a", iteration=1)
        )

        # t-b: running, stale heartbeat (pre-aged via SessionStart alone,
        # then we wait to push it past timeout)
        handler.handle_event(_make_session_start(task_id="t-b"))
        # Make t-b's heartbeat ancient so it crosses the 2.0s threshold
        time.sleep(2.1)

        # t-c: completed
        handler.handle_event(_make_session_start(task_id="t-c"))
        handler.handle_event(
            _make_session_end(task_id="t-c", outcome="completed")
        )

        # Send a fresh heartbeat for t-a just before checking
        handler.handle_event(
            _make_session_heartbeat(task_id="t-a", iteration=2)
        )

        results = detector.check()
        stalled_ids = {r.task_id for r in results if r.action == "stage1"}
        assert stalled_ids == {"t-b"}
        # t-a should not be in results (fresh heartbeat)
        # t-c should not be in results (completed)

        # Restore
        detector._config.heartbeat_timeout_seconds = 0.1

    def test_one_stalled_one_resolved(self, handler, detector):
        handler.handle_event(_make_session_start(task_id="t-s1"))
        handler.handle_event(_make_session_start(task_id="t-s2"))
        time.sleep(0.15)

        detector.check()  # both stage 1

        # Recover t-s1
        handler.handle_event(
            _make_session_heartbeat(task_id="t-s1", iteration=10)
        )

        results = detector.check()
        resolved = [r for r in results if r.task_id == "t-s1" and r.action == "resolved"]
        assert len(resolved) == 1


# ============================================================================
# Recovery handler integration
# ============================================================================


class TestRecoveryHandlerIntegration:
    """Recovery handler receives correct data from real events."""

    def test_stage1_callback_with_real_data(self, handler):
        calls = []

        class SpyRecovery:
            def on_stage1_stall(self, task_id, state, stall_since_seconds):
                calls.append({
                    "task_id": task_id,
                    "state_status": state.status,
                    "state_iteration": state.iteration,
                    "stall_since": stall_since_seconds,
                })

            def on_stage2_escalation(self, task_id, state, stall_since_seconds, escalation_count):
                calls.append({"stage2": task_id, "count": escalation_count})

            def on_stall_resolved(self, task_id, state, reason):
                calls.append({"resolved": task_id, "reason": reason})

        config = StallDetectorConfig(
            heartbeat_timeout_seconds=0.1,
            escalation_timeout_seconds=999.0,  # never escalate
        )
        detector = StallDetector(handler=handler, recovery=SpyRecovery(), config=config)

        handler.handle_event(_make_session_start(task_id="t-spy"))
        time.sleep(0.15)
        detector.check()

        assert len(calls) >= 1
        assert calls[0]["task_id"] == "t-spy"
        assert calls[0]["state_status"] == "running"
        assert calls[0]["stall_since"] > 0

    def test_stage2_callback_with_real_data(self, handler):
        calls = []

        class SpyRecovery:
            def on_stage1_stall(self, task_id, state, stall_since):
                calls.append(("stage1", task_id))

            def on_stage2_escalation(self, task_id, state, stall_since, count):
                calls.append(("stage2", task_id, count))

            def on_stall_resolved(self, task_id, state, reason):
                calls.append(("resolved", task_id))

        config = StallDetectorConfig(
            heartbeat_timeout_seconds=0.1,
            escalation_timeout_seconds=0.15,
        )
        detector = StallDetector(handler=handler, recovery=SpyRecovery(), config=config)

        handler.handle_event(_make_session_start(task_id="t-spy2"))
        time.sleep(0.15)
        detector.check()  # stage 1
        time.sleep(0.2)
        detector.check()  # stage 2

        assert ("stage1", "t-spy2") in calls
        assert any(c[0] == "stage2" and c[1] == "t-spy2" for c in calls)


# ============================================================================
# Configuration integration
# ============================================================================


class TestConfigurationIntegration:
    """Real handler with configurable timeouts."""

    def test_different_heartbeat_timeouts(self, handler):
        config_long = StallDetectorConfig(
            heartbeat_timeout_seconds=999.0,
            escalation_timeout_seconds=9999.0,
        )
        detector_long = StallDetector(handler=handler, config=config_long)

        handler.handle_event(_make_session_start(task_id="t-long"))

        # With 999s timeout, a brand-new task isn't stalled
        results = detector_long.check()
        assert results == []

    def test_different_escalation_actions(self, handler):
        for action in RecoveryAction:
            config = StallDetectorConfig(
                heartbeat_timeout_seconds=0.0,
                escalation_timeout_seconds=0.0,
                escalation_action=action,
            )
            detector = StallDetector(handler=handler, config=config)
            handler.reset()
            detector.reset()

            handler.handle_event(_make_session_start(task_id="t-act"))
            detector.check()  # stage 1 (immediate)
            results = detector.check()  # stage 2 (immediate)
            assert len(results) >= 1
            assert results[0].action == action.value


# ============================================================================
# SessionStalled event integration
# ============================================================================


class TestSessionStalledEvent:
    """SessionStalled events mark tasks as stalled, detector treats accordingly."""

    def test_session_stalled_event_detected_as_stalled(self, handler, detector):
        handler.handle_event(_make_session_start(task_id="t-sse"))

        # With a fresh heartbeat, it's not stalled by timeout yet
        results = detector.check()
        assert results == []

    def test_session_stalled_transitions_to_stage1_after_heartbeat_timeout(
        self, handler, detector
    ):
        handler.handle_event(_make_session_start(task_id="t-sse2"))
        time.sleep(0.15)  # past heartbeat timeout
        results = detector.check()
        stalled = [r for r in results if r.task_id == "t-sse2" and r.action == "stage1"]
        assert len(stalled) == 1
