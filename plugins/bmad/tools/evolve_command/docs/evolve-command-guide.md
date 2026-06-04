# evolve-command — Operator Manual

> **Epic 13, Story 13.10** — Docs & CI gates

## Overview

`evolve-command` is an offline tuner that uses DSPy, GEPA, LLM-as-Judge, and
constraint validation to optimise BMAD command bodies (e.g. `dev-story`).

It runs **offline** — no DSPy code executes at runtime (TI-1/TI-2).

## Quick Start

```bash
# Install (isolated from plugin runtime)
cd plugins/bmad/tools/evolve_command
pip install -e ".[dev]"

# Import traces from Hermes sessions
bmad-evolve-command import-traces --source hermes --output dataset/ --limit 50

# Optimize (GEPA loop — Story 13.7 carry-forward)
bmad-evolve-command optimize --command dev-story --dataset dataset/ --budget 200 --cap 50

# Dry-run (validate inputs only)
bmad-evolve-command optimize --command dev-story --dataset dataset/ --dry-run
```

## CI Gates

| Gate | Script | What it checks |
|------|--------|----------------|
| TI-2 | `check_no_dspy_in_runtime.sh` | No `import dspy` in runtime plugin code |
| TI-3 | `check_metric_frozen.py` | Metric YAML unchanged after `freeze_date` |
| TI-4 | `check_vendor_attribution.py` | Vendored files have attribution headers |

## Metric Spec

`dev_story_composite_v1.yaml` is FROZEN as of 2026-06-04. Formula:

```
0.4 × test_pass_rate
+ 0.2 × scope_discipline
+ 0.2 × spec_faithfulness
+ 0.1 × regression_safety
+ 0.1 × brevity
```

Hard gates (fire BEFORE LLM judge):
- `test_pass_rate ≥ 0.7`
- `regression_safety == 1.0`
- No deploy verbs (OI-4)
- No credential paths (OI-5)

## TI Invariants

- **TI-1**: dspy declared only in `tools/evolve_command/pyproject.toml`
- **TI-2**: CI grep catches `import dspy`, `from dspy`, lazy imports
- **TI-3**: Metric formula frozen; `_v2` for future revisions
- **TI-4**: Vendor attribution headers + LICENSE + CHANGES.md
- **TI-5**: Hard gates fire before LLM judge (saves tokens)
- **TI-6**: Tuned bodies land as PRs, never auto-applied

## Carry-forward (Epic 13.1)

- Full dataset builder (Story 13.6)
- GEPA optimizer loop (Story 13.7)
- G3 acceptance gate (Story 13.9)
