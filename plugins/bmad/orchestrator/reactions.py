"""Orchestrator reaction logic — business rules for ACP lifecycle events.

Consumes events from :class:`ACPOrchestratorEventHandler` and applies
the orchestrator reaction rules specified in the lifecycle hooks design:

    session_end(success=True)  → advance workflow gate
    session_end(success=False) → log to reflection bank
    session_stalled            → check judge for re-routing
    user_question              → forward to user
    session_heartbeat          → update kanban timestamp
    tool_call_error(count>=3)  → flag for judge evaluation

All handlers are pluggable via the :class:`ReactionDispatcher` protocol,
allowing integration with reflection bank, judge, and workflow tracking
as those components mature.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol

from plugins.bmad.orchestrator.event_handler import (
    ACPOrchestratorEventHandler,
    OrchestratorTaskState,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Reaction outcome types
# ---------------------------------------------------------------------------


@dataclass
class ReactionResult:
    """Result of processing one lifecycle event through the reaction rules."""

    task_id: str
    event_type: str
    action: str  # "advance_gate", "log_reflection", "reroute", "block", "notify", etc.
    details: Dict[str, Any] = field(default_factory=dict)
    handler_called: bool = False
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Handler protocols (pluggable integration points)
# ---------------------------------------------------------------------------


class GateHandler(Protocol):
    """Handles workflow gate advancement.

    Called when a session completes successfully — advances the gate
    for the completed task in the workflow DAG.
    """

    def advance_gate(self, task_id: str, result: Dict[str, Any]) -> None:
        """Advance the workflow gate for a completed task.

        Args:
            task_id: The kanban task that completed.
            result: Result metadata from session_end event.
        """
        ...


class ReflectionHandler(Protocol):
    """Handles reflection bank logging.

    Called when a session fails — records the failure for future
    pattern analysis and adjustment suggestions.
    """

    def log_failure(
        self,
        task_id: str,
        outcome: str,
        error_details: Dict[str, Any],
    ) -> str:
        """Log a session failure to the reflection bank.

        Returns:
            A reflection entry id.
        """
        ...


class JudgeHandler(Protocol):
    """Handles orchestrator judge evaluation.

    Called when a task stalls or accumulates tool errors — triggers
    the judge to evaluate re-routing or plan adjustment.
    """

    def evaluate_reroute(
        self, task_id: str, state: OrchestratorTaskState, reason: str
    ) -> Dict[str, Any]:
        """Evaluate whether to re-route a problematic task.

        Returns:
            Judge decision dict with keys: ``action`` (reroute/block/retry/continue),
            ``reason``, ``suggested_assignee``.
        """
        ...


class UserForwardHandler(Protocol):
    """Handles forwarding user questions from profile sessions.

    Called when a profile session asks a question the orchestrator
    should relay to the human operator.
    """

    def forward_question(
        self,
        task_id: str,
        question_text: str,
        question_id: str,
        options: Optional[List[str]] = None,
    ) -> None:
        """Forward a user question to the human operator.

        Args:
            task_id: The task where the question originated.
            question_text: The question to forward.
            question_id: Unique id for correlating with answer.
            options: Optional multiple-choice options.
        """
        ...


# ---------------------------------------------------------------------------
# Reaction dispatcher
# ---------------------------------------------------------------------------


class ReactionDispatcher:
    """Applies orchestrator reaction rules to ACP session events.

    Wired between the :class:`ACPOrchestratorEventHandler` and the
    pluggable handler implementations.  Each ACP event is evaluated
    against the reaction rules and dispatched to the appropriate handler.

    All handler calls are wrapped — a failing handler must not prevent
    other reactions from processing.
    """

    def __init__(
        self,
        handler: ACPOrchestratorEventHandler,
        gate_handler: Optional[GateHandler] = None,
        reflection_handler: Optional[ReflectionHandler] = None,
        judge_handler: Optional[JudgeHandler] = None,
        user_handler: Optional[UserForwardHandler] = None,
    ):
        self._handler = handler
        self._gate = gate_handler
        self._reflection = reflection_handler
        self._judge = judge_handler
        self._user = user_handler

        # Reaction counts for stats
        self._reaction_counts: Dict[str, int] = {}

    # -- Public API ----------------------------------------------------------

    def react_to_ended_tasks(self) -> List[ReactionResult]:
        """Check for recently-ended tasks and apply reaction rules.

        Scans all tracked tasks for those that have ended (completed,
        error, cancelled) and applies the appropriate reactions.

        Returns:
            List of ReactionResult, one per ended task processed.
        """
        results: List[ReactionResult] = []
        stats = self._handler.stats()
        total = stats.get("total_tasks", 0)
        if total == 0:
            return results

        # We iterate all tasks — this is a sparse check, called periodically
        for task_id in list(self._handler._tasks.keys()):
            state = self._handler.get_task_state(task_id)
            if not state:
                continue
            if state.status not in ("completed", "error", "cancelled"):
                continue

            result = self._react_to_session_end(state)
            if result:
                results.append(result)

        return results

    def react_to_stalled_tasks(
        self, max_age_seconds: float = 300.0
    ) -> List[ReactionResult]:
        """Check for stalled tasks and apply reaction rules.

        Args:
            max_age_seconds: Heartbeat age threshold for stall detection.

        Returns:
            List of ReactionResult, one per stalled task.
        """
        stalled_ids = self._handler.get_stalled_tasks(max_age_seconds)
        results: List[ReactionResult] = []

        for task_id in stalled_ids:
            state = self._handler.get_task_state(task_id)
            if not state:
                continue
            result = self._react_to_stall(state)
            if result:
                results.append(result)

        return results

    def react_to_tool_errors(
        self, min_errors: int = 3
    ) -> List[ReactionResult]:
        """Check for tasks with accumulated tool errors and flag for judge.

        Args:
            min_errors: Minimum consecutive tool errors to trigger reaction.

        Returns:
            List of ReactionResult, one per task with error threshold exceeded.
        """
        results: List[ReactionResult] = []

        for task_id in list(self._handler._tasks.keys()):
            state = self._handler.get_task_state(task_id)
            if not state or state.status != "running":
                continue

            # Count recent errors in tool_results
            errors = [r for r in state.tool_results if not r.get("success", True)]
            if len(errors) >= min_errors:
                result = self._react_to_tool_errors(state, len(errors))
                if result:
                    results.append(result)

        return results

    def react_to_user_questions(self) -> List[ReactionResult]:
        """Check for tasks with unanswered user questions.

        Returns:
            List of ReactionResult, one per task with pending questions.
        """
        results: List[ReactionResult] = []

        for task_id in list(self._handler._tasks.keys()):
            state = self._handler.get_task_state(task_id)
            if not state or not state.user_questions:
                continue

            # Only process the most recent question for each task
            last_q = state.user_questions[-1]
            result = self._react_to_question(state, last_q)
            if result:
                results.append(result)

        return results

    def process_all_reactions(
        self,
        stall_max_age: float = 300.0,
        tool_error_threshold: int = 3,
    ) -> Dict[str, List[ReactionResult]]:
        """Run all reaction checks and return combined results.

        Args:
            stall_max_age: Heartbeat age for stall detection.
            tool_error_threshold: Min errors to trigger judge.

        Returns:
            Dict mapping category → list of ReactionResult.
        """
        return {
            "ended": self.react_to_ended_tasks(),
            "stalled": self.react_to_stalled_tasks(stall_max_age),
            "tool_errors": self.react_to_tool_errors(tool_error_threshold),
            "questions": self.react_to_user_questions(),
        }

    def stats(self) -> Dict[str, Any]:
        """Return dispatcher statistics."""
        return {
            "handler_stats": self._handler.stats(),
            "reaction_counts": dict(self._reaction_counts),
            "handlers": {
                "gate": self._gate is not None,
                "reflection": self._reflection is not None,
                "judge": self._judge is not None,
                "user": self._user is not None,
            },
        }

    # -- Reaction rules ------------------------------------------------------

    def _react_to_session_end(
        self, state: OrchestratorTaskState
    ) -> Optional[ReactionResult]:
        """Apply reaction rules for a completed/failed/cancelled session."""
        outcome = state.outcome or state.status

        if outcome == "completed":
            return self._on_success(state)
        elif outcome in ("error", "cancelled", "timeout"):
            return self._on_failure(state, outcome)
        return None

    def _on_success(
        self, state: OrchestratorTaskState
    ) -> ReactionResult:
        """session_end(success=True) → advance workflow gate."""
        self._inc("success")
        result = ReactionResult(
            task_id=state.task_id,
            event_type="session_end",
            action="advance_gate",
            details={"outcome": state.outcome, "elapsed": state.ended_at},
        )

        if self._gate:
            try:
                self._gate.advance_gate(
                    state.task_id,
                    {
                        "outcome": state.outcome,
                        "elapsed_s": (
                            (state.ended_at - state.started_at)
                            if state.ended_at and state.started_at
                            else 0
                        ),
                        "tool_results": len(state.tool_results),
                        "user_questions": len(state.user_questions),
                    },
                )
                result.handler_called = True
            except Exception as e:
                logger.exception(
                    "Gate handler advance_gate raised for %s", state.task_id
                )
                result.error = str(e)

        return result

    def _on_failure(
        self, state: OrchestratorTaskState, outcome: str
    ) -> ReactionResult:
        """session_end(failed) → log to reflection bank."""
        self._inc("failure")
        result = ReactionResult(
            task_id=state.task_id,
            event_type="session_end",
            action="log_reflection",
            details={"outcome": outcome, "elapsed": state.ended_at},
        )

        if self._reflection:
            try:
                error_details = {
                    "outcome": outcome,
                    "elapsed_s": (
                        (state.ended_at - state.started_at)
                        if state.ended_at and state.started_at
                        else 0
                    ),
                    "tool_results_count": len(state.tool_results),
                    "tool_errors": [
                        r for r in state.tool_results
                        if not r.get("success", True)
                    ],
                    "user_questions_count": len(state.user_questions),
                    "last_tool": state.current_tool,
                    "iteration": state.iteration,
                }
                entry_id = self._reflection.log_failure(
                    state.task_id, outcome, error_details
                )
                result.details["reflection_id"] = entry_id
                result.handler_called = True
            except Exception as e:
                logger.exception(
                    "Reflection handler log_failure raised for %s", state.task_id
                )
                result.error = str(e)

        return result

    def _react_to_stall(
        self, state: OrchestratorTaskState
    ) -> ReactionResult:
        """session_stalled → check judge for re-routing."""
        self._inc("stalled")
        now = time.time()
        stall_age = now - (state.last_heartbeat_at or state.started_at or now)

        result = ReactionResult(
            task_id=state.task_id,
            event_type="session_stalled",
            action="evaluate_reroute",
            details={
                "stall_age_s": stall_age,
                "current_tool": state.current_tool,
                "iteration": state.iteration,
            },
        )

        if self._judge:
            try:
                judge_result = self._judge.evaluate_reroute(
                    state.task_id,
                    state,
                    f"Stalled for {stall_age:.0f}s, last tool: {state.current_tool}",
                )
                result.details["judge_decision"] = judge_result
                result.handler_called = True
            except Exception as e:
                logger.exception(
                    "Judge handler evaluate_reroute raised for %s", state.task_id
                )
                result.error = str(e)

        return result

    def _react_to_tool_errors(
        self, state: OrchestratorTaskState, error_count: int
    ) -> ReactionResult:
        """tool_call_error(count>=3) → flag for judge evaluation."""
        self._inc("tool_errors")
        errors = [
            r for r in state.tool_results
            if not r.get("success", True)
        ]

        result = ReactionResult(
            task_id=state.task_id,
            event_type="tool_call_error",
            action="evaluate_adjust",
            details={
                "error_count": error_count,
                "latest_errors": errors[-5:],  # last 5 errors
                "current_tool": state.current_tool,
            },
        )

        if self._judge:
            try:
                judge_result = self._judge.evaluate_reroute(
                    state.task_id,
                    state,
                    f"Accumulated {error_count} tool errors",
                )
                result.details["judge_decision"] = judge_result
                result.handler_called = True
            except Exception as e:
                logger.exception(
                    "Judge handler evaluate_reroute raised for %s", state.task_id
                )
                result.error = str(e)

        return result

    def _react_to_question(
        self,
        state: OrchestratorTaskState,
        question: Dict[str, Any],
    ) -> ReactionResult:
        """user_question → forward to user."""
        self._inc("question")
        result = ReactionResult(
            task_id=state.task_id,
            event_type="user_question",
            action="forward_to_user",
            details={
                "question_id": question.get("question_id", ""),
                "question_text": question.get("question_text", ""),
                "options": question.get("options"),
            },
        )

        if self._user:
            try:
                self._user.forward_question(
                    task_id=state.task_id,
                    question_text=question.get("question_text", ""),
                    question_id=question.get("question_id", ""),
                    options=question.get("options"),
                )
                result.handler_called = True
            except Exception as e:
                logger.exception(
                    "User handler forward_question raised for %s", state.task_id
                )
                result.error = str(e)

        return result

    def _inc(self, category: str) -> None:
        """Increment a reaction counter."""
        self._reaction_counts[category] = (
            self._reaction_counts.get(category, 0) + 1
        )


# ---------------------------------------------------------------------------
# Default (log-only) handler implementations
# ---------------------------------------------------------------------------


class LogGateHandler:
    """Gate handler that logs rather than modifying workflow state."""

    def advance_gate(self, task_id: str, result: Dict[str, Any]) -> None:
        logger.info(
            "[orchestrator:gate] advance_gate task=%s outcome=%s elapsed=%.1fs",
            task_id,
            result.get("outcome"),
            result.get("elapsed_s", 0),
        )


class LogReflectionHandler:
    """Reflection handler that logs failures."""

    def log_failure(
        self,
        task_id: str,
        outcome: str,
        error_details: Dict[str, Any],
    ) -> str:
        logger.warning(
            "[orchestrator:reflection] log_failure task=%s outcome=%s "
            "errors=%d questions=%d",
            task_id,
            outcome,
            len(error_details.get("tool_errors", [])),
            error_details.get("user_questions_count", 0),
        )
        return f"log-{task_id}-{int(time.time())}"


class LogJudgeHandler:
    """Judge handler that logs stall/error evaluations."""

    def evaluate_reroute(
        self, task_id: str, state: OrchestratorTaskState, reason: str
    ) -> Dict[str, Any]:
        logger.warning(
            "[orchestrator:judge] evaluate task=%s reason=%s status=%s",
            task_id,
            reason[:100],
            state.status,
        )
        return {
            "action": "continue",  # default: don't reroute without real judge
            "reason": "log-only handler — no real judge wired",
            "suggested_assignee": None,
        }


class LogUserHandler:
    """User handler that logs questions."""

    def forward_question(
        self,
        task_id: str,
        question_text: str,
        question_id: str,
        options: Optional[List[str]] = None,
    ) -> None:
        logger.info(
            "[orchestrator:user] question task=%s qid=%s text=%s",
            task_id,
            question_id,
            question_text[:200],
        )
