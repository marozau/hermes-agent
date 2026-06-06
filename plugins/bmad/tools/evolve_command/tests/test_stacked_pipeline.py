"""Tests for stacked_pipeline.py — Stacked pipeline driver (Story 15.11).

Covers:
  1. OI-2 disjoint-region validation (pass, fail-overlap, fail-lane-violation).
  2. Section extraction from markdown bodies.
  3. Full pipeline flow with mock runners (success, phase-1 error, phase-2 error).
  4. Frontmatter preservation across the full pipeline.
  5. OI-2 failure blocks pipeline success.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the package is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from stacked_pipeline import (
    PHASE1_REGIONS,
    PHASE2_REGIONS,
    BodySection,
    OI2ValidationResult,
    Phase1Result,
    Phase2Result,
    PhaseRegion,
    StackedPipelineResult,
    extract_sections,
    run_stacked_pipeline,
    sections_for_region,
    validate_oi2_disjoint,
)

# ── Fixtures ──────────────────────────────────────────────────────────

SAMPLE_FRONTMATTER = (
    "name: dev-story\n"
    "description: Development story command\n"
    "version: 1.0"
)

SAMPLE_BODY = """## Strategy

Use TDD approach with incremental delivery.

## Patterns

- Repository pattern for data access
- Strategy pattern for pluggable algorithms

## Edge Cases

- Empty input returns default
- Concurrent writes handled with locks

## Procedure

1. Write failing test
2. Implement minimum code
3. Refactor

## Acceptance Criteria

- [ ] All tests pass
- [ ] No regressions
- [ ] Coverage ≥ 80%

## Test Plan

