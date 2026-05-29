"""BMAD orchestrator — event handler for ACP lifecycle events.

The orchestrator subscribes to ACP session events emitted by Hermes
profiles and uses them to track task state, detect stalls, and
optionally persist events as kanban comments.

Stall detection uses a two-stage escalation: stage 1 queries the
profile, stage 2 escalates via recovery actions.

Reaction logic processes events according to the orchestrator rules:
success → advance gate, failure → reflection bank, stall → judge.

Periodic runner integrates stall detection + reactions into a single
callable suitable for cron jobs and Prefect flows.
"""

from plugins.bmad.orchestrator.event_handler import (
    ACPOrchestratorEventHandler,
    OrchestratorTaskState,
)
from plugins.bmad.orchestrator.stall_detector import (
    KanbanRecoveryHandler,
    LogRecoveryHandler,
    RecoveryAction,
    RecoveryHandler,
    StallCheckResult,
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
    GateHandler,
    ReflectionHandler,
    JudgeHandler,
    UserForwardHandler,
)
from plugins.bmad.orchestrator.periodic import (
    CheckCycleResult,
    check_stalls,
    reset,
    run_check_cycle,
)

__all__ = [
    # Event handler
    "ACPOrchestratorEventHandler",
    "OrchestratorTaskState",
    # Stall detection
    "KanbanRecoveryHandler",
    "LogRecoveryHandler",
    "RecoveryAction",
    "RecoveryHandler",
    "StallCheckResult",
    "StallDetector",
    "StallDetectorConfig",
    "StallStage",
    # Reactions
    "GateHandler",
    "JudgeHandler",
    "LogGateHandler",
    "LogJudgeHandler",
    "LogReflectionHandler",
    "LogUserHandler",
    "ReactionDispatcher",
    "ReactionResult",
    "ReflectionHandler",
    "UserForwardHandler",
    # Periodic
    "CheckCycleResult",
    "check_stalls",
    "reset",
    "run_check_cycle",
]
