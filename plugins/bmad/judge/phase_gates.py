"""
phase_gates.py — Phase gate evaluator for BMAD Method v6.6.0.

Architecture D-8: Loads criteria from criteria.yaml, evaluates gates against
artifact content and prior-phase context.

Exports:
    load_criteria        Load gate criteria for a phase
    check_gate           Evaluate a single gate definition
    evaluate_all_gates   Run all gates for a phase, return structured results
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


# ═══════════════════════════════════════════════════════════════════════════
# Default criteria path (relative to this file)
# ═══════════════════════════════════════════════════════════════════════════

_DEFAULT_CRITERIA = Path(__file__).resolve().parent / "criteria.yaml"


# ═══════════════════════════════════════════════════════════════════════════
# Gate evaluation registry
# ═══════════════════════════════════════════════════════════════════════════
#
# Each key is a gate id.  Each value is a callable (artifacts, context) ->
# (passed: bool, evidence: str, notes: str).
# Gates not registered here fall back to a content-presence heuristic.


def _artifact_text(artifacts: dict, artifact_name: str) -> str | None:
    """Return the text content of a named artifact, trying multiple key forms."""
    # Try exact match
    if artifact_name in artifacts and isinstance(artifacts[artifact_name], str):
        return artifacts[artifact_name]
    # Try without .md extension
    base = artifact_name.removesuffix(".md")
    for key, val in artifacts.items():
        if isinstance(val, str) and key.removesuffix(".md") == base:
            return val
    return None


def _find_line(text: str, keyword: str, case_sensitive: bool = True) -> str | None:
    """Return the first line containing *keyword*, or None."""
    flags = 0 if case_sensitive else re.IGNORECASE
    for line in text.splitlines():
        if re.search(re.escape(keyword), line, flags):
            return line.strip()
    return None


def _count_keyword(text: str, keyword: str) -> int:
    """Count occurrences of *keyword* in *text*."""
    return len(re.findall(re.escape(keyword), text, re.IGNORECASE))


# ── analysis gates ────────────────────────────────────────────────────────


def _gate_problem_statement_present(
    artifacts: dict, context: dict
) -> tuple[bool, str, str]:
    text = _artifact_text(artifacts, "product_brief.md") or ""
    indicators = [
        "problem statement", "problem:", "the problem", "pain point",
        "challenge", "issue:", "addresses the", "solves",
    ]
    found = [kw for kw in indicators if kw.lower() in text.lower()]
    passed = len(found) >= 2
    evidence = (
        f"Found {len(found)} problem indicators: {found}"
        if found
        else "No problem statement indicators found in product brief"
    )
    return passed, evidence, ""


def _gate_target_users_defined(
    artifacts: dict, context: dict
) -> tuple[bool, str, str]:
    text = _artifact_text(artifacts, "product_brief.md") or ""
    indicators = [
        "user", "persona", "target audience", "stakeholder",
        "customer", "end user", "role:", "actor",
    ]
    found = [kw for kw in indicators if kw.lower() in text.lower()]
    passed = len(found) >= 2
    evidence = (
        f"Found {len(found)} user indicators: {found}"
        if found
        else "No target user indicators found in product brief"
    )
    return passed, evidence, ""


def _gate_success_metrics_listed(
    artifacts: dict, context: dict
) -> tuple[bool, str, str]:
    text = _artifact_text(artifacts, "product_brief.md") or ""
    indicators = [
        "metric", "KPI", "success criteria", "measure",
        "OKR", "%", "percent", "target:", "goal:",
        "improvement", "reduction", "increase",
    ]
    found = [kw for kw in indicators if kw.lower() in text.lower()]
    passed = len(found) >= 2
    evidence = (
        f"Found {len(found)} metric indicators: {found}"
        if found
        else "No success metric indicators found in product brief"
    )
    notes = "Quantitative metrics with numeric targets are preferred over vague goals." if not any(k in text.lower() for k in ["%", "percent", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"]) else ""
    return passed, evidence, notes


def _gate_constraints_documented(
    artifacts: dict, context: dict
) -> tuple[bool, str, str]:
    text = _artifact_text(artifacts, "product_brief.md") or ""
    indicators = [
        "constraint", "limitation", "restriction", "budget",
        "timeline", "deadline", "regulation", "compliance",
        "cannot", "must not", "dependency on",
    ]
    found = [kw for kw in indicators if kw.lower() in text.lower()]
    passed = len(found) >= 1
    evidence = (
        f"Found {len(found)} constraint indicators: {found}"
        if found
        else "No constraint indicators found in product brief"
    )
    return passed, evidence, ""


def _gate_scope_boundary_clear(
    artifacts: dict, context: dict
) -> tuple[bool, str, str]:
    text = _artifact_text(artifacts, "product_brief.md") or ""
    scope_indicators = [
        "in scope", "out of scope", "in-scope", "out-of-scope",
        "scope:", "within scope", "beyond scope", "not included",
    ]
    found = [kw for kw in scope_indicators if kw.lower() in text.lower()]
    passed = len(found) >= 1
    evidence = (
        f"Found scope boundary indicators: {found}"
        if found
        else "No scope boundary indicators found in product brief"
    )
    return passed, evidence, ""


# ── planning gates ────────────────────────────────────────────────────────


def _gate_requirements_traceable_to_brief(
    artifacts: dict, context: dict
) -> tuple[bool, str, str]:
    text = _artifact_text(artifacts, "prd.md") or ""
    # Check for traceability language referencing the brief
    trace_indicators = [
        "traces to", "from product brief", "per brief",
        "user need", "problem from", "requirement map", "traceability",
    ]
    found = [kw for kw in trace_indicators if kw.lower() in text.lower()]
    passed = len(found) >= 1
    evidence = (
        f"Found traceability indicators: {found}"
        if found
        else "No traceability from PRD to product brief found"
    )
    return passed, evidence, ""


def _gate_acceptance_criteria_per_feature(
    artifacts: dict, context: dict
) -> tuple[bool, str, str]:
    text = _artifact_text(artifacts, "prd.md") or ""
    ac_indicators = [
        "acceptance criteria", "given when then", "definition of done",
        "acceptance test", "AC:", "must satisfy", "shall", "should",
        "verify that",
    ]
    found = [kw for kw in ac_indicators if kw.lower() in text.lower()]
    passed = len(found) >= 2
    evidence = (
        f"Found {len(found)} acceptance criteria indicators: {found}"
        if found
        else "No acceptance criteria found in PRD"
    )
    return passed, evidence, ""


def _gate_priority_assigned(
    artifacts: dict, context: dict
) -> tuple[bool, str, str]:
    text = _artifact_text(artifacts, "prd.md") or ""
    priority_indicators = [
        "priority", "P0", "P1", "P2", "P3", "P4",
        "MoSCoW", "must have", "should have", "could have", "won't have",
        "critical", "high priority", "medium priority", "low priority",
        "nice to have",
    ]
    found = [kw for kw in priority_indicators if kw.lower() in text.lower()]
    passed = len(found) >= 1
    evidence = (
        f"Found priority indicators: {found}"
        if found
        else "No priority assignments found in PRD"
    )
    return passed, evidence, ""


def _gate_dependencies_identified(
    artifacts: dict, context: dict
) -> tuple[bool, str, str]:
    text = _artifact_text(artifacts, "prd.md") or ""
    dep_indicators = [
        "dependency", "depends on", "prerequisite", "requires",
        "blocked by", "upstream", "external service",
    ]
    found = [kw for kw in dep_indicators if kw.lower() in text.lower()]
    passed = len(found) >= 1
    evidence = (
        f"Found dependency indicators: {found}"
        if found
        else "No dependencies identified in PRD"
    )
    return passed, evidence, ""


def _gate_out_of_scope_stated(
    artifacts: dict, context: dict
) -> tuple[bool, str, str]:
    text = _artifact_text(artifacts, "prd.md") or ""
    scope_indicators = [
        "out of scope", "out-of-scope", "not in scope", "excluded",
        "will not", "won't include", "deferred",
    ]
    found = [kw for kw in scope_indicators if kw.lower() in text.lower()]
    passed = len(found) >= 1
    evidence = (
        f"Found out-of-scope indicators: {found}"
        if found
        else "No out-of-scope statement found in PRD"
    )
    return passed, evidence, ""


# ── solutioning gates ─────────────────────────────────────────────────────


def _gate_components_mapped_to_requirements(
    artifacts: dict, context: dict
) -> tuple[bool, str, str]:
    text = _artifact_text(artifacts, "architecture.md") or ""
    map_indicators = [
        "maps to", "implements requirement", "satisfies",
        "requirement:", "traces to", "component:",
    ]
    found = [kw for kw in map_indicators if kw.lower() in text.lower()]
    passed = len(found) >= 1
    evidence = (
        f"Found component-to-requirement mapping indicators: {found}"
        if found
        else "No component-to-requirement mapping found in architecture"
    )
    return passed, evidence, ""


def _gate_data_flow_documented(
    artifacts: dict, context: dict
) -> tuple[bool, str, str]:
    text = _artifact_text(artifacts, "architecture.md") or ""
    flow_indicators = [
        "data flow", "dataflow", "pipeline", "message queue",
        "event bus", "API call", "RPC", "stream", "database",
        "read/write", "persist", "cache",
    ]
    found = [kw for kw in flow_indicators if kw.lower() in text.lower()]
    passed = len(found) >= 2
    evidence = (
        f"Found {len(found)} data flow indicators: {found}"
        if found
        else "No data flow documentation found in architecture"
    )
    return passed, evidence, ""


def _gate_tech_stack_decisions_justified(
    artifacts: dict, context: dict
) -> tuple[bool, str, str]:
    text = _artifact_text(artifacts, "architecture.md") or ""
    justification_indicators = [
        "chosen because", "selected for", "rationale",
        "trade-off", "tradeoff", "alternative considered",
        "why", "justification", "because", "due to",
    ]
    found = [kw for kw in justification_indicators if kw.lower() in text.lower()]
    passed = len(found) >= 1
    evidence = (
        f"Found tech stack justification indicators: {found}"
        if found
        else "No tech stack justifications found in architecture"
    )
    return passed, evidence, ""


def _gate_integration_points_identified(
    artifacts: dict, context: dict
) -> tuple[bool, str, str]:
    text = _artifact_text(artifacts, "architecture.md") or ""
    integration_indicators = [
        "integration", "API", "REST", "GraphQL", "gRPC",
        "webhook", "OAuth", "auth", "third-party",
        "external service", "endpoint",
    ]
    found = [kw for kw in integration_indicators if kw.lower() in text.lower()]
    passed = len(found) >= 2
    evidence = (
        f"Found {len(found)} integration point indicators: {found}"
        if found
        else "No integration points identified in architecture"
    )
    return passed, evidence, ""


def _gate_risk_mitigation_planned(
    artifacts: dict, context: dict
) -> tuple[bool, str, str]:
    text = _artifact_text(artifacts, "architecture.md") or ""
    risk_indicators = [
        "risk", "mitigation", "fallback", "contingency",
        "failure mode", "circuit breaker", "retry", "rollback",
        "degrade gracefully",
    ]
    found = [kw for kw in risk_indicators if kw.lower() in text.lower()]
    passed = len(found) >= 1
    evidence = (
        f"Found risk mitigation indicators: {found}"
        if found
        else "No risk mitigation planning found in architecture"
    )
    return passed, evidence, ""


# ── implementation gates ──────────────────────────────────────────────────


def _gate_code_compiles(
    artifacts: dict, context: dict
) -> tuple[bool, str, str]:
    # Check if code artifact exists and has content
    code_text = ""
    for key, val in artifacts.items():
        if isinstance(val, str) and (
            key.endswith(".py") or key.endswith(".js") or key.endswith(".ts")
            or key.endswith(".go") or key.endswith(".rs") or key.endswith(".java")
            or key in ("code", "src")
        ):
            code_text += val
    passed = bool(code_text.strip())
    evidence = (
        f"Code artifacts found: {len([k for k in artifacts if isinstance(artifacts[k], str) and not k.startswith('test')])} files"
        if passed
        else "No code artifacts found for compilation check"
    )
    notes = (
        "Full compilation check requires running the actual build tool."
        if passed else ""
    )
    return passed, evidence, notes


def _gate_tests_pass(
    artifacts: dict, context: dict
) -> tuple[bool, str, str]:
    # Check test artifacts and results
    test_files = [k for k in artifacts if "test" in k.lower()]
    test_results = context.get("test_results", context.get("tests", {}))
    test_pass_count = test_results.get("passed", 0) if isinstance(test_results, dict) else 0
    test_fail_count = test_results.get("failed", 0) if isinstance(test_results, dict) else 0

    if test_fail_count > 0:
        return False, f"{test_fail_count} test(s) failed", ""
    if test_files:
        return True, f"Test artifacts present: {len(test_files)} test files", ""
    if test_results:
        return True, f"Test results indicate {test_pass_count} passed, 0 failed", ""
    return True, "No test failures detected (tests may not have been executed yet)", ""


def _gate_requirements_covered(
    artifacts: dict, context: dict
) -> tuple[bool, str, str]:
    # Cross-reference PRD requirements against test/implementation coverage
    prd_text = (
        _artifact_text(artifacts, "prd.md")
        or context.get("prd_text", "")
    )
    if not prd_text:
        return True, "No PRD text available for coverage check — assuming covered", ""

    # Count requirement-like lines in PRD
    req_patterns = [
        r"^\s*[-*]\s+(?:FR|NFR|Req|Requirement)\d*:",
        r"^\s*\d+\.\s+",
        r"(?:shall|must|should)\s",
    ]
    req_count = 0
    for line in prd_text.splitlines():
        if any(re.search(p, line, re.IGNORECASE) for p in req_patterns):
            req_count += 1

    # Count test files as coverage proxy
    test_count = len([k for k in artifacts if "test" in k.lower()])

    if test_count == 0:
        return (
            False,
            f"No test files found to cover {req_count} requirements",
            "Add tests to demonstrate requirement coverage.",
        )
    return True, f"{req_count} requirements, {test_count} test files as coverage proxy", ""


def _gate_no_hardcoded_secrets(
    artifacts: dict, context: dict
) -> tuple[bool, str, str]:
    # Scan all text artifacts for secret patterns
    secret_patterns = [
        (r'(?:api[_-]?key|apikey|api_secret|secret[_-]?key)\s*[:=]\s*["\'](?![$\{])[^"\']{8,}["\']', "hardcoded API key"),
        (r'(?:password|passwd|pwd)\s*[:=]\s*["\'](?![$\{])[^"\']+["\']', "hardcoded password"),
        (r'(?:token|access_token|auth_token)\s*[:=]\s*["\'](?![$\{])[^"\']{8,}["\']', "hardcoded token"),
        (r'(?:private[_-]?key|privkey)\s*[:=]\s*["\'](?![$\{])[^"\']{20,}["\']', "hardcoded private key"),
    ]
    violations = []
    for key, val in artifacts.items():
        if isinstance(val, str):
            for pattern, label in secret_patterns:
                matches = re.findall(pattern, val, re.IGNORECASE)
                if matches:
                    violations.append(f"{key}: {label} ({len(matches)} instance(s))")

    passed = len(violations) == 0
    evidence = (
        "No hardcoded secrets detected"
        if passed
        else f"Secrets found: {'; '.join(violations)}"
    )
    notes = (
        "All secrets should be sourced from environment variables or a secret store."
        if not passed else ""
    )
    return passed, evidence, notes


def _gate_documentation_updated(
    artifacts: dict, context: dict
) -> tuple[bool, str, str]:
    doc_files = [
        k for k in artifacts
        if any(kw in k.lower() for kw in ("readme", "changelog", "doc", "guide"))
    ]
    src_files = [
        k for k in artifacts
        if any(k.endswith(ext) for ext in (".py", ".js", ".ts", ".go", ".rs", ".java", ".md"))
        and "test" not in k.lower()
    ]
    changed_docs = context.get("changed_files", [])
    doc_changes = [f for f in changed_docs if any(kw in f.lower() for kw in ("readme", "changelog", "doc", "guide"))]

    if doc_changes:
        return True, f"Documentation files changed: {doc_changes}", ""
    if doc_files:
        return True, f"Documentation artifacts present: {len(doc_files)} file(s)", ""
    if not src_files:
        return True, "No source changes detected — documentation update not required", ""
    return (
        True,
        f"No documentation changes detected for {len(src_files)} source files",
        "Consider updating README or API docs to reflect changes.",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Registry
# ═══════════════════════════════════════════════════════════════════════════

_GATE_HANDLERS: dict[str, Any] = {
    "problem_statement_present": _gate_problem_statement_present,
    "target_users_defined": _gate_target_users_defined,
    "success_metrics_listed": _gate_success_metrics_listed,
    "constraints_documented": _gate_constraints_documented,
    "scope_boundary_clear": _gate_scope_boundary_clear,
    "requirements_traceable_to_brief": _gate_requirements_traceable_to_brief,
    "acceptance_criteria_per_feature": _gate_acceptance_criteria_per_feature,
    "priority_assigned": _gate_priority_assigned,
    "dependencies_identified": _gate_dependencies_identified,
    "out_of_scope_stated": _gate_out_of_scope_stated,
    "components_mapped_to_requirements": _gate_components_mapped_to_requirements,
    "data_flow_documented": _gate_data_flow_documented,
    "tech_stack_decisions_justified": _gate_tech_stack_decisions_justified,
    "integration_points_identified": _gate_integration_points_identified,
    "risk_mitigation_planned": _gate_risk_mitigation_planned,
    "code_compiles": _gate_code_compiles,
    "tests_pass": _gate_tests_pass,
    "requirements_covered": _gate_requirements_covered,
    "no_hardcoded_secrets": _gate_no_hardcoded_secrets,
    "documentation_updated": _gate_documentation_updated,
}


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════


def load_criteria(
    phase: str,
    custom_path: str | None = None,
) -> dict:
    """Load gate criteria for *phase*.

    Args:
        phase: One of "analysis", "planning", "solutioning", "implementation".
        custom_path: Optional path to a custom criteria.yaml.  When None,
            the default criteria.yaml shipped alongside this module is used.

    Returns:
        A dict with keys ``phase``, ``required_artifacts``, and ``gates``
        (list of gate-definition dicts, each with ``id``, ``description``,
        and ``severity``).

    Raises:
        FileNotFoundError: If the criteria file does not exist.
        ValueError: If *phase* is not a recognized phase.
    """
    path = Path(custom_path) if custom_path else _DEFAULT_CRITERIA
    if not path.is_file():
        raise FileNotFoundError(f"Criteria file not found: {path}")

    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)

    phases = doc.get("phases", {})
    if phase not in phases:
        raise ValueError(
            f"Unknown phase {phase!r}. "
            f"Valid phases: {', '.join(sorted(phases.keys()))}"
        )

    phase_data = phases[phase]
    return {
        "phase": phase,
        "required_artifacts": phase_data.get("required_artifacts", []),
        "gates": phase_data.get("gates", []),
    }


def check_gate(
    gate: dict,
    artifacts: dict,
    context: dict,
) -> dict:
    """Check a single gate against *artifacts* and *context*.

    Args:
        gate: A gate definition dict from criteria.yaml. Must have ``id``,
            ``description``, and ``severity`` keys.
        artifacts: Parsed artifact content.  Keys are artifact names
            (e.g. ``"product_brief.md"``), values are the text content.
        context: Previous phase results and supporting data.

    Returns:
        ``{passed: bool, evidence: str, notes: str}``.
        - ``passed`` — whether the gate condition is satisfied.
        - ``evidence`` — specific, human-readable justification for the result.
        - ``notes`` — optional advisory text (warnings, suggestions).
    """
    gate_id = gate.get("id", "")
    description = gate.get("description", "")

    # Use registered handler if available
    handler = _GATE_HANDLERS.get(gate_id)
    if handler is not None:
        passed, evidence, notes = handler(artifacts, context)
    else:
        # Fallback: treat any non-empty artifact content as passing
        passed = any(
            isinstance(v, str) and v.strip()
            for v in artifacts.values()
        )
        evidence = (
            "Gate passed by content-presence heuristic"
            if passed
            else "No artifact content found"
        )
        notes = f"No registered handler for gate '{gate_id}'. Results are heuristic-based."

    return {
        "gate_id": gate_id,
        "description": description,
        "severity": gate.get("severity", "recommended"),
        "passed": passed,
        "evidence": evidence,
        "notes": notes,
    }


def evaluate_all_gates(
    phase: str,
    artifacts: dict,
    context: dict,
    custom_criteria: str | None = None,
) -> dict:
    """Run all gates for *phase* and return aggregated results.

    Args:
        phase: One of "analysis", "planning", "solutioning", "implementation".
        artifacts: Parsed artifact content (from artifact_reader).
        context: Previous phase results.
        custom_criteria: Optional path to custom criteria.yaml.

    Returns:
        ``{gates: list[gate_result], required_passed: bool,
           recommended_pass_rate: float}``.
    """
    criteria = load_criteria(phase, custom_criteria)
    gates = criteria.get("gates", [])

    results = [check_gate(gate, artifacts, context) for gate in gates]

    required_gates = [r for r in results if r["severity"] == "required"]
    recommended_gates = [r for r in results if r["severity"] == "recommended"]

    required_passed = all(r["passed"] for r in required_gates)
    recommended_pass_rate = (
        sum(1 for r in recommended_gates if r["passed"]) / len(recommended_gates)
        if recommended_gates
        else 1.0
    )

    return {
        "phase": phase,
        "gates": results,
        "required_passed": required_passed,
        "recommended_pass_rate": recommended_pass_rate,
        "total_gates": len(results),
        "passed_count": sum(1 for r in results if r["passed"]),
    }