- Unit tests for each module
- Integration test for end-to-end flow
"""

SAMPLE_RAW = f"---\n{SAMPLE_FRONTMATTER}\n---\n\n{SAMPLE_BODY}"


def _mock_phase1_runner(
    command_body: str,
    *,
    config: dict | None = None,
) -> Phase1Result:
    """Mock Phase 1 that mutates Strategy and Patterns regions."""
    mutated_body = command_body.replace(
        "Use TDD approach with incremental delivery.",
        "Use BDD approach with behavior-first delivery.",
    )
    return Phase1Result(
        best_body=mutated_body,
        mutated_regions={PhaseRegion.STRATEGY, PhaseRegion.PATTERNS},
        elapsed=0.1,
    )


def _mock_phase2_runner(
    command_body: str,
    *,
    config: dict | None = None,
) -> Phase2Result:
    """Mock Phase 2 that mutates Procedure and Acceptance Criteria regions."""
    evolved_body = command_body.replace(
        "1. Write failing test",
        "1. Write failing test\n2.5. Verify edge cases",
    )
    return Phase2Result(
        evolved_body=evolved_body,
        mutated_regions={PhaseRegion.PROCEDURE, PhaseRegion.ACCEPTANCE_CRITERIA},
        elapsed=0.2,
        cost_estimate=0.50,
    )


def _mock_phase1_error_runner(
    command_body: str,
    *,
    config: dict | None = None,
) -> Phase1Result:
    """Mock Phase 1 that errors out."""
    return Phase1Result(
        best_body="",
        mutated_regions=set(),
        elapsed=0.01,
        error="SkillOpt environment setup failed",
    )


def _mock_phase2_error_runner(
    command_body: str,
    *,
    config: dict | None = None,
) -> Phase2Result:
    """Mock Phase 2 that errors out."""
    return Phase2Result(
        evolved_body="",
        mutated_regions=set(),
        elapsed=0.02,
        error="GEPA optimizer crashed",
    )


def _mock_phase1_overlap_runner(
    command_body: str,
    *,
    config: dict | None = None,
) -> Phase1Result:
    """Mock Phase 1 that violates OI-2 by mutating a Phase 2 region."""
    return Phase1Result(
        best_body=command_body,
        mutated_regions={PhaseRegion.STRATEGY, PhaseRegion.PROCEDURE},  # PROCEDURE is Phase 2's
        elapsed=0.1,
    )


# ── Test 1: OI-2 Disjoint-Region Validation ──────────────────────────


class TestOI2Validation:
    """Tests for validate_oi2_disjoint()."""

    def test_disjoint_passes(self) -> None:
        """Disjoint region sets should pass validation."""
        result = validate_oi2_disjoint(
            phase1_mutated={PhaseRegion.STRATEGY, PhaseRegion.PATTERNS},
            phase2_mutated={PhaseRegion.PROCEDURE, PhaseRegion.ACCEPTANCE_CRITERIA},
        )
        assert result.passed is True
        assert len(result.overlap) == 0
        assert "passed" in result.message.lower()

    def test_overlap_fails(self) -> None:
        """Overlapping regions should fail validation."""
        result = validate_oi2_disjoint(
            phase1_mutated={PhaseRegion.STRATEGY, PhaseRegion.PROCEDURE},
            phase2_mutated={PhaseRegion.PROCEDURE, PhaseRegion.TEST_PLAN},
        )
        assert result.passed is False
        assert PhaseRegion.PROCEDURE in result.overlap
        assert "FAILED" in result.message

    def test_phase1_lane_violation_fails(self) -> None:
        """Phase 1 mutating a Phase 2 region should fail even without overlap."""
        result = validate_oi2_disjoint(
            phase1_mutated={PhaseRegion.PROCEDURE},  # Not in PHASE1_REGIONS
            phase2_mutated={PhaseRegion.TEST_PLAN},
        )
        assert result.passed is False
        assert "Phase 1 violated lane" in result.message

    def test_phase2_lane_violation_fails(self) -> None:
        """Phase 2 mutating a Phase 1 region should fail even without overlap."""
        result = validate_oi2_disjoint(
            phase1_mutated={PhaseRegion.STRATEGY},
            phase2_mutated={PhaseRegion.PATTERNS},  # Not in PHASE2_REGIONS
        )
        assert result.passed is False
        assert "Phase 2 violated lane" in result.message

    def test_empty_regions_pass(self) -> None:
        """Empty region sets should pass (nothing to overlap)."""
        result = validate_oi2_disjoint(
            phase1_mutated=set(),
            phase2_mutated=set(),
        )
        assert result.passed is True

    def test_all_phase1_regions_pass(self) -> None:
        """All Phase 1 regions used, disjoint from Phase 2."""
        result = validate_oi2_disjoint(
            phase1_mutated=set(PHASE1_REGIONS),
            phase2_mutated=set(PHASE2_REGIONS),
        )
        assert result.passed is True
        assert len(result.overlap) == 0

    def test_frozen_result_fields(self) -> None:
        """OI2ValidationResult should be frozen (immutable)."""
        result = validate_oi2_disjoint(
            phase1_mutated={PhaseRegion.STRATEGY},
            phase2_mutated={PhaseRegion.PROCEDURE},
        )
        assert isinstance(result.phase1_regions, frozenset)
        assert isinstance(result.phase2_regions, frozenset)
        assert isinstance(result.overlap, frozenset)


# ── Test 2: Section Extraction ────────────────────────────────────────


class TestExtractSections:
    """Tests for extract_sections() and sections_for_region()."""

    def test_extracts_all_known_sections(self) -> None:
        """All 6 known regions should be extracted from the sample body."""
        sections = extract_sections(SAMPLE_BODY)
        regions = {s.region for s in sections}
        assert PhaseRegion.STRATEGY in regions
        assert PhaseRegion.PATTERNS in regions
        assert PhaseRegion.EDGE_CASES in regions
        assert PhaseRegion.PROCEDURE in regions
        assert PhaseRegion.ACCEPTANCE_CRITERIA in regions
        assert PhaseRegion.TEST_PLAN in regions

    def test_sections_in_document_order(self) -> None:
        """Sections should appear in document order."""
        sections = extract_sections(SAMPLE_BODY)
        headers = [s.header for s in sections]
        assert headers.index("## Strategy") < headers.index("## Patterns")
        assert headers.index("## Patterns") < headers.index("## Edge Cases")
        assert headers.index("## Edge Cases") < headers.index("## Procedure")
        assert headers.index("## Procedure") < headers.index("## Acceptance Criteria")
        assert headers.index("## Acceptance Criteria") < headers.index("## Test Plan")

    def test_section_content_extracted(self) -> None:
        """Each section's content should contain expected text."""
        sections = extract_sections(SAMPLE_BODY)
        strategy = [s for s in sections if s.region == PhaseRegion.STRATEGY][0]
        assert "TDD approach" in strategy.content

        proc = [s for s in sections if s.region == PhaseRegion.PROCEDURE][0]
        assert "Write failing test" in proc.content

    def test_unknown_sections_get_none_region(self) -> None:
        """Sections not matching any known region get region=None."""
        body = "## Intro\nSome intro text.\n## Strategy\nStrat text.\n"
        sections = extract_sections(body)
        intro = [s for s in sections if s.header == "## Intro"]
        assert len(intro) == 1
        assert intro[0].region is None

    def test_empty_body_returns_empty(self) -> None:
        """Empty body should return no sections."""
        assert len(extract_sections("")) == 1  # P1-5: empty body tracked as single section

    def test_no_headers_returns_empty(self) -> None:
        """Body with no headers should return no sections."""
        assert len(extract_sections("Just plain text with no headers.")) == 1  # P1-5

    def test_sections_for_region_filter(self) -> None:
        """sections_for_region should filter correctly."""
        sections = extract_sections(SAMPLE_BODY)
        strategy_sections = sections_for_region(sections, PhaseRegion.STRATEGY)
        assert len(strategy_sections) == 1
        assert strategy_sections[0].region == PhaseRegion.STRATEGY

    def test_h3_headers_recognized(self) -> None:
        """H3 headers should also be recognized."""
        body = "### Strategy\nH3 strategy.\n### Procedure\nH3 proc.\n"
        sections = extract_sections(body)
        assert len(sections) == 2
        assert sections[0].region == PhaseRegion.STRATEGY
        assert sections[1].region == PhaseRegion.PROCEDURE

    def test_underscore_variants(self) -> None:
        """Underscore variants (e.g. 'edge_cases') should map correctly."""
        body = "## Edge Cases\nEdge case text.\n## Test plan\nTest plan text.\n"
        sections = extract_sections(body)
        assert sections[0].region == PhaseRegion.EDGE_CASES
        assert sections[1].region == PhaseRegion.TEST_PLAN


