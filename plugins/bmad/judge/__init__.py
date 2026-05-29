"""BMAD judge — phase gate evaluation with reflection bank cross-referencing.

Exports:
    load_criteria            Load gate criteria for a phase
    check_gate               Evaluate a single gate definition
    evaluate_all_gates       Run all gates for a phase, return structured results
    _RECURRENCE_THRESHOLD    Threshold for flagging recurring patterns (3)
"""

from plugins.bmad.judge.phase_gates import (
    check_gate,
    evaluate_all_gates,
    load_criteria,
    _RECURRENCE_THRESHOLD,
)

__all__ = [
    "check_gate",
    "evaluate_all_gates",
    "load_criteria",
    "_RECURRENCE_THRESHOLD",
]
