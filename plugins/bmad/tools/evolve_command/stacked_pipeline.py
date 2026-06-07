"""Stacked pipeline driver — orchestrates Phase 1 (SkillOpt) → Phase 2 (GEPA).

Story 15.11: Composes the two optimization phases into a single pipeline.

Flow:
    1. Parse command body into frontmatter + body sections.
    2. Run Phase 1 (SkillOpt) on Strategy/Patterns/Edge Cases regions.
    3. Feed Phase 1 output (best_skill.md) into Phase 2 (GEPA).
    4. Phase 2 mutates Procedure/Acceptance Criteria/Test plan regions.
    5. Reassemble the evolved body with the original frontmatter.
    6. Validate OI-2 disjoint-region constraint (no overlap between phases).

OI-2 Disjoint Regions:
    Phase 1 mutates: Strategy, Patterns, Edge Cases
    Phase 2 mutates: Procedure, Acceptance Criteria, Test plan
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

try:
    from .adapters.command_body_module import parse_command, reassemble_command
except ImportError:
    from adapters.command_body_module import parse_command, reassemble_command

logger = logging.getLogger(__name__)

# ── OI-2 Disjoint Region Definitions ──────────────────────────────────


class PhaseRegion(str, Enum):
    """Sections of a BMAD command body that phases may mutate."""

    STRATEGY = "Strategy"
    PATTERNS = "Patterns"
    EDGE_CASES = "Edge Cases"
    PROCEDURE = "Procedure"
    ACCEPTANCE_CRITERIA = "Acceptance Criteria"
    TEST_PLAN = "Test plan"


# Phase 1 (SkillOpt) owns these regions
PHASE1_REGIONS: frozenset[PhaseRegion] = frozenset({
    PhaseRegion.STRATEGY,
    PhaseRegion.PATTERNS,
    PhaseRegion.EDGE_CASES,
})

# Phase 2 (GEPA) owns these regions
PHASE2_REGIONS: frozenset[PhaseRegion] = frozenset({
    PhaseRegion.PROCEDURE,
    PhaseRegion.ACCEPTANCE_CRITERIA,
    PhaseRegion.TEST_PLAN,
})

# ── Section extraction ────────────────────────────────────────────────

# Matches markdown H2 or H3 headers: ## Strategy, ### Patterns, etc.
_SECTION_RE = re.compile(
    r"^(#{1,6})\s+(.+?)\s*$",
    re.MULTILINE,
)

# Map of header text → PhaseRegion (case-insensitive, normalized)
_REGION_ALIASES: dict[str, PhaseRegion] = {
    "strategy": PhaseRegion.STRATEGY,
    "patterns": PhaseRegion.PATTERNS,
    "edge cases": PhaseRegion.EDGE_CASES,
    "edge_cases": PhaseRegion.EDGE_CASES,
    "procedure": PhaseRegion.PROCEDURE,
    "acceptance criteria": PhaseRegion.ACCEPTANCE_CRITERIA,
    "acceptance_criteria": PhaseRegion.ACCEPTANCE_CRITERIA,
    "test plan": PhaseRegion.TEST_PLAN,
    "test_plan": PhaseRegion.TEST_PLAN,
}


@dataclass(frozen=True)
class BodySection:
    """A named section extracted from the command body.

    Attributes:
        region: The :class:`PhaseRegion` this section maps to (or None).
        header: The full markdown header line (e.g. ``## Strategy``).
        content: The section body text (everything until the next header).
        start: Character offset of the header in the original body.
        end: Character offset of the end of the section content.
    """

    region: Optional[PhaseRegion]
    header: str
    content: str
    start: int
    end: int


def extract_sections(body: str) -> list[BodySection]:
    """Extract named sections from a markdown command body.

    Scans for H2/H3 headers and maps them to :class:`PhaseRegion` values.
    Sections that don't match a known region get ``region=None``.

    Args:
        body: The markdown body text (without frontmatter).

    Returns:
        List of :class:`BodySection` objects in document order.
    """
    matches = list(_SECTION_RE.finditer(body))
    if not matches:
        # Return entire body as one unnamed section so OI-2 still tracks it (P1-5 fix)
        return [BodySection(header="<body>", content=body, region=None, start=0, end=len(body))]

    sections: list[BodySection] = []
    for i, m in enumerate(matches):
        header_text = m.group(2).strip()
        normalized = header_text.lower().replace("_", " ").replace("-", " ")
        region = _REGION_ALIASES.get(normalized)

        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        content = body[m.end():end].strip()

        sections.append(BodySection(
            region=region,
            header=m.group(0).strip(),
            content=content,
            start=start,
            end=end,
        ))

    return sections


def sections_for_region(
    sections: list[BodySection],
    region: PhaseRegion,
) -> list[BodySection]:
    """Return sections matching a specific :class:`PhaseRegion`."""
    return [s for s in sections if s.region == region]


# ── OI-2 Disjoint-Region Validator ────────────────────────────────────


@dataclass(frozen=True)
class OI2ValidationResult:
    """Result of the OI-2 disjoint-region validation.

    Attributes:
        passed: ``True`` if no overlap was detected.
        phase1_regions: Regions that Phase 1 claims to have mutated.
        phase2_regions: Regions that Phase 2 claims to have mutated.
        overlap: The set of regions that both phases tried to mutate.
        message: Human-readable summary.
    """

    passed: bool
    phase1_regions: frozenset[PhaseRegion]
    phase2_regions: frozenset[PhaseRegion]
    overlap: frozenset[PhaseRegion]
    message: str


def compute_empirical_mutations(
    original_body: str,
    evolved_body: str,
) -> set[PhaseRegion]:
    """Compute which PhaseRegions actually changed by diffing section content (OI-2 enforcement)."""
    original_sections = extract_sections(original_body)
    evolved_sections = extract_sections(evolved_body)
    original_map = {(s.region, s.header): s.content for s in original_sections}
    evolved_map = {(s.region, s.header): s.content for s in evolved_sections}
    mutated: set[PhaseRegion] = set()
    for key, evo_content in evolved_map.items():
        region, _header = key
        if region is None:
            continue
        orig_content = original_map.get(key, "")
        if evo_content.strip() != orig_content.strip():
            mutated.add(region)
    return mutated


def validate_oi2_disjoint(
    phase1_mutated: set[PhaseRegion],
    phase2_mutated: set[PhaseRegion],
) -> OI2ValidationResult:
    """Validate OI-2 disjoint-region constraint.

    Phase 1 (SkillOpt) must only mutate Strategy/Patterns/Edge Cases.
    Phase 2 (GEPA) must only mutate Procedure/Acceptance Criteria/Test plan.
    The two sets must not overlap.

    Args:
        phase1_mutated: Regions Phase 1 claims to have modified.
        phase2_mutated: Regions Phase 2 claims to have modified.

    Returns:
        An :class:`OI2ValidationResult` with pass/fail and details.
    """
    overlap = phase1_mutated & phase2_mutated

    # Also check that each phase stayed in its lane
    phase1_violations = phase1_mutated - PHASE1_REGIONS
    phase2_violations = phase2_mutated - PHASE2_REGIONS

    passed = len(overlap) == 0 and len(phase1_violations) == 0 and len(phase2_violations) == 0

    messages: list[str] = []
    if overlap:
        names = ", ".join(r.value for r in sorted(overlap, key=lambda r: r.value))
        messages.append(f"Overlap detected: {{{names}}}")
    if phase1_violations:
        names = ", ".join(r.value for r in sorted(phase1_violations, key=lambda r: r.value))
        messages.append(f"Phase 1 violated lane: {{{names}}}")
    if phase2_violations:
        names = ", ".join(r.value for r in sorted(phase2_violations, key=lambda r: r.value))
        messages.append(f"Phase 2 violated lane: {{{names}}}")

    if passed:
        if not phase1_mutated and not phase2_mutated:
            message = "OI-2 NOT APPLICABLE: no recognized phase regions in body"
        else:
            message = "OI-2 disjoint-region check passed: no overlap between phases"
    else:
        message = "OI-2 disjoint-region check FAILED: " + "; ".join(messages)

    return OI2ValidationResult(
        passed=passed,
        phase1_regions=frozenset(phase1_mutated),
        phase2_regions=frozenset(phase2_mutated),
        overlap=frozenset(overlap),
        message=message,
    )


# ── Phase result containers ───────────────────────────────────────────


@dataclass
class Phase1Result:
    """Result from Phase 1 (SkillOpt).

    Attributes:
        best_body: The optimized body text from SkillOpt.
        mutated_regions: Which regions SkillOpt claims to have mutated.
        elapsed: Wall-clock seconds for Phase 1.
        error: Error message if Phase 1 failed (non-None = failure).
    """

    best_body: str = ""
    mutated_regions: set[PhaseRegion] = field(default_factory=set)
    elapsed: float = 0.0
    error: Optional[str] = None
    degraded_from_error: Optional[str] = None  # P2: preserves original Phase 1 failure for audit


@dataclass
class Phase2Result:
    """Result from Phase 2 (GEPA).

    Attributes:
        evolved_body: The evolved body text from GEPA.
        mutated_regions: Which regions GEPA claims to have mutated.
        elapsed: Wall-clock seconds for Phase 2.
        cost_estimate: Estimated USD cost of the GEPA run.
        used_fallback: ``True`` if MIPROv2 fallback was used.
        error: Error message if Phase 2 failed (non-None = failure).
    """

    evolved_body: str = ""
    mutated_regions: set[PhaseRegion] = field(default_factory=set)
    elapsed: float = 0.0
    cost_estimate: float = 0.0
    used_fallback: bool = False
    error: Optional[str] = None


@dataclass
class StackedPipelineResult:
    """Final result of the stacked pipeline.

    Attributes:
        command_text: The fully reassembled command (frontmatter + evolved body).
        frontmatter: The preserved original frontmatter.
        phase1: Result from Phase 1 (SkillOpt).
        phase2: Result from Phase 2 (GEPA).
        oi2_validation: OI-2 disjoint-region validation result.
        total_elapsed: Total wall-clock seconds for the full pipeline.
        success: ``True`` if both phases succeeded and OI-2 passed.
    """

    command_text: str = ""
    frontmatter: str = ""
    phase1: Optional[Phase1Result] = None
    phase2: Optional[Phase2Result] = None
    oi2_validation: Optional[OI2ValidationResult] = None
    total_elapsed: float = 0.0
    success: bool = False


# ── Phase runner type aliases ─────────────────────────────────────────

# Phase 1 (SkillOpt) runner: takes body text, returns Phase1Result
# Phase 2 (GEPA) runner: takes body text, returns Phase2Result


# ── Default (no-op) runners for dry-run / testing ────────────────────


def _default_phase1_runner(
    command_body: str,
    *,
    config: dict[str, Any] | None = None,
) -> Phase1Result:
    """No-op Phase 1 runner: returns the body unchanged."""
    return Phase1Result(
        best_body=command_body,
        mutated_regions=set(),
        elapsed=0.0,
    )


def _default_phase2_runner(
    command_body: str,
    *,
    config: dict[str, Any] | None = None,
) -> Phase2Result:
    """No-op Phase 2 runner: returns the body unchanged."""
    return Phase2Result(
        evolved_body=command_body,
        mutated_regions=set(),
        elapsed=0.0,
    )


# ── Stacked Pipeline ─────────────────────────────────────────────────


def run_stacked_pipeline(
    command_text: str,
    *,
    phase1_runner: Optional[Callable[..., Phase1Result]] = None,
    phase2_runner: Optional[Callable[..., Phase2Result]] = None,
    phase1_config: dict[str, Any] | None = None,
    phase2_config: dict[str, Any] | None = None,
) -> StackedPipelineResult:
    """Run the stacked SkillOpt → GEPA pipeline on a command body.

    Orchestrates Phase 1 (SkillOpt) and Phase 2 (GEPA), validates
    OI-2 disjoint regions, and reassembles the evolved body with the
    original frontmatter.

    Args:
        command_text: Full command file content (frontmatter + body).
        phase1_runner: Callable to execute Phase 1 (SkillOpt).
            Defaults to a no-op runner.
        phase2_runner: Callable to execute Phase 2 (GEPA).
            Defaults to a no-op runner.
        phase1_config: Configuration dict passed to Phase 1 runner.
        phase2_config: Configuration dict passed to Phase 2 runner.

    Returns:
        A :class:`StackedPipelineResult` with the evolved command text,
        phase results, OI-2 validation, and timing metadata.
    """
    total_start = time.monotonic()

    if phase1_runner is None:
        phase1_runner = _default_phase1_runner
    if phase2_runner is None:
        phase2_runner = _default_phase2_runner

    # ── Step 1: Parse command into frontmatter + body ──────────────────
    parsed = parse_command(command_text)

    # ── Step 2: Run Phase 1 (SkillOpt) ────────────────────────────────
    logger.info("Stacked pipeline: starting Phase 1 (SkillOpt)")
    phase1_start = time.monotonic()
    phase1 = phase1_runner(parsed.body, config=phase1_config)
    phase1.elapsed = time.monotonic() - phase1_start
    logger.info(
        "Stacked pipeline: Phase 1 complete (%.2fs, regions=%s)",
        phase1.elapsed,
        {r.value for r in phase1.mutated_regions},
    )

    if phase1.error:
        logger.warning("Stacked pipeline: Phase 1 failed (%s); proceeding with Phase 2 on original body", phase1.error)
        phase1 = Phase1Result(
            best_body=parsed.body,
            mutated_regions=set(),
            elapsed=phase1.elapsed,
            error=None,
            degraded_from_error=phase1.error,  # P2: preserve original failure for audit
        )

    # ── Step 3: Run Phase 2 (GEPA) on Phase 1 output ──────────────────
    logger.info("Stacked pipeline: starting Phase 2 (GEPA)")
    phase2_start = time.monotonic()
    phase2 = phase2_runner(phase1.best_body, config=phase2_config)
    phase2.elapsed = time.monotonic() - phase2_start
    logger.info(
        "Stacked pipeline: Phase 2 complete (%.2fs, regions=%s, fallback=%s)",
        phase2.elapsed,
        {r.value for r in phase2.mutated_regions},
        phase2.used_fallback,
    )

    if phase2.error:
        logger.warning("Stacked pipeline: Phase 2 failed: %s", phase2.error)
        return StackedPipelineResult(
            frontmatter=parsed.frontmatter,
            phase1=phase1,
            phase2=phase2,
            total_elapsed=time.monotonic() - total_start,
            success=False,
        )

    # ── Step 4: Validate OI-2 disjoint regions (P0-3: empirical diff) ──
    p1_empirical = compute_empirical_mutations(parsed.body, phase1.best_body)
    p2_empirical = compute_empirical_mutations(phase1.best_body, phase2.evolved_body)
    oi2 = validate_oi2_disjoint(p1_empirical, p2_empirical)
    logger.info("Stacked pipeline: OI-2 validation: %s", oi2.message)

    if not oi2.passed:
        # P1-10: include evolved body even on OI-2 failure
        failed_text = reassemble_command(parsed.frontmatter, phase2.evolved_body)
        return StackedPipelineResult(
            frontmatter=parsed.frontmatter,
            phase1=phase1,
            phase2=phase2,
            command_text=failed_text,
            oi2_validation=oi2,
            total_elapsed=time.monotonic() - total_start,
            success=False,
        )

    # ── Step 5: Reassemble with original frontmatter ──────────────────
    evolved_text = reassemble_command(parsed.frontmatter, phase2.evolved_body)

    total_elapsed = time.monotonic() - total_start
    logger.info("Stacked pipeline: complete (%.2fs)", total_elapsed)

    return StackedPipelineResult(
        command_text=evolved_text,
        frontmatter=parsed.frontmatter,
        phase1=phase1,
        phase2=phase2,
        oi2_validation=oi2,
        total_elapsed=total_elapsed,
        success=True,
    )
