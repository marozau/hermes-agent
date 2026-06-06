# Epic 15.2 Implementation Summary

## What Epic 15.2 Implements

Epic 15.2 restores upstream BMAD template-output mechanisms into Hermes skill
bodies to close the structural-output gap measured in Epic 15 G4 (research
output scored 0.212 vs gold 0.996).

### Core hypothesis
Templates inlined in skill bodies raise structural scores from ~0.2 to
0.7-0.85 by giving the LLM literal output skeletons with placeholders.

## Files Changed

### New files (8)
| File | Purpose |
|------|---------|
| `skills/bmad/templates/research.template.md` | Research doc skeleton (29 lines, frontmatter + sections) |
| `plugins/.../metrics/research_structural_v1.yaml` | FROZEN rubric for research output |
| `plugins/.../metrics/create_prd_structural_v1.yaml` | FROZEN rubric for PRD output |
| `plugins/.../metrics/create_architecture_structural_v1.yaml` | FROZEN rubric for architecture output |
| `plugins/.../metrics/product_brief_structural_v1.yaml` | FROZEN rubric for product brief output |
| `plugins/.../metrics/epics_stories_structural_v1.yaml` | FROZEN rubric for epics/stories output |
| `plugins/.../scripts/score_output.py` | Scoring engine — applies metric YAML to text |
| `plugins/.../scripts/smoke_test_skill.py` | TUI smoke test harness with G1 gate |

### Modified files (6)
| File | Change |
|------|--------|
| `skills/bmad/bmm/research/SKILL.md` | Inline template reference + execution steps |
| `skills/bmad/bmm/create-prd/SKILL.md` | Inline template + anti-patterns |
| `skills/bmad/bmm/create-architecture/SKILL.md` | Inline template + anti-patterns |
| `skills/bmad/bmm/epics-stories/SKILL.md` | Inline template + anti-patterns |
| `plugins/.../scripts/check_metric_frozen.py` | Extended to check ALL metrics (was hardcoded to 1) |
| `tests/plugins/bmad/test_structural_metrics.py` | 10 unit tests for scoring engine |

## How to Test

### Unit tests (headless, runs in CI)
```bash
cd /Users/im/usr-local/hermes
/Users/im/.hermes/hermes-agent/venv/bin/python -m pytest tests/plugins/bmad/test_structural_metrics.py -v
```
**Result: 10 passed** — tests verify perfect outputs score high, minimal outputs
fail gates, missing citations score low, metric YAML validity, and score ranges.

### Full regression suite
```bash
/Users/im/.hermes/hermes-agent/venv/bin/python -m pytest tests/plugins/bmad/ tests/agent/test_skill_commands.py -v
```
**Result: 67 passed, 0 failures**

### Smoke tests (requires TUI + LLM)
```bash
# Start TUI
hermes -p bmad --tui

# In TUI, run skill
/bmad:research AI agents in 2026

# Save output, then score
python plugins/bmad/tools/evolve_command/scripts/score_output.py \
  research_structural_v1 planning-artifacts/smoke-test-output.md
```

G1 gate: avg score ≥ 0.7 across 3 runs = PASS, continue to W2

### Metric freeze check (CI gate)
```bash
python plugins/bmad/tools/evolve_command/scripts/check_metric_frozen.py
```
**Result: 1 PASS (dev_story_composite_v1), 5 SKIP (new metrics, not yet committed)**

## Limitations

1. **LLM smoke tests require TUI** — cannot run from headless environment.
   The smoke_test_skill.py script documents the manual procedure.
2. **Score thresholds are heuristic** — the scoring engine uses regex patterns,
   not semantic understanding. Perfect synthetic outputs may score lower than
   real LLM outputs because the heuristics favor certain keywords.
3. **G1 empirical validation pending** — actual LLM generation scores have not
   been measured yet due to TUI requirement.

## Verification Checklist

| Item | Status | Evidence |
|------|--------|----------|
| Implementation plan produced | ✅ | `planning-artifacts/epic-15-2-implementation-plan.md` |
| All source code changes implemented | ✅ | 12 files in commit `7e1ae757a` |
| Unit tests for scoring engine | ✅ | 10 tests in `test_structural_metrics.py` |
| Full test suite passes | ✅ | 67 passed, 0 failures |
| No TODO stubs / placeholders | ✅ | All functions implemented |
| No debug code | ✅ | No print/logging artifacts |
| Configuration documented | ✅ | `SMOKE_TEST_OUTPUT_DIR`, `SMOKE_TEST_MODEL` env vars |
| Scoring engine works | ✅ | `score_output.py` tested with synthetic inputs |
| Smoke test harness ready | ✅ | `smoke_test_skill.py` with G1 gate logic |
| Written summary | ✅ | This file |
| LLM smoke test run | ❌ | Requires TUI gateway + API key (not available headless) |

## How to Verify It Works

1. **Check templates exist:** `ls skills/bmad/templates/research.template.md`
2. **Check metrics exist:** `ls plugins/bmad/tools/evolve_command/metrics/*.yaml`
3. **Run scoring engine:** `python plugins/.../scripts/score_output.py research_structural_v1 - < your-output.md`
4. **Run unit tests:** `pytest tests/plugins/bmad/test_structural_metrics.py`
5. **Run smoke test (TUI required):** `/bmad:research test topic` then score output

## Next Steps

1. Run actual LLM smoke tests from TUI to validate G1 hypothesis
2. If G1 PASS (avg ≥ 0.7): continue to W2 (create-prd, create-architecture, etc.)
3. If G1 FAIL (avg < 0.5): halt per D-42, escalate to Epic 17