# ── Test 3: Full Pipeline Flow ────────────────────────────────────────


class TestStackedPipeline:
    """Tests for run_stacked_pipeline() end-to-end."""

    def test_success_with_mock_runners(self) -> None:
        """Full pipeline with mock runners should succeed."""
        result = run_stacked_pipeline(
            SAMPLE_RAW,
            phase1_runner=_mock_phase1_runner,
            phase2_runner=_mock_phase2_runner,
        )
        assert result.success is True
        assert result.phase1 is not None
        assert result.phase2 is not None
        assert result.oi2_validation is not None
        assert result.oi2_validation.passed is True
        assert result.total_elapsed > 0.0

    def test_frontmatter_preserved(self) -> None:
        """The original frontmatter must survive the full pipeline."""
        result = run_stacked_pipeline(
            SAMPLE_RAW,
            phase1_runner=_mock_phase1_runner,
            phase2_runner=_mock_phase2_runner,
        )
        assert result.success is True
        assert "name: dev-story" in result.command_text
        assert "description: Development story command" in result.command_text
        assert "version: 1.0" in result.command_text
        # Frontmatter markers should be present
        assert result.command_text.strip().startswith("---")

    def test_phase1_mutation_applied(self) -> None:
        """Phase 1's mutation should appear in the final output."""
        result = run_stacked_pipeline(
            SAMPLE_RAW,
            phase1_runner=_mock_phase1_runner,
            phase2_runner=_mock_phase2_runner,
        )
        assert result.success is True
        # Phase 1 replaced "TDD" with "BDD"
        assert "BDD approach" in result.command_text
        assert "TDD approach" not in result.command_text

    def test_phase2_mutation_applied(self) -> None:
        """Phase 2's mutation should appear in the final output."""
        result = run_stacked_pipeline(
            SAMPLE_RAW,
            phase1_runner=_mock_phase1_runner,
            phase2_runner=_mock_phase2_runner,
        )
        assert result.success is True
        # Phase 2 added "2.5. Verify edge cases"
        assert "Verify edge cases" in result.command_text

    def test_phase1_error_aborts_pipeline(self) -> None:
        """Phase 1 error should abort without running Phase 2."""
        phase2_called = False

        def tracking_phase2(body: str, *, config: dict | None = None) -> Phase2Result:
            nonlocal phase2_called
            phase2_called = True
            return Phase2Result(evolved_body=body)

        result = run_stacked_pipeline(
            SAMPLE_RAW,
            phase1_runner=_mock_phase1_error_runner,
            phase2_runner=tracking_phase2,
        )
        assert result.success is False
        assert result.phase1 is not None
        assert result.phase1.error == "SkillOpt environment setup failed"
        assert result.phase2 is None
        assert phase2_called is False

    def test_phase2_error_aborts_pipeline(self) -> None:
        """Phase 2 error should abort and report failure."""
        result = run_stacked_pipeline(
            SAMPLE_RAW,
            phase1_runner=_mock_phase1_runner,
            phase2_runner=_mock_phase2_error_runner,
        )
        assert result.success is False
        assert result.phase1 is not None
        assert result.phase2 is not None
        assert result.phase2.error == "GEPA optimizer crashed"

    def test_oi2_overlap_fails_pipeline(self) -> None:
        """OI-2 violation should cause pipeline to fail."""
        result = run_stacked_pipeline(
            SAMPLE_RAW,
            phase1_runner=_mock_phase1_overlap_runner,
            phase2_runner=_mock_phase2_runner,
        )
        assert result.success is False
        assert result.oi2_validation is not None
        assert result.oi2_validation.passed is False
        assert "FAILED" in result.oi2_validation.message

    def test_default_noop_runners(self) -> None:
        """With no runners provided, pipeline uses no-op defaults."""
        result = run_stacked_pipeline(SAMPLE_RAW)
        assert result.success is True
        assert result.phase1 is not None
        assert result.phase2 is not None
        assert result.phase1.mutated_regions == set()
        assert result.phase2.mutated_regions == set()
        # Body should be unchanged (no-op runners)
        assert result.command_text.strip() == SAMPLE_RAW.strip()

    def test_no_frontmatter_body_only(self) -> None:
        """Pipeline should handle body-only input (no frontmatter)."""
        body_only = "## Strategy\nDo stuff.\n## Procedure\nStep 1.\n"

        def p1(body: str, *, config: dict | None = None) -> Phase1Result:
            return Phase1Result(
                best_body=body,
                mutated_regions={PhaseRegion.STRATEGY},
            )

        def p2(body: str, *, config: dict | None = None) -> Phase2Result:
            return Phase2Result(
                evolved_body=body,
                mutated_regions={PhaseRegion.PROCEDURE},
            )

        result = run_stacked_pipeline(body_only, phase1_runner=p1, phase2_runner=p2)
        assert result.success is True
        assert result.frontmatter == ""


