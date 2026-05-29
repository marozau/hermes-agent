"""BMAD judge — phase gate evaluation with reflection bank cross-referencing.

Exports:
    judge_phase              Full pipeline: load criteria → discover artifacts →
                             validate → evaluate gates → aggregate verdict.
    judge_plan_adjustment    Evaluate whether execution plan needs adjustment
                             based on phase results.
    load_criteria            Load gate criteria for a phase
    check_gate               Evaluate a single gate definition
    evaluate_all_gates       Run all gates for a phase, return structured results
    _RECURRENCE_THRESHOLD    Threshold for flagging recurring patterns (3)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from plugins.bmad.judge.artifact_reader import (
    discover_artifacts,
    summarize_artifacts,
    validate_required_artifacts,
)
from plugins.bmad.judge.critical_thinker import (
    falsify_then_confirm,
    ThinkerVerdict,
)
from plugins.bmad.judge.phase_gates import (
    check_gate,
    evaluate_all_gates,
    load_criteria,
    _RECURRENCE_THRESHOLD,
)

__all__ = [
    # New integration functions
    "judge_phase",
    "judge_plan_adjustment",
    # Re-exports from phase_gates
    "check_gate",
    "evaluate_all_gates",
    "load_criteria",
    "_RECURRENCE_THRESHOLD",
    # Re-exports from critical_thinker
    "falsify_then_confirm",
    "ThinkerVerdict",
]


# ═══════════════════════════════════════════════════════════════════════════
# Judge dispatcher — main entry point
# ═══════════════════════════════════════════════════════════════════════════

def judge_phase(
    phase: str,
    artifacts_dir: str,
    context: dict | None = None,
    criteria_override: str | None = None,
) -> dict:
    """Orchestrate the full judge pipeline for a BMAD phase.

    Pipeline:
        1. Load criteria (phase_gates.load_criteria)
        2. Discover and read artifacts (artifact_reader)
        3. Validate required artifacts exist
        4. For each gate, evaluate with phase_gates + critical_thinker
        5. Aggregate into a JudgeVerdict (PASS / FAIL / CONDITIONAL_PASS)
        6. Return structured result with verdict, confidence, reasoning chain,
           evidence summary, and required fixes.

    Args:
        phase: BMAD phase name ("analysis", "planning", "solutioning",
            "implementation").
        artifacts_dir: Path to the directory containing phase artifacts.
        context: Optional context dict. Supports:
            - ``previous_phase``: str — name of the previous phase
            - ``previous_results``: dict — results from previous phase judge
            - ``project_root``: str — BMAD project root for reflection bank
            - ``profile``: str — Hermes profile name for reflection bank
            - ``test_results``: dict — test execution results (implementation)
            - ``changed_files``: list[str] — files changed (implementation)
        criteria_override: Optional path to a custom criteria.yaml.

    Returns:
        ``{verdict, confidence, reasoning_chain, evidence_summary,
           gate_results, required_fixes, phase, artifacts_found,
           artifacts_missing}``.

        - ``verdict``: "PASS" | "FAIL" | "CONDITIONAL_PASS"
        - ``confidence``: float 0.0–1.0
        - ``required_fixes``: list[str] — empty on PASS
    """
    context = context or {}
    reasoning_chain: list[str] = []
    evidence_items: list[str] = []

    # ── Step 1: Load criteria ───────────────────────────────────────────
    try:
        criteria = load_criteria(phase, criteria_override)
    except (FileNotFoundError, ValueError) as exc:
        return {
            "verdict": "FAIL",
            "confidence": 0.0,
            "reasoning_chain": [f"Failed to load criteria: {exc}"],
            "evidence_summary": "Criteria loading failed",
            "gate_results": [],
            "required_fixes": [f"Fix criteria loading: {exc}"],
            "phase": phase,
            "artifacts_found": [],
            "artifacts_missing": [],
        }

    reasoning_chain.append(
        f"Loaded criteria for phase '{phase}': "
        f"{len(criteria['gates'])} gates, "
        f"{len(criteria['required_artifacts'])} required artifacts"
    )

    # ── Step 2: Discover and read artifacts ─────────────────────────────
    artifacts = discover_artifacts(artifacts_dir)
    reasoning_chain.append(
        f"Discovered {len(artifacts)} artifacts in {artifacts_dir}"
    )
    evidence_items.append(
        f"Artifacts directory: {artifacts_dir} ({len(artifacts)} files)"
    )

    # ── Step 3: Validate required artifacts ─────────────────────────────
    validation = validate_required_artifacts(
        criteria["required_artifacts"], artifacts
    )

    if not validation["all_present"]:
        reasoning_chain.append(
            f"Missing required artifacts: {validation['missing']}"
        )

    # ── Step 4: Evaluate each gate ──────────────────────────────────────
    gate_results: list[dict] = []
    gate_confidences: list[float] = []
    required_fixes: list[str] = []

    for gate in criteria["gates"]:
        gate_id = gate["id"]
        severity = gate.get("severity", "recommended")

        # Build a composite evidence list for this gate
        gate_evidence: list[str] = []
        for key, content in artifacts.items():
            gate_evidence.append(f"[{key}] {content[:500]}")

        # Add context evidence
        if context.get("test_results"):
            gate_evidence.append(
                f"[test_results] {context['test_results']}"
            )
        if context.get("changed_files"):
            gate_evidence.append(
                f"[changed_files] {', '.join(context['changed_files'])}"
            )

        # Phase gate evaluation (fast heuristic check)
        pg_result = check_gate(gate, artifacts, context)

        # Critical thinker (deep falsification-first analysis)
        claim = (
            f"Gate '{gate_id}' ({severity}): {gate['description']} "
            f"is satisfied based on the evidence provided."
        )

        base_rate = 0.7 if severity == "required" else 0.5
        thinker_result = falsify_then_confirm(
            claim=claim,
            evidence=gate_evidence,
            context={
                "gate_id": gate_id,
                "phase": phase,
                "previous_verdict": context.get("previous_results", {}).get("verdict", ""),
            },
            base_rate=base_rate,
        )

        # Synthesize: phase_gates + critical_thinker
        # A gate passes only if BOTH agree (phase_gates heuristic AND thinker survival)
        passed = pg_result["passed"] and thinker_result.verdict != "FAIL"

        gate_result = {
            "gate_id": gate_id,
            "description": gate["description"],
            "severity": severity,
            "passed": passed,
            "phase_gates_result": {
                "passed": pg_result["passed"],
                "evidence": pg_result["evidence"],
                "notes": pg_result.get("notes", ""),
            },
            "thinker_result": {
                "verdict": thinker_result.verdict,
                "confidence": thinker_result.confidence,
                "reasoning_chain": thinker_result.reasoning_chain,
                "weak_points": thinker_result.weak_points,
            },
        }
        gate_results.append(gate_result)
        gate_confidences.append(thinker_result.confidence)

        if not passed:
            required_fixes.append(
                f"[{gate_id}] {pg_result.get('notes', pg_result['evidence'])}"
            )
            if thinker_result.weak_points:
                for wp in thinker_result.weak_points:
                    if wp not in required_fixes:
                        required_fixes.append(f"[{gate_id}/thinker] {wp}")

        reasoning_chain.append(
            f"Gate '{gate_id}': {'✓' if passed else '✗'} "
            f"(pg={'✓' if pg_result['passed'] else '✗'}, "
            f"thinker={thinker_result.verdict}, "
            f"confidence={thinker_result.confidence:.2f})"
        )

    # ── Step 5: Aggregate verdict ───────────────────────────────────────
    required_gates = [g for g in gate_results if g["severity"] == "required"]
    recommended_gates = [g for g in gate_results if g["severity"] == "recommended"]

    all_required_passed = (
        all(g["passed"] for g in required_gates)
        if required_gates
        else True
    )
    recommended_pass_rate = (
        sum(1 for g in recommended_gates if g["passed"]) / len(recommended_gates)
        if recommended_gates
        else 1.0
    )

    avg_confidence = (
        sum(gate_confidences) / len(gate_confidences)
        if gate_confidences
        else 0.0
    )

    if all_required_passed and recommended_pass_rate >= 0.6 and avg_confidence >= 0.7:
        verdict = "PASS"
    elif all_required_passed and (recommended_pass_rate >= 0.4 or avg_confidence >= 0.5):
        verdict = "CONDITIONAL_PASS"
    else:
        verdict = "FAIL"

    reasoning_chain.append(
        f"Verdict: {verdict} | Required gates: "
        f"{sum(1 for g in required_gates if g['passed'])}/{len(required_gates)} | "
        f"Recommended: {recommended_pass_rate:.0%} | "
        f"Avg confidence: {avg_confidence:.2f}"
    )

    # Build required_fixes list (only for CONDITIONAL_PASS and FAIL)
    if verdict == "PASS":
        required_fixes = []

    return {
        "verdict": verdict,
        "confidence": round(avg_confidence, 2),
        "reasoning_chain": reasoning_chain,
        "evidence_summary": (
            f"Phase: {phase} | Artifacts: {len(artifacts)} found, "
            f"{len(validation['missing'])} missing | "
            f"Gates: {sum(1 for g in gate_results if g['passed'])}/"
            f"{len(gate_results)} passed"
        ),
        "gate_results": gate_results,
        "required_fixes": required_fixes,
        "phase": phase,
        "artifacts_found": list(artifacts.keys()),
        "artifacts_missing": validation["missing"],
    }


# ═══════════════════════════════════════════════════════════════════════════
# Plan adjustment evaluation
# ═══════════════════════════════════════════════════════════════════════════

def judge_plan_adjustment(
    original_plan: dict,
    phase_results: dict,
    current_phase: str,
) -> dict:
    """Evaluate whether the execution plan needs adjustment.

    Uses critical_thinker to analyze gaps between the original plan's
    expectations and actual phase results.

    Args:
        original_plan: The original execution plan. Expected keys:
            - ``phases``: list[str] — planned phase sequence
            - ``expectations``: dict — per-phase expected outcomes
            - ``milestones``: list[dict] — planned milestone checkpoints
        phase_results: Actual results from completed phases. Expected shape:
            ``{phase_name: {verdict, confidence, gate_results, ...}}``.
        current_phase: The BMAD phase we're about to start or are currently in.

    Returns:
        ``{recommendation, confidence, rationale, suggested_changes}``.

        - ``recommendation``: "keep" | "adjust" | "replan" | "escalate"
        - ``confidence``: float 0.0–1.0
        - ``rationale``: str — human-readable reasoning
        - ``suggested_changes``: list[str] — actionable recommendations
    """
    reasoning_chain: list[str] = []
    evidence_pieces: list[str] = []

    # ── Analyze plan expectations ───────────────────────────────────────
    planned_phases = original_plan.get("phases", [])
    expectations = original_plan.get("expectations", {})
    milestones = original_plan.get("milestones", [])

    # Collect evidence from phase results
    completed_phases = list(phase_results.keys())
    failed_phases = [
        p for p, r in phase_results.items()
        if r.get("verdict") in ("FAIL", "CONDITIONAL_PASS")
    ]
    passed_phases = [
        p for p, r in phase_results.items()
        if r.get("verdict") == "PASS"
    ]

    # Build evidence for critical_thinker
    evidence_pieces.append(
        f"Planned phases: {planned_phases}"
    )
    evidence_pieces.append(
        f"Completed phases: {completed_phases} "
        f"({len(passed_phases)} passed, {len(failed_phases)} failed/conditional)"
    )

    for phase_name, result in phase_results.items():
        verdict = result.get("verdict", "unknown")
        confidence = result.get("confidence", 0.0)
        missing = result.get("artifacts_missing", [])
        fixes = result.get("required_fixes", [])
        evidence_pieces.append(
            f"[{phase_name}] verdict={verdict}, confidence={confidence:.2f}, "
            f"missing_artifacts={missing}, num_fixes={len(fixes)}"
        )

    # ── Critical thinker evaluation ─────────────────────────────────────
    claim = (
        f"The execution plan for phase '{current_phase}' should proceed "
        f"as originally planned without adjustments. "
        f"The plan expects phases {planned_phases} and has completed "
        f"{completed_phases}. Failed phases: {failed_phases}. "
        f"The current execution trajectory is on track."
    )

    # Base rate: plans rarely survive first contact with reality without adjustment
    base_rate = 0.45

    thinker_result = falsify_then_confirm(
        claim=claim,
        evidence=evidence_pieces,
        context={
            "current_phase": current_phase,
            "failed_phases": failed_phases,
            "completed_phases": completed_phases,
        },
        base_rate=base_rate,
    )

    # ── Determine recommendation ────────────────────────────────────────
    # Decision logic:
    # - No failures, thinker says PASS → keep
    # - One conditional pass, thinker says PASS or CONDITIONAL_PASS → adjust
    # - Failures present, thinker says CONDITIONAL_PASS → replan
    # - Multiple failures, thinker says FAIL → escalate

    num_failures = len(failed_phases)
    num_completed = len(completed_phases)

    suggested_changes: list[str] = []

    if thinker_result.verdict == "PASS" and num_failures == 0:
        recommendation = "keep"
        rationale = (
            f"Plan is on track: {num_completed} phases completed, "
            f"no failures, critical thinker confidence {thinker_result.confidence:.2f}"
        )
    elif thinker_result.verdict in ("PASS", "CONDITIONAL_PASS") and num_failures <= 1:
        recommendation = "adjust"
        rationale = (
            f"Minor course correction needed: {num_failures} phase(s) had issues. "
            f"Thinker confidence: {thinker_result.confidence:.2f}"
        )
        if failed_phases:
            for fp in failed_phases:
                fixes = phase_results[fp].get("required_fixes", [])
                for fix in fixes[:3]:  # top 3 fixes
                    suggested_changes.append(f"[{fp}] {fix}")
    elif thinker_result.verdict == "CONDITIONAL_PASS" or num_failures >= 2:
        recommendation = "replan"
        rationale = (
            f"Significant deviations: {num_failures}/{num_completed} phases had issues. "
            f"Re-evaluate plan before proceeding to '{current_phase}'."
        )
        suggested_changes.append(
            f"Revisit plan assumptions for phase '{current_phase}'"
        )
        for fp in failed_phases:
            suggested_changes.append(
                f"Address {fp} failures before advancing"
            )
    else:
        recommendation = "escalate"
        rationale = (
            f"Plan execution severely off track: "
            f"{num_failures} failed phases. Human intervention recommended."
        )
        suggested_changes.append(
            "Escalate to project lead for replanning decision"
        )

    # Add thinker-identified weak points as suggestions
    for wp in thinker_result.weak_points[:5]:
        if wp not in suggested_changes:
            suggested_changes.append(wp)

    return {
        "recommendation": recommendation,
        "confidence": thinker_result.confidence,
        "rationale": rationale,
        "suggested_changes": suggested_changes,
    }
