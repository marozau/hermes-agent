"""
judge_tools.py — Hermes tool registrations for BMAD judge.

Defines the OpenAI function-calling schemas and handler functions
for the bmad_judge_phase and bmad_judge_plan tools.

These are consumed by plugins/bmad/__init__.py:register() which calls
ctx.register_tool() for each tool.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Schema: bmad_judge_phase
# ═══════════════════════════════════════════════════════════════════════════

BMAD_JUDGE_PHASE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "bmad_judge_phase",
        "description": (
            "Run the BMAD judge pipeline on a completed phase. Loads gate criteria, "
            "discovers artifacts, evaluates every gate using heuristic checks + "
            "falsification-first reasoning, and returns a structured verdict "
            "(PASS/FAIL/CONDITIONAL_PASS) with confidence, reasoning chain, "
            "and required fixes. Call this when a BMAD phase has completed to "
            "validate readiness before advancing to the next phase."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "phase": {
                    "type": "string",
                    "enum": ["analysis", "planning", "solutioning", "implementation"],
                    "description": (
                        "BMAD phase to judge. One of: analysis, planning, "
                        "solutioning, implementation."
                    ),
                },
                "artifacts_dir": {
                    "type": "string",
                    "description": (
                        "Path to the directory containing phase artifacts. "
                        "Defaults to '.hermes/artifacts/' relative to the "
                        "project root when omitted."
                    ),
                },
                "context": {
                    "type": "object",
                    "description": (
                        "Optional context dict with previous phase results, "
                        "test results, changed files, and reflection bank "
                        "configuration."
                    ),
                    "properties": {
                        "previous_phase": {
                            "type": "string",
                            "description": "Name of the previous phase.",
                        },
                        "previous_results": {
                            "type": "object",
                            "description": "Results from the previous phase's judge call.",
                        },
                        "test_results": {
                            "type": "object",
                            "description": (
                                "Test execution results. Expected keys: "
                                "passed (int), failed (int), total (int)."
                            ),
                        },
                        "changed_files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of file paths changed during implementation.",
                        },
                        "profile": {
                            "type": "string",
                            "description": "Hermes profile name for reflection bank queries.",
                        },
                        "project_root": {
                            "type": "string",
                            "description": "BMAD project root for reflection bank queries.",
                        },
                    },
                },
                "criteria_override": {
                    "type": "string",
                    "description": (
                        "Optional path to a custom criteria.yaml file. "
                        "When omitted, the default bundled criteria is used."
                    ),
                },
            },
            "required": ["phase"],
        },
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# Schema: bmad_judge_plan
# ═══════════════════════════════════════════════════════════════════════════

BMAD_JUDGE_PLAN_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "bmad_judge_plan",
        "description": (
            "Evaluate whether the BMAD execution plan needs adjustment. "
            "Compares the original plan's expectations against actual phase "
            "results and returns a recommendation: keep (on track), adjust "
            "(minor course correction), replan (significant deviation), or "
            "escalate (human intervention needed). Call this when a phase "
            "has produced CONDITIONAL_PASS or FAIL results, or before "
            "starting a new phase to validate the plan is still viable."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "original_plan": {
                    "type": "object",
                    "description": (
                        "The original execution plan. Expected keys: "
                        "phases (list of phase names), expectations "
                        "(per-phase expected outcomes), milestones "
                        "(planned milestone checkpoints)."
                    ),
                },
                "phase_results": {
                    "type": "object",
                    "description": (
                        "Actual results from completed phases. Shape: "
                        "{phase_name: {verdict, confidence, gate_results, "
                        "artifacts_found, artifacts_missing, required_fixes}}. "
                        "Use the output from bmad_judge_phase for each phase."
                    ),
                },
                "current_phase": {
                    "type": "string",
                    "description": (
                        "The BMAD phase currently being considered or about "
                        "to start (e.g., 'implementation')."
                    ),
                },
            },
            "required": ["original_plan", "phase_results", "current_phase"],
        },
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# Handler: bmad_judge_phase
# ═══════════════════════════════════════════════════════════════════════════

def _handle_bmad_judge_phase(args: dict[str, Any]) -> str:
    """Handle bmad_judge_phase tool calls.

    Calls judge_phase from the judge module and returns results as JSON.
    """
    try:
        from plugins.bmad.judge import judge_phase
    except ImportError as exc:
        logger.exception("Failed to import judge_phase")
        return json.dumps({
            "verdict": "FAIL",
            "confidence": 0.0,
            "reasoning_chain": [f"Import error: {exc}"],
            "required_fixes": [f"Judge module not available: {exc}"],
        })

    phase = args.get("phase", "")
    artifacts_dir = args.get("artifacts_dir", ".hermes/artifacts/")
    context = args.get("context") or {}
    criteria_override = args.get("criteria_override")

    try:
        result = judge_phase(
            phase=phase,
            artifacts_dir=artifacts_dir,
            context=context,
            criteria_override=criteria_override,
        )
        return json.dumps(result, indent=2, default=str)
    except Exception as exc:
        logger.exception("judge_phase raised")
        return json.dumps({
            "verdict": "FAIL",
            "confidence": 0.0,
            "reasoning_chain": [f"judge_phase raised {type(exc).__name__}: {exc}"],
            "required_fixes": [f"Fix judge execution error: {exc}"],
        })


# ═══════════════════════════════════════════════════════════════════════════
# Handler: bmad_judge_plan
# ═══════════════════════════════════════════════════════════════════════════

def _handle_bmad_judge_plan(args: dict[str, Any]) -> str:
    """Handle bmad_judge_plan tool calls.

    Calls judge_plan_adjustment from the judge module and returns results as JSON.
    """
    try:
        from plugins.bmad.judge import judge_plan_adjustment
    except ImportError as exc:
        logger.exception("Failed to import judge_plan_adjustment")
        return json.dumps({
            "recommendation": "escalate",
            "confidence": 0.0,
            "rationale": f"Import error: {exc}",
            "suggested_changes": [f"Judge module not available: {exc}"],
        })

    original_plan = args.get("original_plan", {})
    phase_results = args.get("phase_results", {})
    current_phase = args.get("current_phase", "")

    try:
        result = judge_plan_adjustment(
            original_plan=original_plan,
            phase_results=phase_results,
            current_phase=current_phase,
        )
        return json.dumps(result, indent=2, default=str)
    except Exception as exc:
        logger.exception("judge_plan_adjustment raised")
        return json.dumps({
            "recommendation": "escalate",
            "confidence": 0.0,
            "rationale": f"judge_plan_adjustment raised {type(exc).__name__}: {exc}",
            "suggested_changes": [f"Fix plan evaluation error: {exc}"],
        })
