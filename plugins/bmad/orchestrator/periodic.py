"""Periodic stall check and reaction runner.

Provides a self-contained callable suitable for cron jobs, Prefect
flows, or event-loop tick handlers.  Runs one detection + reaction
cycle against the in-process orchestrator state.

Usage as cron (from a Hermes session):
    cronjob action='create' schedule='5m' \\
        prompt="Run the stall checker. Load the 'bmad-lifecycle-periodic' skill."

The cron job loads this module and calls `run_check_cycle()`.

Usage as Prefect flow:
    from plugins.bmad.orchestrator.periodic import run_check_cycle
    result = run_check_cycle()

The module is designed for single-process, single-orchestrator use.
For multi-process setups, persist state via kanban or a shared store.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from plugins.bmad.orchestrator.event_handler import ACPOrchestratorEventHandler
from plugins.bmad.orchestrator.stall_detector import (
    KanbanRecoveryHandler,
    LogRecoveryHandler,
    RecoveryAction,
    StallDetector,
    StallDetectorConfig,
    StallStage,
)
from plugins.bmad.orchestrator.reactions import (
    LogGateHandler,
    LogJudgeHandler,
    LogReflectionHandler,
    LogUserHandler,
    ReactionDispatcher,
    ReactionResult,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------
# These are initialized once at module load and reused across check cycles.
# In a long-lived orchestrator process, they accumulate state naturally.
# In cron-style ephemeral processes, each tick starts fresh — persistent
# state is maintained via kanban comments.

_event_handler: Optional[ACPOrchestratorEventHandler] = None
_stall_detector: Optional[StallDetector] = None
_reaction_dispatcher: Optional[ReactionDispatcher] = None


# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------

DEFAULT_HEARTBEAT_TIMEOUT = 300.0   # 5 minutes
DEFAULT_ESCALATION_TIMEOUT = 600.0  # 10 minutes
DEFAULT_MAX_ESCALATIONS = 2
DEFAULT_ESCALATION_ACTION = RecoveryAction.BLOCK


# ---------------------------------------------------------------------------
# Check result
# ---------------------------------------------------------------------------


@dataclass
class CheckCycleResult:
    """Result of a single check cycle."""

    timestamp: float = field(default_factory=time.time)
    elapsed_ms: float = 0.0
    detector_results: List[Dict[str, Any]] = field(default_factory=list)
    reaction_results: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    handler_stats: Dict[str, Any] = field(default_factory=dict)
    detector_stats: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def has_escalations(self) -> bool:
        """True if any stage-2 escalations occurred in this cycle."""
        return any(
            r.get("stage") == StallStage.STAGE2.value
            for r in self.detector_results
        )

    def has_stalls(self) -> bool:
        """True if any stalls (stage 1 or stage 2) were detected."""
        return any(
            r.get("stage") in (StallStage.STAGE1.value, StallStage.STAGE2.value)
            for r in self.detector_results
        )

    def to_summary(self) -> str:
        """Return a compact human-readable summary."""
        parts = []
        if self.detector_results:
            stage1 = sum(
                1 for r in self.detector_results
                if r.get("stage") == StallStage.STAGE1.value
            )
            stage2 = sum(
                1 for r in self.detector_results
                if r.get("stage") == StallStage.STAGE2.value
            )
            resolved = sum(
                1 for r in self.detector_results
                if r.get("stage") == StallStage.RESOLVED.value
            )
            if stage1:
                parts.append(f"{stage1} stage-1 stall(s)")
            if stage2:
                parts.append(f"{stage2} stage-2 escalation(s)")
            if resolved:
                parts.append(f"{resolved} resolved")

        reaction_summary = self._reaction_summary()
        if reaction_summary:
            parts.append(reaction_summary)

        if self.errors:
            parts.append(f"{len(self.errors)} error(s)")

        status = " ⚠️".join(parts) if parts else "✅ all clear"
        elapsed = f" ({self.elapsed_ms:.0f}ms)" if self.elapsed_ms else ""
        return f"Stall check:{elapsed} {status}"

    def _reaction_summary(self) -> str:
        """Compact summary of reaction actions."""
        actions: Dict[str, int] = {}
        for category, results in self.reaction_results.items():
            for r in results:
                action = r.get("action", "unknown")
                actions[action] = actions.get(action, 0) + 1
        return ", ".join(f"{v} {k}" for k, v in sorted(actions.items()))


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


def _ensure_initialized(
    heartbeat_timeout: float = DEFAULT_HEARTBEAT_TIMEOUT,
    escalation_timeout: float = DEFAULT_ESCALATION_TIMEOUT,
    max_escalations: int = DEFAULT_MAX_ESCALATIONS,
    escalation_action: RecoveryAction = DEFAULT_ESCALATION_ACTION,
    kanban_comments: bool = True,
    kanban_board: str = "default",
) -> None:
    """Initialize module-level singletons if not already created."""
    global _event_handler, _stall_detector, _reaction_dispatcher

    if _event_handler is None:
        _event_handler = ACPOrchestratorEventHandler(
            kanban_comments_enabled=kanban_comments,
            kanban_board=kanban_board,
        )

    if _stall_detector is None:
        config = StallDetectorConfig(
            heartbeat_timeout_seconds=heartbeat_timeout,
            escalation_timeout_seconds=escalation_timeout,
            max_escalations=max_escalations,
            escalation_action=escalation_action,
        )
        recovery = KanbanRecoveryHandler(kanban_board=kanban_board)
        _stall_detector = StallDetector(
            handler=_event_handler,
            recovery=recovery,
            config=config,
        )

    if _reaction_dispatcher is None:
        _reaction_dispatcher = ReactionDispatcher(
            handler=_event_handler,
            gate_handler=LogGateHandler(),
            reflection_handler=LogReflectionHandler(),
            judge_handler=LogJudgeHandler(),
            user_handler=LogUserHandler(),
        )


def reset() -> None:
    """Reset module-level state — primarily for tests."""
    global _event_handler, _stall_detector, _reaction_dispatcher
    if _event_handler:
        _event_handler.reset()
    if _stall_detector:
        _stall_detector.reset()
    _event_handler = None
    _stall_detector = None
    _reaction_dispatcher = None


# ---------------------------------------------------------------------------
# Public API — callable from cron / Prefect
# ---------------------------------------------------------------------------


def run_check_cycle(
    heartbeat_timeout: float = DEFAULT_HEARTBEAT_TIMEOUT,
    escalation_timeout: float = DEFAULT_ESCALATION_TIMEOUT,
    max_escalations: int = DEFAULT_MAX_ESCALATIONS,
    escalation_action: RecoveryAction = DEFAULT_ESCALATION_ACTION,
    kanban_comments: bool = True,
    kanban_board: str = "default",
) -> CheckCycleResult:
    """Run one detection + reaction cycle.

    This is the primary entry point for cron jobs and Prefect flows.
    It initializes singletons on first call, runs stall detection,
    applies reaction rules, and returns a structured result.

    Returns:
        CheckCycleResult with detection findings and reaction outcomes.
    """
    t0 = time.time()
    result = CheckCycleResult()
    errors: List[str] = []

    try:
        _ensure_initialized(
            heartbeat_timeout=heartbeat_timeout,
            escalation_timeout=escalation_timeout,
            max_escalations=max_escalations,
            escalation_action=escalation_action,
            kanban_comments=kanban_comments,
            kanban_board=kanban_board,
        )
    except Exception as e:
        logger.exception("Failed to initialize periodic checker")
        result.errors.append(f"init: {e}")
        result.elapsed_ms = (time.time() - t0) * 1000
        return result

    # 1. Run stall detection
    try:
        detector_results = _stall_detector.check() if _stall_detector else []
        result.detector_results = [
            {
                "task_id": r.task_id,
                "action": r.action,
                "stage": r.stage.value,
                "stall_age_seconds": r.stall_age_seconds,
                "escalation_count": r.escalation_count,
            }
            for r in detector_results
        ]
    except Exception as e:
        logger.exception("Stall detection failed")
        errors.append(f"detection: {e}")

    # 2. Run reaction logic
    try:
        if _reaction_dispatcher:
            all_reactions = _reaction_dispatcher.process_all_reactions(
                stall_max_age=heartbeat_timeout,
            )
            result.reaction_results = {
                category: [
                    {
                        "task_id": r.task_id,
                        "event_type": r.event_type,
                        "action": r.action,
                        "details": r.details,
                        "handler_called": r.handler_called,
                        "error": r.error,
                    }
                    for r in reaction_list
                ]
                for category, reaction_list in all_reactions.items()
                if reaction_list
            }
    except Exception as e:
        logger.exception("Reaction dispatch failed")
        errors.append(f"reactions: {e}")

    # 3. Collect stats
    try:
        if _event_handler:
            result.handler_stats = _event_handler.stats()
        if _stall_detector:
            stall_stats = {}
            for task_id in list(_stall_detector._trackers.keys()):
                tracker = _stall_detector.get_tracker(task_id)
                if tracker:
                    stall_stats[task_id] = {
                        "stage": tracker.stage.value,
                        "first_stalled_at": tracker.first_stalled_at,
                        "escalation_count": tracker.escalation_count,
                        "resolution_reason": tracker.resolution_reason,
                    }
            result.detector_stats = {
                "tracked_tasks": len(stall_stats),
                "by_stage": {
                    stage.value: sum(
                        1 for t in stall_stats.values()
                        if t["stage"] == stage.value
                    )
                    for stage in StallStage
                },
            }
    except Exception as e:
        logger.exception("Stats collection failed")
        errors.append(f"stats: {e}")

    result.errors = errors
    result.elapsed_ms = (time.time() - t0) * 1000
    return result


# ---------------------------------------------------------------------------
# Legacy-compatible thin wrapper for cron-script imports
# ---------------------------------------------------------------------------


def check_stalls() -> str:
    """Legacy entry point: run check cycle and return summary string.

    Suitable for cron jobs that expect a text output:
        python -c "from plugins.bmad.orchestrator.periodic import check_stalls; print(check_stalls())"
    """
    result = run_check_cycle()
    logger.info(result.to_summary())
    return result.to_summary()
