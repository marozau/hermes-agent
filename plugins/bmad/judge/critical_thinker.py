"""
critical_thinker.py — Falsification-first reasoning engine for BMAD judge.

Architecture: Evaluates claims by attempting to falsify them FIRST,
then confirms what survives. Weighted multi-signal calibration produces
a calibrated confidence score.

Exports:
    falsify_then_confirm   Evaluate a claim with falsification-first methodology
    FalsifyResult          Structured result from a falsification check
    ConfirmResult          Structured result from a confirmation check
    ThinkerVerdict         Final verdict with confidence and reasoning chain
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════
# Data types
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class FalsifyResult:
    """Result from a single falsification check."""
    check_name: str
    flagged: bool          # True = potential problem found
    finding: str           # Human-readable description of what was found
    severity: float = 1.0  # 0.0–1.0, how damaging this finding is to the claim


@dataclass
class ConfirmResult:
    """Result from a single confirmation check."""
    check_name: str
    supported: bool        # True = evidence supports the claim
    strength: float = 0.5  # 0.0–1.0, how strongly this confirms
    detail: str = ""       # Supporting detail


@dataclass
class ThinkerVerdict:
    """Final verdict from falsify_then_confirm."""
    verdict: str           # "PASS", "FAIL", or "CONDITIONAL_PASS"
    confidence: float      # 0.0–1.0, calibrated
    reasoning_chain: list[str] = field(default_factory=list)
    falsification_results: list[dict] = field(default_factory=list)
    confirmation_results: list[dict] = field(default_factory=list)
    weak_points: list[str] = field(default_factory=list)
    evidence_summary: str = ""

    def __post_init__(self):
        """Enforce weak_points when confidence < 0.9."""
        # Round confidence to nearest 0.05 with deterministic jitter
        self.confidence = _round_confidence(self.confidence, self.verdict)
        if self.confidence < 0.9 and not self.weak_points:
            self.weak_points = ["Confidence below 0.9 — review recommended"]


# ═══════════════════════════════════════════════════════════════════════════
# Confidence calibration
# ═══════════════════════════════════════════════════════════════════════════

# Weights for multi-signal calibration:
#   evidence(0.35), falsification(0.30), corroboration(0.15),
#   context(0.10), base_rate(0.30)
# Flaw penalty: 0.20 per flaw, max 0.65
# Evidence capped at 5 items
# Diversity adjustment: ±0.05

_EVIDENCE_WEIGHT = 0.35
_FALSIFICATION_WEIGHT = 0.30
_CORROBORATION_WEIGHT = 0.15
_CONTEXT_WEIGHT = 0.10
_BASE_RATE_WEIGHT = 0.30
_FLAW_PENALTY = 0.20
_MAX_FLAW_PENALTY = 0.65
_MAX_EVIDENCE_ITEMS = 5
_DIVERSITY_BONUS = 0.05


def _deterministic_jitter(seed: str, magnitude: float = 0.025) -> float:
    """Generate deterministic jitter from a hash of the seed string."""
    h = hashlib.sha256(seed.encode()).digest()
    # Convert first 4 bytes to a float in [-magnitude, +magnitude]
    val = int.from_bytes(h[:4], "big") / (2**32 - 1)
    return (val * 2 - 1) * magnitude


def _round_confidence(raw: float, verdict: str) -> float:
    """Round confidence to nearest 0.05 with deterministic jitter."""
    jitter = _deterministic_jitter(f"{verdict}:{raw:.6f}")
    adjusted = raw + jitter
    rounded = round(adjusted * 20) / 20  # round to nearest 0.05
    return max(0.0, min(1.0, rounded))


def _calibrate_confidence(
    evidence_score: float,
    falsification_score: float,
    corroboration_score: float,
    context_score: float,
    base_rate: float,
    flaw_count: int,
    diversity_sources: int,
) -> float:
    """Multi-signal weighted calibration.

    Args:
        evidence_score: 0–1, quality/quantity of direct evidence
        falsification_score: 0–1, how well claim survived falsification (1 = no problems)
        corroboration_score: 0–1, supporting evidence from independent sources
        context_score: 0–1, contextual fit
        base_rate: 0–1, prior probability / base rate for this type of claim
        flaw_count: Number of falsification flags found
        diversity_sources: Number of distinct evidence sources/categories

    Returns:
        Calibrated confidence 0.0–1.0 (not yet rounded).
    """
    weighted = (
        _EVIDENCE_WEIGHT * evidence_score
        + _FALSIFICATION_WEIGHT * falsification_score
        + _CORROBORATION_WEIGHT * corroboration_score
        + _CONTEXT_WEIGHT * context_score
        + _BASE_RATE_WEIGHT * base_rate
    )

    # Flaw penalty
    penalty = min(flaw_count * _FLAW_PENALTY, _MAX_FLAW_PENALTY)
    weighted -= penalty

    # Diversity bonus (capped at ±0.05)
    diversity_bonus = min(diversity_sources - 1, 3) * (_DIVERSITY_BONUS / 3)
    weighted += diversity_bonus

    return max(0.0, min(1.0, weighted))


# ═══════════════════════════════════════════════════════════════════════════
# Falsification checks (5)
# ═══════════════════════════════════════════════════════════════════════════

def _check_logical_gaps(claim: str, evidence: list[str], context: dict | None = None) -> FalsifyResult:
    """Check for logical gaps in the claim.

    Looks for:
    - Missing premises or unstated assumptions
    - Non-sequiturs (conclusion doesn't follow from premises)
    - Circular reasoning patterns
    """
    context = context or {}
    gaps = []

    # Check for assertion-only claims without supporting reasoning
    if len(claim.split()) < 20 and not any(
        kw in claim.lower()
        for kw in ("because", "therefore", "since", "due to", "as a result")
    ):
        gaps.append("Claim is brief assertion without explicit reasoning chain")

    # Check for circular indicators
    circular_patterns = [
        r"\b(because\s+\w+\s+is\b.*\bbecause)",
        r"\b(it is.*because it is)\b",
    ]
    for pattern in circular_patterns:
        if re.search(pattern, claim, re.IGNORECASE):
            gaps.append("Potential circular reasoning detected")
            break

    # Check if evidence items connect to the claim
    if evidence:
        connected = False
        claim_keywords = set(re.findall(r"\b[a-z]{4,}\b", claim.lower()))
        for item in evidence:
            item_keywords = set(re.findall(r"\b[a-z]{4,}\b", item.lower()))
            if claim_keywords & item_keywords:
                connected = True
                break
        if not connected:
            gaps.append("Evidence keywords have low overlap with claim keywords")

    flagged = len(gaps) > 0
    severity = min(1.0, len(gaps) * 0.3)
    finding = "; ".join(gaps) if gaps else "No logical gaps detected"

    return FalsifyResult(
        check_name="logical_gaps",
        flagged=flagged,
        finding=finding,
        severity=severity,
    )


def _check_contradictions(claim: str, evidence: list[str], context: dict | None = None) -> FalsifyResult:
    """Check for internal contradictions and evidence contradictions.

    Looks for:
    - Self-contradictory statements within the claim
    - Evidence that contradicts the claim
    - Inconsistencies with context data
    """
    context = context or {}
    contradictions = []

    # Check for self-contradiction patterns
    contradiction_indicators = [
        r"\b(?:but|however|although|on the other hand|conversely)\b",
        r"\b(?:nevertheless|nonetheless|yet|despite)\b",
    ]
    contra_count = sum(
        1 for p in contradiction_indicators
        if re.search(p, claim, re.IGNORECASE)
    )
    if contra_count >= 2:
        contradictions.append(
            f"Multiple contrastive markers ({contra_count}) — possible internal tension"
        )

    # Check evidence against claim for negation patterns
    claim_negated = set()
    for sent in re.split(r"[.!?]+", claim):
        if re.search(r"\b(?:not|no|never|none|cannot|won't|don't)\b", sent, re.IGNORECASE):
            words = set(re.findall(r"\b[a-z]{4,}\b", sent.lower()))
            claim_negated.update(words)

    if claim_negated and evidence:
        for item in evidence:
            item_words = set(re.findall(r"\b[a-z]{4,}\b", item.lower()))
            overlap = claim_negated & item_words
            if overlap:
                contradictions.append(
                    f"Evidence contains negated claim terms: {overlap}"
                )
                break

    # Check context data for inconsistencies
    prev_verdict = context.get("previous_verdict", "")
    if prev_verdict == "FAIL" and "pass" in claim.lower():
        contradictions.append("Claim suggests pass despite previous FAIL verdict in context")

    flagged = len(contradictions) > 0
    severity = min(1.0, len(contradictions) * 0.35)
    finding = "; ".join(contradictions) if contradictions else "No contradictions detected"

    return FalsifyResult(
        check_name="contradictions",
        flagged=flagged,
        finding=finding,
        severity=severity,
    )


def _check_missing_evidence(claim: str, evidence: list[str], context: dict | None = None) -> FalsifyResult:
    """Check whether the evidence adequately supports the claim.

    Looks for:
    - Claims that require evidence but have none
    - Evidence that is too generic or tangential
    - Quantitative claims without numeric support
    """
    context = context or {}
    missing = []

    if not evidence:
        missing.append("No evidence provided to support the claim")
    else:
        # Check if evidence is substantive (not just restating the claim)
        evidence_text = " ".join(evidence).lower()
        claim_lower = claim.lower()

        # Check for quantitative claims without numeric evidence
        quant_indicators = [
            r"\b\d+%", r"\b\d+\s*(?:ms|seconds?|minutes?|hours?|days?)",
            r"\b(?:increase|decrease|reduce|improve)\w*\s+by\s+\d+",
            r"\b(?:faster|slower|larger|smaller|better|worse)\s+than\b",
        ]
        has_quant_claim = any(re.search(p, claim_lower) for p in quant_indicators)
        has_quant_evidence = any(re.search(p, evidence_text) for p in quant_indicators)
        if has_quant_claim and not has_quant_evidence:
            missing.append("Quantitative claim lacks numeric evidence")

        # Evidence length heuristic for substantive support
        total_evidence_len = len(evidence_text)
        if total_evidence_len < 50:
            missing.append(
                f"Evidence is very brief ({total_evidence_len} chars total)"
            )

    flagged = len(missing) > 0
    severity = min(1.0, len(missing) * 0.4)
    finding = "; ".join(missing) if missing else "Evidence appears adequate"

    return FalsifyResult(
        check_name="missing_evidence",
        flagged=flagged,
        finding=finding,
        severity=severity,
    )


def _check_alternative_explanations(claim: str, evidence: list[str], context: dict | None = None) -> FalsifyResult:
    """Consider alternative explanations that could account for the same evidence.

    Looks for:
    - Whether the evidence could be explained without the claim being true
    - Common causal fallacies (correlation ≠ causation)
    - Selection bias or survivorship bias indicators
    """
    context = context or {}
    alternatives = []

    # Check for causal language without mechanism
    causal_markers = [
        r"\b(?:causes?|leads? to|results? in|drives?|produces?)\b",
        r"\b(?:because of|due to|as a result of|owing to)\b",
    ]
    has_causal = any(re.search(p, claim, re.IGNORECASE) for p in causal_markers)
    has_mechanism = re.search(
        r"\b(?:mechanism|pathway|through|via|by means of|via the)\b",
        claim, re.IGNORECASE,
    )

    if has_causal and not has_mechanism:
        alternatives.append(
            "Causal claim without mechanism — correlation alternative not ruled out"
        )

    # Check for binary framing (false dichotomy)
    binary_markers = [
        r"\b(?:either|vs\.?|versus|or else|one or the other)\b",
    ]
    if any(re.search(p, claim, re.IGNORECASE) for p in binary_markers):
        alternatives.append("Binary framing — other alternatives may exist")

    flagged = len(alternatives) > 0
    severity = min(1.0, len(alternatives) * 0.3)
    finding = "; ".join(alternatives) if alternatives else "No alternative explanations identified"

    return FalsifyResult(
        check_name="alternative_explanations",
        flagged=flagged,
        finding=finding,
        severity=severity,
    )


def _search_counter_examples(claim: str, evidence: list[str], context: dict | None = None) -> FalsifyResult:
    """Search for counter-examples that would falsify the claim.

    Looks for:
    - Universal quantifiers that are easily disproven
    - Domain-specific counter-examples from context data
    - Edge cases mentioned in evidence that undermine the claim
    """
    context = context or {}
    counter_examples = []

    # Check for universal quantifiers
    universal_patterns = [
        (r"\b(?:always|never|every|all|none|no one|everyone)\b",
         "Universal quantifier — single counter-example would falsify"),
        (r"\b(?:must|necessarily|inevitably|certainly|absolutely)\b",
         "Strong necessity claim — counter-examples likely exist"),
    ]
    for pattern, msg in universal_patterns:
        if re.search(pattern, claim, re.IGNORECASE):
            counter_examples.append(msg)
            break

    # Check for edge cases in evidence that contradict the claim
    edge_markers = [
        r"\b(?:however|but|except|unless|edge case|corner case)\b",
        r"\b(?:fails? when|doesn't work for|breaks? with)\b",
    ]
    for item in evidence:
        for pattern in edge_markers:
            if re.search(pattern, item, re.IGNORECASE):
                counter_examples.append(
                    f"Evidence mentions edge case / exception: '{item[:100]}...'"
                )
                break
        if counter_examples:
            break

    flagged = len(counter_examples) > 0
    severity = min(1.0, len(counter_examples) * 0.4)
    finding = "; ".join(counter_examples) if counter_examples else "No counter-examples found"

    return FalsifyResult(
        check_name="counter_examples",
        flagged=flagged,
        finding=finding,
        severity=severity,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Confirmation checks (3)
# ═══════════════════════════════════════════════════════════════════════════

def _check_direct_support(claim: str, evidence: list[str]) -> ConfirmResult:
    """Check for direct evidence supporting the claim.

    Measures keyword overlap and structural similarity between claim and evidence.
    """
    if not evidence:
        return ConfirmResult(
            check_name="direct_support",
            supported=False,
            strength=0.0,
            detail="No evidence to check for direct support",
        )

    claim_words = set(re.findall(r"\b[a-z]{4,}\b", claim.lower()))
    evidence_words = set()
    for item in evidence:
        evidence_words.update(re.findall(r"\b[a-z]{4,}\b", item.lower()))

    if not claim_words:
        return ConfirmResult(
            check_name="direct_support",
            supported=False,
            strength=0.0,
            detail="Claim has no analysable keywords",
        )

    overlap = claim_words & evidence_words
    overlap_ratio = len(overlap) / len(claim_words)
    strength = min(1.0, overlap_ratio * 1.5)  # scale up slightly

    supported = overlap_ratio > 0.1
    detail = (
        f"Keyword overlap: {len(overlap)}/{len(claim_words)} "
        f"({overlap_ratio:.1%})"
    )

    return ConfirmResult(
        check_name="direct_support",
        supported=supported,
        strength=strength,
        detail=detail,
    )


def _check_corroboration(claim: str, evidence: list[str]) -> ConfirmResult:
    """Check for corroborating evidence from multiple independent sources.

    Multiple evidence items that support the claim independently increase confidence.
    """
    if len(evidence) < 2:
        return ConfirmResult(
            check_name="corroboration",
            supported=len(evidence) == 1,
            strength=0.3 if len(evidence) == 1 else 0.0,
            detail=(
                f"Only {len(evidence)} evidence source(s) — "
                "corroboration requires 2+ independent sources"
            ),
        )

    # Check if evidence items cover different aspects
    claim_keywords = set(re.findall(r"\b[a-z]{4,}\b", claim.lower()))

    item_keyword_sets = []
    for item in evidence:
        item_keyword_sets.append(
            set(re.findall(r"\b[a-z]{4,}\b", item.lower()))
        )

    # Measure how many evidence items independently connect to the claim
    connecting_items = sum(
        1 for iks in item_keyword_sets
        if iks & claim_keywords
    )

    strength = min(1.0, connecting_items / len(evidence))
    supported = connecting_items >= 2

    detail = (
        f"{connecting_items}/{len(evidence)} evidence items connect to claim "
        f"({strength:.1%})"
    )

    return ConfirmResult(
        check_name="corroboration",
        supported=supported,
        strength=strength,
        detail=detail,
    )


def _check_falsification_survival(
    claim: str,
    falsification_results: list[FalsifyResult],
) -> ConfirmResult:
    """Check how well the claim survived falsification attempts.

    A claim that survives aggressive falsification is more trustworthy.
    """
    total_checks = len(falsification_results)
    if total_checks == 0:
        return ConfirmResult(
            check_name="falsification_survival",
            supported=False,
            strength=0.0,
            detail="No falsification checks performed",
        )

    flagged = sum(1 for r in falsification_results if r.flagged)
    survived = total_checks - flagged

    strength = survived / total_checks
    supported = strength >= 0.6  # Must survive majority of checks

    detail = (
        f"Survived {survived}/{total_checks} falsification checks "
        f"({strength:.1%})"
    )

    return ConfirmResult(
        check_name="falsification_survival",
        supported=supported,
        strength=strength,
        detail=detail,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Main API
# ═══════════════════════════════════════════════════════════════════════════

def falsify_then_confirm(
    claim: str,
    evidence: list[str] | None = None,
    context: dict | None = None,
    base_rate: float = 0.5,
) -> ThinkerVerdict:
    """Evaluate a claim using falsification-first methodology.

    1. Run all 5 falsification checks against the claim
    2. Run 3 confirmation checks
    3. Calibrate confidence using multi-signal weighted model
    4. Determine verdict: PASS / FAIL / CONDITIONAL_PASS
    5. Build reasoning chain and identify weak points

    Args:
        claim: The assertion or proposition to evaluate.
        evidence: List of evidence strings supporting (or opposing) the claim.
        context: Optional dict with additional context (e.g., previous_verdict,
            phase, related_gates).
        base_rate: Prior probability estimate for this type of claim (0–1).

    Returns:
        ThinkerVerdict with verdict, confidence, reasoning chain, and
        detailed results from each check.
    """
    evidence = evidence or []
    context = context or {}

    reasoning_chain: list[str] = []

    # ── Phase 1: Falsification ──────────────────────────────────────────
    reasoning_chain.append(
        f"Evaluating claim: '{claim[:120]}{'...' if len(claim) > 120 else ''}'"
    )

    falsification_checks = [
        _check_logical_gaps,
        _check_contradictions,
        _check_missing_evidence,
        _check_alternative_explanations,
        _search_counter_examples,
    ]

    falsification_results: list[FalsifyResult] = []
    for check_fn in falsification_checks:
        result = check_fn(claim, evidence, context)
        falsification_results.append(result)
        if result.flagged:
            reasoning_chain.append(
                f"⚠️  {result.check_name}: {result.finding}"
            )
        else:
            reasoning_chain.append(
                f"✓ {result.check_name}: {result.finding}"
            )

    flaw_count = sum(1 for r in falsification_results if r.flagged)
    reasoning_chain.append(
        f"Falsification: {flaw_count}/{len(falsification_results)} checks flagged issues"
    )

    # ── Phase 2: Confirmation ──────────────────────────────────────────
    confirmation_results: list[ConfirmResult] = []

    ds = _check_direct_support(claim, evidence)
    confirmation_results.append(ds)
    reasoning_chain.append(f"Direct support: {'✓' if ds.supported else '✗'} {ds.detail}")

    corr = _check_corroboration(claim, evidence)
    confirmation_results.append(corr)
    reasoning_chain.append(f"Corroboration: {'✓' if corr.supported else '✗'} {corr.detail}")

    fs = _check_falsification_survival(claim, falsification_results)
    confirmation_results.append(fs)
    reasoning_chain.append(f"Falsification survival: {'✓' if fs.supported else '✗'} {fs.detail}")

    # ── Phase 3: Calibration ───────────────────────────────────────────
    evidence_score = min(1.0, len(evidence) / _MAX_EVIDENCE_ITEMS)
    falsification_score = 1.0 - (sum(r.severity for r in falsification_results if r.flagged) / max(1, len(falsification_results)))
    corroboration_score = corr.strength
    context_score = 0.5  # neutral default
    if context.get("previous_verdict") == "PASS":
        context_score = 0.7
    elif context.get("previous_verdict") == "FAIL":
        context_score = 0.3

    diversity_sources = len(set(
        r.check_name for r in falsification_results
    )) + len(set(
        r.check_name for r in confirmation_results
    ))

    confidence = _calibrate_confidence(
        evidence_score=evidence_score,
        falsification_score=falsification_score,
        corroboration_score=corroboration_score,
        context_score=context_score,
        base_rate=base_rate,
        flaw_count=flaw_count,
        diversity_sources=diversity_sources,
    )

    # ── Phase 4: Verdict ───────────────────────────────────────────────
    if flaw_count == 0 and confidence >= 0.7:
        verdict = "PASS"
    elif flaw_count <= 1 and confidence >= 0.5:
        verdict = "CONDITIONAL_PASS"
    else:
        verdict = "FAIL"

    reasoning_chain.append(
        f"Calibrated confidence: {confidence:.2f} | Verdict: {verdict}"
    )

    # ── Phase 5: Weak points ───────────────────────────────────────────
    weak_points: list[str] = []
    for r in falsification_results:
        if r.flagged:
            weak_points.append(f"[{r.check_name}] {r.finding}")
    if not evidence:
        weak_points.append("No evidence provided")
    if confidence < 0.6:
        weak_points.append(f"Low confidence ({confidence:.2f}) — additional evidence recommended")

    # Round confidence
    rounded_confidence = _round_confidence(confidence, verdict)

    # Build evidence summary
    evidence_summary = (
        f"{len(evidence)} evidence items; "
        f"{flaw_count} falsification flags; "
        f"{sum(1 for c in confirmation_results if c.supported)}/{len(confirmation_results)} confirmations passed"
    )

    result = ThinkerVerdict(
        verdict=verdict,
        confidence=rounded_confidence,
        reasoning_chain=reasoning_chain,
        falsification_results=[
            {
                "check_name": r.check_name,
                "flagged": r.flagged,
                "finding": r.finding,
                "severity": r.severity,
            }
            for r in falsification_results
        ],
        confirmation_results=[
            {
                "check_name": r.check_name,
                "supported": r.supported,
                "strength": r.strength,
                "detail": r.detail,
            }
            for r in confirmation_results
        ],
        weak_points=weak_points,
        evidence_summary=evidence_summary,
    )

    return result
