"""OCR triage adapter — source normalization + consensus classification (Story 8.5).

Extends the triage step to handle source: ocr findings and produce
multi-source consensus signals per OI-12.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ── Consensus classification table ───────────────────────────────────────────

CONSENSUS_TABLE: dict[frozenset[str], str] = {
    frozenset({"ocr"}): "patch",
    frozenset({"blind"}): "decision_needed",
    frozenset({"edge"}): "decision_needed",
    frozenset({"auditor"}): "decision_needed",
    frozenset({"blind", "ocr"}): "patch",
    frozenset({"edge", "ocr"}): "patch",
    frozenset({"auditor", "ocr"}): "patch",
    frozenset({"blind", "edge"}): "patch",
    frozenset({"blind", "auditor"}): "patch",
    frozenset({"edge", "auditor"}): "patch",
    frozenset({"blind", "edge", "ocr"}): "patch_strong",
    frozenset({"blind", "auditor", "ocr"}): "patch_strong",
    frozenset({"edge", "auditor", "ocr"}): "patch_strong",
    frozenset({"blind", "edge", "auditor"}): "patch_strong",
    frozenset({"blind", "edge", "auditor", "ocr"}): "must_fix",
}

# Minimum sources for each classification level
MIN_SOURCES_FOR_PATCH = 1
MIN_SOURCES_FOR_STRONG = 3
MIN_SOURCES_FOR_MUST_FIX = 4


@dataclass
class TriageFinding:
    """A finding after triage normalization and consensus classification."""

    file: str
    line: int
    message: str
    severity: str  # BLOCKER, MAJOR, MINOR, NIT
    classification: str  # must_fix, patch_strong, patch, decision_needed, skip
    sources: set[str] = field(default_factory=set)
    source_details: dict[str, Any] = field(default_factory=dict)
    consensus_count: int = 0


def normalize_ocr_finding(finding: Any) -> dict[str, Any]:
    """Convert an OCRFinding to the standard triage input format."""
    return {
        "file": finding.file,
        "line": finding.line,
        "message": finding.message,
        "severity": finding.severity,
        "source": "ocr",
        "rule_id": finding.rule_id,
        "category": finding.category,
    }


def classify_consensus(sources: set[str]) -> str:
    """Classify a finding based on which independent sources agree.

    OI-12: Consensus reflects independent agreement, not authority.
    """
    key = frozenset(sources)
    if key in CONSENSUS_TABLE:
        return CONSENSUS_TABLE[key]

    # Fallback: count sources
    count = len(sources)
    if count >= MIN_SOURCES_FOR_MUST_FIX:
        return "must_fix"
    if count >= MIN_SOURCES_FOR_STRONG:
        return "patch_strong"
    if count >= MIN_SOURCES_FOR_PATCH:
        return "patch"
    return "decision_needed"


def merge_findings(
    blind_findings: list[dict[str, Any]] | None = None,
    edge_findings: list[dict[str, Any]] | None = None,
    auditor_findings: list[dict[str, Any]] | None = None,
    ocr_findings: list[dict[str, Any]] | None = None,
) -> list[TriageFinding]:
    """Merge findings from all 4 sources and classify by consensus.

    Findings are matched by (file, line, normalized_message_prefix).
    Each unique location gets a TriageFinding with the set of sources
    that flagged it.
    """
    all_findings: list[tuple[str, dict[str, Any]]] = []

    for source, findings in [
        ("blind", blind_findings or []),
        ("edge", edge_findings or []),
        ("auditor", auditor_findings or []),
        ("ocr", ocr_findings or []),
    ]:
        for f in findings:
            all_findings.append((source, f))

    # Group by (file, line) — simple matching; can be refined
    by_location: dict[tuple[str, int], list[tuple[str, dict[str, Any]]]] = {}
    for source, f in all_findings:
        key = (f.get("file", ""), int(f.get("line", 0)))
        by_location.setdefault(key, []).append((source, f))

    results: list[TriageFinding] = []
    for (file, line), entries in by_location.items():
        sources: set[str] = set()
        source_details: dict[str, Any] = {}
        messages: list[str] = []
        severities: list[str] = []

        for source, f in entries:
            sources.add(source)
            source_details[source] = f
            messages.append(f.get("message", ""))
            severities.append(f.get("severity", "MINOR"))

        classification = classify_consensus(sources)

        # Severity: take the highest from contributing sources
        sev_order = {"BLOCKER": 4, "MAJOR": 3, "MINOR": 2, "NIT": 1}
        max_sev = max(severities, key=lambda s: sev_order.get(s, 0))

        # LLM reviewer can escalate OCR finding to BLOCKER (OD-8)
        if "ocr" in sources and len(sources) > 1 and max_sev == "MAJOR":
            # If an LLM reviewer explicitly agrees, allow escalation
            if any(s in sources for s in ("blind", "edge", "auditor")):
                max_sev = "MAJOR"  # Keep MAJOR; BLOCKER reserved for merge-blockers

        results.append(TriageFinding(
            file=file,
            line=line,
            message=messages[0] if messages else "",
            severity=max_sev,
            classification=classification,
            sources=sources,
            source_details=source_details,
            consensus_count=len(sources),
        ))

    # Sort: must_fix first, then by severity, then by file
    class_order = {"must_fix": 0, "patch_strong": 1, "patch": 2, "decision_needed": 3, "skip": 4}
    sev_order = {"BLOCKER": 0, "MAJOR": 1, "MINOR": 2, "NIT": 3}
    results.sort(key=lambda f: (class_order.get(f.classification, 9), sev_order.get(f.severity, 9), f.file, f.line))

    return results
