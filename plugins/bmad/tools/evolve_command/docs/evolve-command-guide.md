# evolve-command — Operator Manual

> **Epic 13, Story 13.10** — Docs & CI gates

## Overview

`evolve-command` is an offline tuner that uses DSPy, GEPA, LLM-as-Judge, and
constraint validation to optimise BMAD command bodies (e.g. `dev-story`).

It runs **offline** — no DSPy code executes at runtime. All tuning happens
inside `plugins/bmad/tools/evolve_command/` and the results are reviewed
before merging into the plugin.

## Quick Start

```bash
cd worktree/hermes-epic-13

# 1. Install evolve-command deps (isolated from runtime plugin)
cd plugins/bmad/tools/evolve_command
pip install -e ".[dev]"

# 2. Run the tuner on dev-story
python -m evolve_command.cli tune \
  --command dev-story \
  --metric dev_story_composite_v1 \
  --metric-config ../metrics/dev_story_composite_v1.yaml \
  --rounds 3 \
  --output reports/dev-story-tuning-$(date +%Y%m%d).json

# 3. Review the report
cat reports/dev-story-tuning-*.json | python -m json.tool
```

## CI Gates (run on every PR)

| Script | What it checks | Fail = |
|--------|---------------|--------|
| `check_no_dspy_in_runtime.sh` | No `import dspy` in `plugins/bmad/{lib,commands,hooks,scripts}/` | Runtime isolation breach (TI-2) |
| `check_vendor_attribution.py` | All vendored files under `_vendor/` have attribution headers | Missing license notice |
| `check_metric_frozen.py` | `metrics/dev_story_composite_v1.yaml` unchanged after freeze date | Metric drift (TI-3) |

Run all gates:

```bash
bash plugins/bmad/tools/evolve_command/scripts/check_no_dspy_in_runtime.sh
python plugins/bmad/tools/evolve_command/scripts/check_vendor_attribution.py
python plugins/bmad/tools/evolve_command/scripts/check_metric_frozen.py
```

## Metric: `dev_story_composite_v1`

**Frozen:** 2026-06-04 (TI-3)

Formula (weights):

| Component | Weight | Gate |
|-----------|--------|------|
| `test_pass_rate` | 0.4 | ≥ 0.7 |
| `scope_discipline` | 0.2 | — |
| `spec_faithfulness` | 0.2 | — |
| `regression_safety` | 0.1 | == 1.0 |
| `brevity` | 0.1 | — |

**Hard gates (ConstraintValidator):**
- `test_pass_rate >= 0.7`
- `regression_safety == 1.0`
- No deploy verbs (`terraform apply`, `kubectl apply`, etc.)
- No credential paths (`~/.ssh`, `~/.aws`, etc.)

## TI Invariants

| ID | Invariant | Enforcement |
|----|-----------|-------------|
| TI-1 | `dspy` declared only in `tools/evolve_command/pyproject.toml` | CI grep |
| TI-2 | No `import dspy` in `plugins/bmad/{lib,commands,hooks,scripts}/` | `check_no_dspy_in_runtime.sh` |
| TI-3 | Metric formula frozen after first run | `check_metric_frozen.py` |

## Reports

All tuning reports land in `reports/` (gitignored except `.gitkeep`).
Reports are JSON with schema: `{command, metric, rounds, results[], timestamp}`.

## Troubleshooting

**"DSPy import found in runtime"** — A PR added `import dspy` to a runtime
file. DSPy must only be imported inside `tools/evolve_command/`. Fix: move
the import behind a lazy/conditional gate or remove it.

**"Metric file modified after freeze"** — Someone changed the metric weights
or gates after the freeze date. This requires an explicit unfreeze decision
and a new freeze date. Document in the metric YAML's `freeze_date` field.
