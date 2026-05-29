"""Stall detector — two-stage timeout and heartbeat monitoring for kanban tasks.

Uses :class:`ACPOrchestratorEventHandler` to detect stalled tasks, then applies
a two-stage escalation:

  Stage 1 (heartbeat timeout): task heartbeat is older than the configured
  heartbeat timeout → the detector queries the profile via the recovery
  handler. If the profile responds, the task is deemed alive and the stall
  is cleared.

  Stage 2 (escalation timeout): task has been in stage-1 without resolution
  for longer than the escalation timeout → the detector escalates via the
  recovery handler (re-route, block, notify).

Completed and cancelled tasks are never flagged — no false positives on
normal completion.

Thread safety: the detector uses a lock for its internal stage tracking.
The underlying ACPOrchestratorEventHandler is itself thread-safe.

Configuration lives in :class:`StallDetectorConfig`.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol

from plugins.bmad.orchestrator.event_handler import (
    ACPOrchestratorEventHandler,
    OrchestratorTaskState,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class RecoveryAction(Enum):
    """Built-in escalation outcomes."""

    BLOCK = "block"       # Block the kanban task with a stall reason
    REROUTE = "reroute"   # Unassign and re-queue the task
    NOTIFY = "notify"     # Log + notify only, no state change
    LOG = "log"           # Log-only, no external side effect


@dataclass
class StallDetectorConfig:
    """Configuration for the stall detector.

    All time values are in seconds.
    """

    # How old a heartbeat must be before stage 1 triggers (default: 5 min)
    heartbeat_timeout_seconds: float = 300.0

    # How long we wait for stage-1 resolution before escalating (default: 10 min)
    # This is total elapsed time, not additional time on top of heartbeat timeout.
    escalation_timeout_seconds: float = 600.0

    # Maximum times a single task can be escalated (prevents infinite loops)
    max_escalations: int = 2

    # Recovery action for stage 2 escalation
    escalation_action: RecoveryAction = RecoveryAction.BLOCK


# ---------------------------------------------------------------------------
# Task stall tracking (per-detector state)
# ---------------------------------------------------------------------------


class StallStage(Enum):
    """Which escalation stage a task is currently in."""

    NONE = "none"        # Not stalled (or hasn't been checked yet)
    STAGE1 = "stage1"    # Heartbeat timeout — awaiting ACP query
    STAGE2 = "stage2"    # Escalation timeout — recovery action applied
    RESOLVED = "resolved"  # Was stalled, now resolved (heartbeat recovered)


@dataclass
class _StallTracker:
    """Per-task state tracked by the detector across multiple check() calls."""

    stage: StallStage = StallStage.NONE
    first_stalled_at: Optional[float] = None   # When stage 1 was first detected
    escalation_count: int = 0
    last_check_at: Optional[float] = None
    last_heartbeat_at: Optional[float] = None
    resolution_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Recovery handler protocol
# ---------------------------------------------------------------------------


class RecoveryHandler(Protocol):
    """Pluggable recovery action handler.

    Implementations perform the actual side effects: kanban comments,
    task blocking, notifications, etc.
    """

    def on_stage1_stall(
        self,
        task_id: str,
        state: OrchestratorTaskState,
        stall_since_seconds: float,
    ) -> None:
        """Called when stage 1 is first detected for a task.

        The detector has already determined the heartbeat is stale.
        Implementations should query the profile or log the event.
        """
        ...

    def on_stage2_escalation(
        self,
        task_id: str,
        state: OrchestratorTaskState,
        stall_since_seconds: float,
        escalation_count: int,
    ) -> None:
        """Called when stage 2 escalation triggers.

        The task has been stalled past the escalation timeout.
        Implementations should re-route, block, or notify.
        """
        ...

    def on_stall_resolved(
        self,
        task_id: str,
        state: OrchestratorTaskState,
        reason: str,
    ) -> None:
        """Called when a previously stalled task resumes heartbeats.

        Implementations can clear any alerts or comments.
        """
        ...


# ---------------------------------------------------------------------------
# Default recovery handler (log-only, safe for tests)
# ---------------------------------------------------------------------------


class LogRecoveryHandler:
    """Logging-only recovery handler — no external side effects."""

    def on_stage1_stall(
        self,
        task_id: str,
        state: OrchestratorTaskState,
        stall_since_seconds: float,
    ) -> None:
        logger.warning(
            "[stall-detector] stage1 task=%s stalled=%.1fs "
            "last_hb=%.1fs iteration=%s",
            task_id,
            stall_since_seconds,
            state.last_heartbeat_at or 0,
            state.iteration,
        )

    def on_stage2_escalation(
        self,
        task_id: str,
        state: OrchestratorTaskState,
        stall_since_seconds: float,
        escalation_count: int,
    ) -> None:
        logger.error(
            "[stall-detector] stage2 escalated task=%s stalled=%.1fs "
            "escalation_count=%d status=%s",
            task_id,
            stall_since_seconds,
            escalation_count,
            state.status,
        )

    def on_stall_resolved(
        self,
        task_id: str,
        state: OrchestratorTaskState,
        reason: str,
    ) -> None:
        logger.info(
            "[stall-detector] resolved task=%s reason=%s",
            task_id,
            reason,
        )


# ---------------------------------------------------------------------------
# Kanban recovery handler
# ---------------------------------------------------------------------------


class KanbanRecoveryHandler:
    """Recovery handler that persists events as kanban comments and blocks.

    Uses deferred imports to avoid hard kanban dependency at module load,
    same pattern as ACPOrchestratorEventHandler._maybe_kanban_comment.
    """

    def __init__(self, kanban_board: str = "default"):
        self._board = kanban_board

    def on_stage1_stall(
        self,
        task_id: str,
        state: OrchestratorTaskState,
        stall_since_seconds: float,
    ) -> None:
        logger.warning(
            "[stall-detector] stage1 (kanban) task=%s stalled=%.1fs",
            task_id,
            stall_since_seconds,
        )
        self._comment(
            task_id,
            f"⚠️ **Stage 1 stall detected** — no heartbeat for "
            f"`{stall_since_seconds:.0f}s`\n"
            f"Last tool: `{state.current_tool or 'unknown'}`\n"
            f"Iteration: {state.iteration or '?'}",
        )

    def on_stage2_escalation(
        self,
        task_id: str,
        state: OrchestratorTaskState,
        stall_since_seconds: float,
        escalation_count: int,
    ) -> None:
        logger.error(
            "[stall-detector] stage2 escalation (kanban) task=%s stalled=%.1fs "
            "count=%d",
            task_id,
            stall_since_seconds,
            escalation_count,
        )
        self._comment(
            task_id,
            f"🚨 **Stage 2 escalation** — stalled for `{stall_since_seconds:.0f}s`\n"
            f"Escalation #{escalation_count}\n"
            f"Status: `{state.status}`\n"
            f"Last heartbeat: {state.last_heartbeat_at or 'never'}",
        )

    def on_stall_resolved(
        self,
        task_id: str,
        state: OrchestratorTaskState,
        reason: str,
    ) -> None:
        logger.info(
            "[stall-detector] resolved (kanban) task=%s reason=%s",
            task_id,
            reason,
        )
        self._comment(
            task_id,
            f"✅ **Stall resolved** — {reason}",
        )

    def _comment(self, task_id: str, body: str) -> None:
        """Persist a kanban comment, silently drop on failure."""
        if not task_id:
            return
        try:
            from hermes_cli.kanban_db import add_comment, get_connection

            conn = get_connection(self._board)
            add_comment(
                conn=conn,
                task_id=task_id,
                author="stall-detector",
                body=body,
            )
        except Exception:
            logger.debug(
                "Failed to persist kanban comment for task %s",
                task_id,
                exc_info=True,
            )


# ---------------------------------------------------------------------------
# Stall detector
# ---------------------------------------------------------------------------


class StallDetector:
    """Two-stage stall detection using ACP orchestrator event handler.

    Usage::

        handler = ACPOrchestratorEventHandler()
        recovery = LogRecoveryHandler()
        detector = StallDetector(
            handler=handler,
            recovery=recovery,
            config=StallDetectorConfig(
                heartbeat_timeout_seconds=300.0,
                escalation_timeout_seconds=600.0,
            ),
        )

        # Called periodically (e.g. cron, Prefect flow, event loop tick)
        results: List[StallCheckResult] = detector.check()

    Each check() call:
      1. Queries handler.get_stalled_tasks() for tasks past heartbeat timeout
      2. For tasks not yet in stage 1: transitions to stage 1, calls
         recovery.on_stage1_stall()
      3. For tasks already in stage 1 past escalation timeout: transitions
         to stage 2, calls recovery.on_stage2_escalation()
      4. For tasks that were stalled but now have fresh heartbeats:
         resolves, calls recovery.on_stall_resolved()
    """

    def __init__(
        self,
        handler: ACPOrchestratorEventHandler,
        recovery: RecoveryHandler | None = None,
        config: StallDetectorConfig | None = None,
    ):
        self._handler = handler
        self._config = config or StallDetectorConfig()
        self._recovery = recovery or LogRecoveryHandler()
        self._lock = threading.Lock()
        # task_id → per-task stall tracking
        self._trackers: Dict[str, _StallTracker] = {}

    # -- Public API ----------------------------------------------------------

    def check(self) -> List[StallCheckResult]:
        """Run one detection cycle.

        Returns:
            List of :class:`StallCheckResult` — one per task that was
            evaluated this cycle (stalled tasks + resolved tasks).
            Completed/cancelled tasks are silently ignored.
        """
        results: List[StallCheckResult] = []
        now = time.time()

        # 1. Get stalled tasks from the handler
        stalled_ids = self._handler.get_stalled_tasks(
            max_age_seconds=self._config.heartbeat_timeout_seconds,
        )
        stalled_set = set(stalled_ids)

        with self._lock:
            # 2. Process each stalled task
            for task_id in stalled_ids:
                result = self._process_stalled(task_id, now)
                if result:
                    results.append(result)

            # 3. Check for resolved tasks (were stalled, now have fresh heartbeat)
            resolved_results = self._process_resolved(stalled_set, now)
            results.extend(resolved_results)

        # 4. Clean up trackers for completed/cancelled tasks
        self._cleanup_completed()

        return results

    def get_tracker(self, task_id: str) -> Optional[_StallTracker]:
        """Return the stall tracker for a task, or None."""
        with self._lock:
            return self._trackers.get(task_id)

    def reset(self) -> None:
        """Clear all tracking state — primarily for tests."""
        with self._lock:
            self._trackers.clear()

    # -- Internal ------------------------------------------------------------

    def _process_stalled(
        self, task_id: str, now: float
    ) -> Optional[StallCheckResult]:
        """Process a single stalled task through the two-stage escalation.

        Returns a result if action was taken, or None if the task was
        already fully escalated.
        """
        tracker = self._trackers.get(task_id)
        state = self._handler.get_task_state(task_id)

        if tracker is None:
            # First time seeing this task as stalled → transition to stage 1
            tracker = _StallTracker(
                stage=StallStage.STAGE1,
                first_stalled_at=now,
                last_check_at=now,
                last_heartbeat_at=state.last_heartbeat_at if state else None,
            )
            self._trackers[task_id] = tracker

            if state:
                stall_age = now - (state.last_heartbeat_at or state.started_at or now)
            else:
                stall_age = 0.0

            try:
                self._recovery.on_stage1_stall(task_id, state or _dummy_state(task_id), stall_age)
            except Exception:
                logger.exception("Recovery handler on_stage1_stall raised for %s", task_id)

            return StallCheckResult(
                task_id=task_id,
                action="stage1",
                stall_age_seconds=stall_age,
                stage=StallStage.STAGE1,
            )

        # Already tracked — check if we should escalate
        tracker.last_check_at = now
        if state:
            tracker.last_heartbeat_at = state.last_heartbeat_at

        # If in stage 1 and past escalation timeout → escalate
        if (
            tracker.stage == StallStage.STAGE1
            and tracker.first_stalled_at is not None
        ):
            stall_since = now - tracker.first_stalled_at
            if (
                stall_since >= self._config.escalation_timeout_seconds
                and tracker.escalation_count < self._config.max_escalations
            ):
                tracker.stage = StallStage.STAGE2
                tracker.escalation_count += 1

                if state:
                    stall_age = now - (state.last_heartbeat_at or state.started_at or now)
                else:
                    stall_age = 0.0

                try:
                    self._recovery.on_stage2_escalation(
                        task_id,
                        state or _dummy_state(task_id),
                        stall_age,
                        tracker.escalation_count,
                    )
                except Exception:
                    logger.exception("Recovery handler on_stage2_escalation raised for %s", task_id)

                return StallCheckResult(
                    task_id=task_id,
                    action=self._config.escalation_action.value,
                    stall_age_seconds=stall_age,
                    stage=StallStage.STAGE2,
                    escalation_count=tracker.escalation_count,
                )

        return None  # No new action needed

    def _process_resolved(
        self, stalled_set: set[str], now: float
    ) -> List[StallCheckResult]:
        """Find tasks that were previously stalled but now have fresh heartbeats."""
        results: List[StallCheckResult] = []
        for task_id, tracker in list(self._trackers.items()):
            if tracker.stage in (StallStage.RESOLVED,):
                continue
            if task_id in stalled_set:
                continue  # Still stalled

            # Check if the task has a fresh heartbeat
            state = self._handler.get_task_state(task_id)
            if state is None:
                # Task removed from handler → probably completed
                tracker.stage = StallStage.RESOLVED
                tracker.resolution_reason = "task removed from handler"
                try:
                    self._recovery.on_stall_resolved(
                        task_id, _dummy_state(task_id), "task removed from handler"
                    )
                except Exception:
                    logger.exception("Recovery handler on_stall_resolved raised for %s", task_id)
                results.append(
                    StallCheckResult(
                        task_id=task_id,
                        action="resolved",
                        stall_age_seconds=0.0,
                        stage=StallStage.RESOLVED,
                    )
                )
                continue

            # Task exists but is no longer "running" → completed normally
            if state.status != "running":
                tracker.stage = StallStage.RESOLVED
                tracker.resolution_reason = f"task status changed to {state.status}"
                try:
                    self._recovery.on_stall_resolved(
                        task_id, state, f"task completed with status {state.status}"
                    )
                except Exception:
                    logger.exception("Recovery handler on_stall_resolved raised for %s", task_id)
                results.append(
                    StallCheckResult(
                        task_id=task_id,
                        action="resolved",
                        stall_age_seconds=0.0,
                        stage=StallStage.RESOLVED,
                    )
                )
                continue

            # Task is running with a fresh heartbeat → resolved
            hb = state.last_heartbeat_at or state.started_at
            hb_age = now - hb if hb else float("inf")
            if hb_age < self._config.heartbeat_timeout_seconds:
                tracker.stage = StallStage.RESOLVED
                tracker.resolution_reason = "heartbeat resumed"
                try:
                    self._recovery.on_stall_resolved(task_id, state, "heartbeat resumed")
                except Exception:
                    logger.exception("Recovery handler on_stall_resolved raised for %s", task_id)
                results.append(
                    StallCheckResult(
                        task_id=task_id,
                        action="resolved",
                        stall_age_seconds=0.0,
                        stage=StallStage.RESOLVED,
                    )
                )

        return results

    def _cleanup_completed(self) -> None:
        """Remove trackers for tasks that are no longer active in the handler."""
        with self._lock:
            for task_id in list(self._trackers):
                state = self._handler.get_task_state(task_id)
                if state is None or state.status in ("completed", "cancelled", "error"):
                    if self._trackers[task_id].stage == StallStage.RESOLVED:
                        del self._trackers[task_id]


# ---------------------------------------------------------------------------
# Check result dataclass
# ---------------------------------------------------------------------------


@dataclass
class StallCheckResult:
    """Result from a single check() call for one task."""

    task_id: str
    action: str  # "stage1", "block", "reroute", "notify", "log", "resolved"
    stall_age_seconds: float
    stage: StallStage
    escalation_count: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dummy_state(task_id: str) -> OrchestratorTaskState:
    """Return a minimal task state for logging when the real one is gone."""
    return OrchestratorTaskState(
        session_id="",
        task_id=task_id,
        status="unknown",
    )
