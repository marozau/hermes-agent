"""Scorer for research_g4_v1 — FROZEN metric for Epic 15 G4.

Reads a research markdown doc, returns composite score in [0.0, 1.0]
per the FROZEN YAML at research_g4_v1.yaml.

Sanity check (all 11 gold docs score >= 0.85):
    python3 research_g4_v1.py --sanity

Score a single doc:
    python3 research_g4_v1.py <path/to/doc.md>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import NamedTuple


class ScoreBreakdown(NamedTuple):
    composite: float
    yaml_frontmatter: float
    numbered_sections: float
    falsification_section: float
    numeric_confidence: float
    sources_block: float
    methodology_section: float
    headline_section: float
    min_word_count: float
    section_count: int
    word_count: int
    hard_gates_passed: bool


_FRONTMATTER_FIELDS = ["title", "type", "status", "scope", "agent", "date", "confidence", "inputs"]
_WEIGHTS = {
    "yaml_frontmatter": 0.30,
    "numbered_sections": 0.15,
    "falsification_section": 0.10,
    "numeric_confidence": 0.10,
    "sources_block": 0.10,
    "methodology_section": 0.05,
    "headline_section": 0.10,
    "min_word_count": 0.10,
}


def _score_frontmatter(text: str) -> float:
    if not text.startswith("---\n"):
        return 0.0
    fm_end = text.find("\n---\n", 4)
    if fm_end < 0:
        return 0.0
    fm = text[4:fm_end]
    present = sum(1 for f in _FRONTMATTER_FIELDS if f"{f}:" in fm)
    if present == len(_FRONTMATTER_FIELDS):
        return 1.0
    if present >= 4:
        return 0.5
    return 0.0


def _score_sections(text: str) -> tuple[float, int]:
    sections = re.findall(r"^## \d+\.\s+", text, re.MULTILINE)
    n = len(sections)
    if n >= 10:
        return 1.0, n
    if n >= 8:
        return 0.7, n
    if n >= 5:
        return 0.3, n
    return 0.0, n


def _score_falsification(text: str) -> float:
    return 1.0 if ("Falsification welcome" in text or re.search(r"falsifi", text, re.IGNORECASE)) else 0.0


def _score_confidence(text: str) -> float:
    return 1.0 if re.search(r"[Cc]onfidence[:\s\*]+(0\.\d+|\d{2,3}%)", text) else 0.0


def _score_sources(text: str) -> float:
    return 1.0 if re.search(r"##\s+\d+\.\s+(Sources|Inputs and sources)", text) else 0.0


def _score_methodology(text: str) -> float:
    return 1.0 if ("Methodology" in text or "methodology:" in text) else 0.0


def _score_headline(text: str) -> float:
    return 1.0 if re.search(r"^## 1\.\s+Headline", text, re.MULTILINE) else 0.0


def _score_wordcount(text: str) -> tuple[float, int]:
    n = len(text.split())
    if n >= 800:
        return 1.0, n
    if n >= 500:
        return 0.5, n
    return 0.0, n


def score_doc(text: str) -> ScoreBreakdown:
    fm_score = _score_frontmatter(text)
    sec_score, sec_count = _score_sections(text)
    fals_score = _score_falsification(text)
    conf_score = _score_confidence(text)
    src_score = _score_sources(text)
    meth_score = _score_methodology(text)
    head_score = _score_headline(text)
    wc_score, wc = _score_wordcount(text)

    # Hard gates
    fm_present_at_all = text.startswith("---\n")
    wc_gate = wc >= 500
    gates_ok = fm_present_at_all and wc_gate

    composite = (
        _WEIGHTS["yaml_frontmatter"] * fm_score
        + _WEIGHTS["numbered_sections"] * sec_score
        + _WEIGHTS["falsification_section"] * fals_score
        + _WEIGHTS["numeric_confidence"] * conf_score
        + _WEIGHTS["sources_block"] * src_score
        + _WEIGHTS["methodology_section"] * meth_score
        + _WEIGHTS["headline_section"] * head_score
        + _WEIGHTS["min_word_count"] * wc_score
    )

    if not gates_ok:
        # Hard gate failure caps at 0.4 (doc has SOMETHING but fundamentals missing)
        composite = min(composite, 0.4)

    return ScoreBreakdown(
        composite=round(composite, 3),
        yaml_frontmatter=fm_score,
        numbered_sections=sec_score,
        falsification_section=fals_score,
        numeric_confidence=conf_score,
        sources_block=src_score,
        methodology_section=meth_score,
        headline_section=head_score,
        min_word_count=wc_score,
        section_count=sec_count,
        word_count=wc,
        hard_gates_passed=gates_ok,
    )


def metric(example, prediction, trace=None) -> float:
    """DSPy-compatible metric — for use in GEPA.compile()."""
    output_text = getattr(prediction, "output", "") or getattr(prediction, "command_output", "") or ""
    return score_doc(str(output_text)).composite


def _sanity_check():
    """Score all 11 gold + 5 silver reference docs."""
    refdir = Path("/Users/im/usr-local/hermes-bmad/planning-artifacts/research")
    gold = [
        "technical-command-handler-return-shape-research-2026-06-01.md",
        "technical-command-spec-open-questions-and-dspy-2026-06-01.md",
        "technical-critics-skill-design-2026-06-06.md",
        "technical-epic-12-open-questions-2026-06-01.md",
        "technical-fork-migration-prep-2026-06-04.md",
        "technical-gepa-and-skillopt-wiring-2026-06-06.md",
        "technical-hermes-cli-vs-gateway-cli-ref-architecture-2026-06-06.md",
        "technical-hermes-plugin-vs-skill-llm-engagement-2026-06-06.md",
        "technical-hermes-self-evolution-narrative-shift-2026-06-01.md",
        "technical-hermes-wide-tuning-scope-2026-06-04.md",
        "technical-skillopt-bmad-integration-2026-06-04.md",
    ]
    silver = [
        "domain-bias-mitigation-techniques-2026-05-18.md",
        "domain-bmad-large-edit-failure-modes-2026-05-21.md",
        "domain-thinking-standards-review-2026-05-18.md",
        "domain-triz-ariz-2026-05-17.md",
        "technical-bmad-command-bodies-audit-2026-06-01.md",
    ]
    print(f"=== GOLD docs (should score >= 0.85) ===")
    g_scores = []
    for n in gold:
        s = score_doc((refdir / n).read_text())
        g_scores.append(s.composite)
        print(f"  {s.composite:.3f}  {n}")
    print(f"  GOLD avg: {sum(g_scores)/len(g_scores):.3f}, min: {min(g_scores):.3f}")
    print(f"\n=== SILVER docs (should score lower — discrimination check) ===")
    s_scores = []
    for n in silver:
        s = score_doc((refdir / n).read_text())
        s_scores.append(s.composite)
        print(f"  {s.composite:.3f}  {n}")
    print(f"  SILVER avg: {sum(s_scores)/len(s_scores):.3f}")
    print(f"\n=== Discrimination ===")
    print(f"  GOLD - SILVER = {sum(g_scores)/len(g_scores) - sum(s_scores)/len(s_scores):.3f}")
    return min(g_scores) >= 0.85


if __name__ == "__main__":
    if len(sys.argv) == 1 or sys.argv[1] == "--sanity":
        ok = _sanity_check()
        sys.exit(0 if ok else 1)
    path = Path(sys.argv[1])
    s = score_doc(path.read_text())
    print(f"Composite: {s.composite}")
    print(f"Word count: {s.word_count}")
    print(f"Section count: {s.section_count}")
    print(f"Hard gates: {'PASS' if s.hard_gates_passed else 'FAIL'}")
    print(f"\nBreakdown:")
    for k in _WEIGHTS:
        v = getattr(s, k)
        print(f"  {k:<25} {v:.2f}  (weight {_WEIGHTS[k]})")