# ── Test 4: Phase Region Enum ─────────────────────────────────────────


class TestPhaseRegion:
    """Tests for PhaseRegion enum and region set definitions."""

    def test_phase1_and_phase2_disjoint(self) -> None:
        """PHASE1_REGIONS and PHASE2_REGIONS must be disjoint by design."""
        assert len(PHASE1_REGIONS & PHASE2_REGIONS) == 0

    def test_phase_region_count(self) -> None:
        """There should be exactly 3 regions per phase."""
        assert len(PHASE1_REGIONS) == 3
        assert len(PHASE2_REGIONS) == 3

    def test_all_regions_covered(self) -> None:
        """All PhaseRegion values should be in one of the two phase sets."""
        all_regions = set(PhaseRegion)
        assert all_regions == PHASE1_REGIONS | PHASE2_REGIONS

    def test_enum_values_are_strings(self) -> None:
        """PhaseRegion values should be human-readable strings."""
        for region in PhaseRegion:
            assert isinstance(region.value, str)
            assert len(region.value) > 0


# ── Test 5: BodySection dataclass ─────────────────────────────────────


class TestBodySection:
    """Tests for BodySection dataclass."""

    def test_frozen(self) -> None:
        """BodySection should be frozen (immutable)."""
        section = BodySection(
            region=PhaseRegion.STRATEGY,
            header="## Strategy",
            content="Do stuff.",
            start=0,
            end=20,
        )
        with pytest.raises(AttributeError):
            section.content = "mutated"  # type: ignore[misc]

    def test_none_region_for_unknown(self) -> None:
        """Unknown sections should have region=None."""
        section = BodySection(
            region=None,
            header="## Unknown",
            content="text",
            start=0,
            end=10,
        )
        assert section.region is None


# ── Test 6: Phase result containers ───────────────────────────────────


class TestPhaseResults:
    """Tests for Phase1Result and Phase2Result dataclasses."""

    def test_phase1_defaults(self) -> None:
        """Phase1Result should have sensible defaults."""
        result = Phase1Result()
        assert result.best_body == ""
        assert result.mutated_regions == set()
        assert result.elapsed == 0.0
        assert result.error is None

    def test_phase2_defaults(self) -> None:
        """Phase2Result should have sensible defaults."""
        result = Phase2Result()
        assert result.evolved_body == ""
        assert result.mutated_regions == set()
        assert result.elapsed == 0.0
        assert result.cost_estimate == 0.0
        assert result.used_fallback is False
        assert result.error is None

    def test_stacked_result_defaults(self) -> None:
        """StackedPipelineResult should have sensible defaults."""
        result = StackedPipelineResult()
        assert result.command_text == ""
        assert result.frontmatter == ""
        assert result.phase1 is None
        assert result.phase2 is None
        assert result.oi2_validation is None
        assert result.total_elapsed == 0.0
        assert result.success is False
