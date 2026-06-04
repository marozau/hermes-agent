# evolve-command — Architecture Reference

> **Epic 13, Story 13.10** — Internal reference for contributors

## Module Layout

```
plugins/bmad/tools/evolve_command/
├── pyproject.toml              # Isolated deps (dspy, click, rich, pyyaml)
├── README.md                   # Quick overview
├── _vendor/                    # Upstream vendored files (SHA-pinned)
│   ├── fitness.py              # LLM-as-judge scoring
│   ├── constraints.py          # Constraint validation
│   ├── external_importers.py   # Session importers (Hermes, Claude, Copilot)
│   └── skill_module.py         # DSPy module wrapper
├── cli.py                      # Click CLI (bmad-evolve-command)
├── code_output_module.py       # DSPy ChainOfThought adapter
├── judge.py                    # CodeOutputJudge + hard gates
├── importer.py                 # BMADTrace + build_trace
├── metrics/
│   └── dev_story_composite_v1.yaml  # FROZEN metric (TI-3)
├── prompts/
│   ├── scope_discipline_v1.md       # LOCKED judge prompt (UQ §2.4)
│   └── spec_faithfulness_v1.md      # LOCKED judge prompt (UQ §2.4)
├── reports/                    # Tuning run outputs (gitignored)
├── scripts/
│   ├── check_no_dspy_in_runtime.sh   # TI-2 CI gate
│   ├── check_vendor_attribution.py   # TI-4 CI gate
│   └── check_metric_frozen.py        # TI-3 CI gate
├── tests/                      # Isolation, unit, integration tests
└── docs/
    ├── architecture.md         # This file
    ├── evolve-command-guide.md # Operator manual
    └── metric-versioning.md    # TI-3 freeze discipline
```

## Data Flow

```
Session history (~/.hermes/sessions/*.jsonl)
    ↓ importer.py: build_trace()
8-file trace format (story.md, command_body.md, etc.)
    ↓ judge.py: check_hard_gates() → CodeOutputJudge.score()
Scored candidates
    ↓ cli.py: optimize --budget N
Report directory (tuned body, score_card, transcripts)
    ↓ Human review
PR to main (never auto-applied per TI-6)
```

## Isolation Boundary

DSPy lives ONLY in `tools/evolve_command/`. CI enforces:
- `check_no_dspy_in_runtime.sh` greps `plugins/bmad/{lib,commands,hooks,scripts}/`
- `check_vendor_attribution.py` verifies vendored file headers
- `check_metric_frozen.py` verifies metric YAML unchanged after freeze_date

## T-11 Wiring

Story 13.8 wired `predicate_runner.run_predicates` to the dev-story handler.
After `/bmad:dev-story` completes, predicates fire and results land in
`sprint-status.yaml` under `predicate_results.<story_id>`.

## Locked Prompts (UQ §2.4)

Judge prompts are documented on disk at `prompts/scope_discipline_v1.md` and
`prompts/spec_faithfulness_v1.md` for audit trail. The actual prompts used at
runtime are the DSPy Signature docstrings in `judge.py`. Changes to the prompt
require updating BOTH the disk file (versioned) AND the code (for reproducibility).

## Carry-forward (Epic 14)

- Story 13.6: Full dataset builder (currently stub)
- Story 13.7: GEPA optimizer loop (currently stub)
- Story 13.9: G3 acceptance gate (needs real dataset)
