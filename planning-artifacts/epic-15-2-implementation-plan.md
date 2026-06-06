# Epic 15.2 Implementation Plan

## Overview
Restore upstream BMAD templates into Hermes skill bodies to close the structural-output gap (G4 measured 0.212→0.400 with augmented body; target 0.7-0.85 with templates inlined).

## Files to Create/Modify

### New Files (11)
1. `skills/bmad/templates/research.template.md` — research doc skeleton (copied upstream)
2. `plugins/bmad/tools/evolve_command/metrics/research_structural_v1.yaml` — FROZEN rubric
3. `plugins/bmad/tools/evolve_command/metrics/create_prd_structural_v1.yaml` — FROZEN rubric
4. `plugins/bmad/tools/evolve_command/metrics/create_architecture_structural_v1.yaml` — FROZEN rubric
5. `plugins/bmad/tools/evolve_command/metrics/product_brief_structural_v1.yaml` — FROZEN rubric
6. `plugins/bmad/tools/evolve_command/metrics/epics_stories_structural_v1.yaml` — FROZEN rubric
7. `plugins/bmad/tools/evolve_command/scripts/smoke_test_skill.py` — TUI-runnable smoke test harness
8. `plugins/bmad/tools/evolve_command/scripts/score_output.py` — scoring engine for structural metrics
9. `tests/plugins/bmad/test_structural_metrics.py` — unit tests for metric scoring
10. `tests/plugins/bmad/test_template_inline.py` — unit tests for template inlining
11. `planning-artifacts/epic-15-2-implementation-plan.md` — this plan

### Modified Files (9)
1. `skills/bmad/bmm/research/SKILL.md` — inline template reference + execution steps
2. `skills/bmad/bmm/create-prd/SKILL.md` — inline template reference + execution steps
3. `skills/bmad/bmm/create-architecture/SKILL.md` — inline template reference + execution steps
4. `skills/bmad/bmm/epics-stories/SKILL.md` — inline template reference + execution steps
5. `skills/bmad/bmm/product-brief/SKILL.md` — add template reference section
6. `plugins/bmad/tools/evolve_command/scripts/check_metric_frozen.py` — extend to ALL metrics
7. `skills/bmad/bmm/create-story/SKILL.md` — add template reference for story spec structure
8. `skills/bmad/bmm/sprint-planning/SKILL.md` — add template reference (sprint-status YAML)
9. `skills/bmad/bmm/document-project/SKILL.md` — add template reference

### Functions/Logic Changes
- `check_metric_frozen.py`: from hardcoded single metric → glob all `metrics/*.yaml`
- `score_output.py`: new scoring engine that applies regex patterns from metric YAML to output text
- `smoke_test_skill.py`: new harness that calls skill via TUI, captures output, scores against metric

## Dependencies
- `pyyaml` — already installed
- `git` — for freeze-date verification
- TUI gateway — for smoke tests (not available in headless mode; documented as limitation)

## Smoke Test Procedure (requires TUI)
1. Start TUI: `hermes -p bmad --tui`
2. Run: `/bmad:research test topic`
3. Save output to `planning-artifacts/smoke-test-output.md`
4. Run: `python plugins/bmad/tools/evolve_command/scripts/score_output.py research_structural_v1 planning-artifacts/smoke-test-output.md`
5. Score ≥ 0.7 = G1 PASS

## G1 Gate
- AVG score across 3 research generations ≥ 0.7: PASS, continue to W2
- AVG 0.5-0.7: MARGINAL, continue with reduced scope
- AVG < 0.5: FAIL, halt per D-42, escalate to Epic 17

## G2 Gate
- ALL 5 P1 skills ≥ 0.7 composite
- ALL 6 metrics CI-gated (check_metric_frozen.py passes)
- Total LLM spend ≤ $15
