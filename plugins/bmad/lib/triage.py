"""Triage module for BMAD code review — Epic 8 Story 8.5.

Normalizes findings from all 4 sources (Blind Hunter, Edge Case Hunter,
Acceptance Auditor, OCR) and produces consensus classification signals.

Consensus rules (all 15 source combinations):
  - ocr_only → patch
  - blind_only → decision_needed
  - blind+ocr → patch
  - blind+edge → patch
  - blind+edge+ocr → patch(strong)
  - blind+edge+auditor → must_fix
  - blind+edge+auditor+ocr → MUST-FIX
  - auditor_only → decision_needed
  - auditor+ocr → patch
  - edge_only → decision_needed
  - edge+ocr → decision_needed
  - blind+auditor → patch
  - blind+auditor+ocr → must_fix
  - edge+auditor → patch
  - edge+auditor+ocr → patch(strong)
  - none → no findings

Hard invariants:
  - OI-12: OCR is one of 4 INDEPENDENT sources, NOT authority.
  - OI-11: OCR findings never injected into LLM prompts.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from plugins.bmad.lib.ocr_runner import Finding, Severity


class Source(Enum):
    """The 4 independent review sources."""
    BLIND = "blind"
    EDGE = "edge"
    AUDITOR = "auditor"
    OCR = "ocr"


class ConsensusLevel(Enum):
    """Consensus classification levels — from softest to strongest signal."""
    NO_FINDINGS = "no_findings"
    DECISION_NEEDED = "decision_needed"
    PATCH = "patch"
    PATCH_STRONG = "patch_strong"
    MUST_FIX = "must_fix"


@dataclass
class NormalizedFinding:
    """A finding normalized from any of the 4 sources."""
    source: Source
    rule_id: str
    file: str
    line: int
    severity: Severity
    message: str
    raw: str = ""  # original text for debugging


@dataclass
class TriageResult:
    """The output of triage consensus classification."""
    consensus: ConsensusLevel
    sources_present: set[Source]
    finding_count: int
    findings_by_source: dict[Source, list[NormalizedFinding]]
    explanation: str


# ── Severity normalization ──────────────────────────────────────────────────

_LLM_SEVERITY_PATTERNS = {
    Severity.MAJOR: [
        r"\bMUST[- ]?FIX\b",
        r"\bBLOCKER?\b",
        r"\bCRITICAL\b",
        r"\bSEVERITY:\s*HIGH\b",
        r"\bHIGH\b",
    ],
    Severity.MINOR: [
        r"\bSHOULD[- ]?FIX\b",
        r"\bMAJOR\b",
        r"\bMEDIUM\b",
        r"\bMED\b",
        r"\bSEVERITY:\s*MEDIUM\b",
    ],
    Severity.NIT: [
        r"\bCONSIDER\b",
        r"\bNIT\b",
        r"\bLOW\b",
        r"\bMINOR\b",
        r"\bSEVERITY:\s*LOW\b",
        r"\bSUGGESTION\b",
    ],
}


def _infer_severity_from_text(text: str) -> Severity:
    """Infer severity from LLM-generated text using keyword patterns."""
    text_upper = text.upper()
    for sev, patterns in _LLM_SEVERITY_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text_upper):
                return sev
    # Default: if text has "issue" or "bug", MINOR; else NIT
    if re.search(r"\b(bug|issue|error|problem|vulnerability)\b", text_upper):
        return Severity.MINOR
    return Severity.NIT


# ── Finding normalization from LLM text output ──────────────────────────────


def normalize_llm_findings(
    raw_text: str,
    source: Source,
) -> list[NormalizedFinding]:
    """Parse LLM reviewer output into NormalizedFinding objects.

    LLM output is free-form Markdown/JSON. We extract structured findings
    heuristically:
    - JSON arrays of objects (Edge Case Hunter format)
    - Markdown list items (Blind Hunter / Acceptance Auditor format)
    """
    findings: list[NormalizedFinding] = []

    # Try JSON first (Edge Case Hunter)
    stripped = raw_text.strip()
    if stripped.startswith("["):
        try:
            data = json.loads(stripped)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        findings.append(NormalizedFinding(
                            source=source,
                            rule_id=item.get("rule_id", item.get("location", "unknown")),
                            file=item.get("file", item.get("location", "unknown").split(":")[0] if ":" in str(item.get("location", "")) else "unknown"),
                            line=int(item.get("line", 0)),
                            severity=_infer_severity_from_text(json.dumps(item)),
                            message=item.get("message", item.get("potential_consequence", str(item))),
                            raw=json.dumps(item),
                        ))
                return findings
        except (json.JSONDecodeError, ValueError):
            pass

    # Markdown list parsing: lines starting with "- " or "* " or "1. "
    for line in raw_text.splitlines():
        line_stripped = line.strip()
        if not line_stripped:
            continue
        # Match list items: "- **Title** — description" or "- Finding text"
        m = re.match(r"^[-*]\s+(?:(?:\*\*)?(.+?)(?:\*\*)?\s*[-:—]\s*)?(.+)$", line_stripped)
        if m:
            title = (m.group(1) or "").strip()
            desc = (m.group(2) or "").strip()
            full_text = f"{title}: {desc}" if title else desc
            findings.append(NormalizedFinding(
                source=source,
                rule_id=title[:50] if title else "unknown",
                file="unknown",
                line=0,
                severity=_infer_severity_from_text(full_text),
                message=full_text,
                raw=line_stripped,
            ))
            continue
        # Numbered list: "1. Finding text"
        m2 = re.match(r"^\d+[.)]\s+(.+)$", line_stripped)
        if m2:
            text = m2.group(1).strip()
            findings.append(NormalizedFinding(
                source=source,
                rule_id="unknown",
                file="unknown",
                line=0,
                severity=_infer_severity_from_text(text),
                message=text,
                raw=line_stripped,
            ))

    return findings


def normalize_ocr_findings(findings: list[Finding]) -> list[NormalizedFinding]:
    """Convert OCR Finding objects to NormalizedFinding (already normalized)."""
    return [
        NormalizedFinding(
            source=Source.OCR,
            rule_id=f.rule_id,
            file=f.file,
            line=f.line,
            severity=f.severity,
            message=f.message,
            raw=f"{f.rule_id}: {f.message}",
        )
        for f in findings
    ]


# ── Consensus classification ────────────────────────────────────────────────

# All 15 non-empty source combinations → consensus level.
# Keys are frozensets of Source for order-independent lookup.
_CONSENSUS_TABLE: dict[frozenset, ConsensusLevel] = {
    # Single source
    frozenset({Source.OCR}): ConsensusLevel.PATCH,
    frozenset({Source.BLIND}): ConsensusLevel.DECISION_NEEDED,
    frozenset({Source.EDGE}): ConsensusLevel.DECISION_NEEDED,
    frozenset({Source.AUDITOR}): ConsensusLevel.DECISION_NEEDED,

    # Two sources
    frozenset({Source.BLIND, Source.OCR}): ConsensusLevel.PATCH,
    frozenset({Source.BLIND, Source.EDGE}): ConsensusLevel.PATCH,
    frozenset({Source.BLIND, Source.AUDITOR}): ConsensusLevel.PATCH,
    frozenset({Source.EDGE, Source.OCR}): ConsensusLevel.DECISION_NEEDED,
    frozenset({Source.EDGE, Source.AUDITOR}): ConsensusLevel.PATCH,
    frozenset({Source.AUDITOR, Source.OCR}): ConsensusLevel.PATCH,

    # Three sources
    frozenset({Source.BLIND, Source.EDGE, Source.OCR}): ConsensusLevel.PATCH_STRONG,
    frozenset({Source.BLIND, Source.EDGE, Source.AUDITOR}): ConsensusLevel.MUST_FIX,
    frozenset({Source.BLIND, Source.AUDITOR, Source.OCR}): ConsensusLevel.MUST_FIX,
    frozenset({Source.EDGE, Source.AUDITOR, Source.OCR}): ConsensusLevel.PATCH_STRONG,

    # Four sources — strongest signal
    frozenset({Source.BLIND, Source.EDGE, Source.AUDITOR, Source.OCR}): ConsensusLevel.MUST_FIX,
}


def classify_consensus(sources_present: set[Source]) -> ConsensusLevel:
    """Classify the consensus level based on which sources have findings.

    Args:
        sources_present: Set of sources that produced at least one finding.

    Returns:
        ConsensusLevel for this combination.

    OI-12: OCR is one of 4 independent sources, NOT the authority.
    """
    if not sources_present:
        return ConsensusLevel.NO_FINDINGS

    key = frozenset(sources_present)
    return _CONSENSUS_TABLE.get(key, ConsensusLevel.DECISION_NEEDED)


def _consensus_explanation(consensus: ConsensusLevel, sources: set[Source]) -> str:
    """Human-readable explanation for the consensus classification."""
    source_names = sorted(s.value for s in sources)
    joined = " + ".join(source_names)
    explanations = {
        ConsensusLevel.NO_FINDINGS: "No findings from any source.",
        ConsensusLevel.DECISION_NEEDED: (
            f"Findings from `{joined}` — human decision needed to determine severity."
        ),
        ConsensusLevel.PATCH: (
            f"Findings from `{joined}` — likely needs a patch. Review and fix."
        ),
        ConsensusLevel.PATCH_STRONG: (
            f"Strong signal from `{joined}` — high confidence this needs a patch."
        ),
        ConsensusLevel.MUST_FIX: (
            f"Consensus from `{joined}` — MUST FIX before merge."
        ),
    }
    return explanations.get(consensus, f"Consensus: {consensus.value}")


# ── Main triage function ────────────────────────────────────────────────────


def triage(
    blind_text: str = "",
    edge_text: str = "",
    auditor_text: str = "",
    ocr_findings: list[Finding] | None = None,
) -> TriageResult:
    """Run triage on findings from all 4 sources.

    Args:
        blind_text: Raw text output from Blind Hunter.
        edge_text: Raw text output from Edge Case Hunter.
        auditor_text: Raw text output from Acceptance Auditor.
        ocr_findings: Parsed OCR Finding objects (may be None if OCR disabled).

    Returns:
        TriageResult with consensus classification and normalized findings.

    OI-12: OCR is one of 4 independent sources, NOT authority.
    """
    findings_by_source: dict[Source, list[NormalizedFinding]] = {}

    # Normalize each source
    if blind_text.strip():
        findings_by_source[Source.BLIND] = normalize_llm_findings(blind_text, Source.BLIND)
    if edge_text.strip():
        findings_by_source[Source.EDGE] = normalize_llm_findings(edge_text, Source.EDGE)
    if auditor_text.strip():
        findings_by_source[Source.AUDITOR] = normalize_llm_findings(auditor_text, Source.AUDITOR)
    if ocr_findings:
        findings_by_source[Source.OCR] = normalize_ocr_findings(ocr_findings)

    # Sources that have at least one finding
    sources_present = {
        source for source, findings in findings_by_source.items()
        if findings
    }

    # Classify
    consensus = classify_consensus(sources_present)
    total = sum(len(f) for f in findings_by_source.values())

    return TriageResult(
        consensus=consensus,
        sources_present=sources_present,
        finding_count=total,
        findings_by_source=findings_by_source,
        explanation=_consensus_explanation(consensus, sources_present),
    )


# ── Result formatting ───────────────────────────────────────────────────────


_CONSENSUS_EMOJI = {
    ConsensusLevel.NO_FINDINGS: "✅",
    ConsensusLevel.DECISION_NEEDED: "🟡",
    ConsensusLevel.PATCH: "🟠",
    ConsensusLevel.PATCH_STRONG: "🔴",
    ConsensusLevel.MUST_FIX: "🚫",
}


def format_triage_markdown(result: TriageResult) -> str:
    """Format a TriageResult as human-readable Markdown."""
    lines: list[str] = []
    emoji = _CONSENSUS_EMOJI.get(result.consensus, "❓")
    lines.append(f"## {emoji} Consensus: {result.consensus.value.upper()}")
    lines.append("")
    lines.append(f"**{result.explanation}**")
    lines.append(f"Total findings: {result.finding_count}")
    lines.append("")

    for source in Source:
        findings = result.findings_by_source.get(source, [])
        if not findings:
            continue
        lines.append(f"### Source: {source.value} ({len(findings)} findings)")
        for f in findings:
            severity_tag = f"[{f.severity.value}]"
            lines.append(f"- {severity_tag} `{f.file}:{f.line}` — {f.message}")
        lines.append("")

    return "\n".join(lines)
