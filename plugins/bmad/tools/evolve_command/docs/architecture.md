# evolve-command — Architecture Reference

> **Epic 13, Story 13.10** — Internal reference for contributors

## Module Layout

```
plugins/bmad/tools/evolve_command/
├── pyproject.toml              # Isolated deps (dspy, gepa, etc.)
├── README.md                   # Quick overview
├── _vendor/                    # Vendored upstream (NousResearch/hermes-agent-self-evolution)
│   ├── __init__.py
│   ├── _attribution.py         # Vendored-file attribution registry
│   ├── gepa/                   # GEPA optimizer
│   ├── llm_judge/              # LLM-as-Judge evaluator
│   ├── constraint_validator/   # Hard-gate enforcement
│   └── session_importers/      # Trajectory → training data
├── evolve_command/
│   ├── __init__.py
│   ├── cli.py                  # CLI entrypoint (`python -m evolve_command.cli`)
│   ├── tuner.py                # Main tuning loop
│   ├── metric.py               # Metric loading + evaluation
│   └── report.py               # Report generation
├── metrics/
│   └── dev_story_composite_v1.yaml   # Frozen metric definition (TI-3)
├── docs/
│   ├── evolve-command-guide.md       # Operator manual
│   ├── architecture.md               # This file
│   └── metric-versioning.md          # TI-3 freeze discipline
├── scripts/
│   ├── check_no_dspy_in_runtime.sh   # TI-2 isolation gate
│   ├── check_vendor_attribution.py   # Vendor attribution gate
│   └── check_metric_frozen.py        # TI-3 metric freeze gate
├── tests/
│   └── test_t11_closure.py           # predicate_runner wiring test
└── reports/
    ├── .gitignore
    └── .gitkeep
```

## Data Flow

```
User runs `evolve_command.cli tune`
        │
        ▼
   tuner.py ──────────────────────────────────────────────┐
   │ 1. Load metric from metrics/*.yaml                    │
   │ 2. Load training data via session_importers           │
   │ 3. For each round:                                    │
   │    a. Generate candidate command bodies               │
   │    b. Evaluate with metric.py                         │
   │    c. Score with LLM-as-Judge (in _vendor/)          │
   │    d. Enforce hard gates with ConstraintValidator     │
   │    e. Select best via GEPA optimizer                  │
   │ 4. Write report to reports/                           │
   └───────────────────────────────────────────────────────┘
        │
        ▼
   Human reviews report, optionally merges into command body
```

## Isolation Boundary

**TI-1/TI-2:** DSPy and all tuning machinery live ONLY under
`plugins/bmad/tools/evolve_command/`. The runtime plugin
(`plugins/bmad/{lib,commands,hooks,scripts}/`) must NEVER import dspy
or any tuning dependency. The CI script `check_no_dspy_in_runtime.sh`
enforces this on every PR.

The boundary exists because:
1. DSPy is a heavy dependency (~200MB transitive) — runtime users shouldn't pay it
2. Tuning is offline-only — runtime code just loads the tuned body
3. The vendor tree (`_vendor/`) has its own license obligations

## Vendor Attribution

Vendored files from `NousResearch/hermes-agent-self-evolution` (SHA `2377f9e0`)
must retain attribution headers. `check_vendor_attribution.py` verifies this.
See `_vendor/_attribution.py` for the registry of expected headers.

## Predicate Runner Integration (Epic 12 T-11)

Story 13.8 closes Epic 12's T-11 carry-forward by wiring
`predicate_runner.run_predicates` to the dev-story handler. This means:
- Success predicates defined in story specs are evaluated after dev-story runs
- The predicate runner checks: test_pass_rate, scope_discipline, etc.
- Results feed back into the metric for the next tuning round

See `tests/test_t11_closure.py` for the integration test.
