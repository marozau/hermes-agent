# BMAD Evolve Command — Offline Command-Body Tuner

Offline GEPA-based optimizer for BMAD command bodies (dev-story phase 1).

## Architecture

```
evolve_command/
├── _vendor/           # Upstream NousResearch/hermes-agent-self-evolution files
├── evolve_command/    # Main package (CodeOutputModule, CodeOutputJudge, CLI)
├── metrics/           # Pre-registered metric definitions (v1 frozen)
├── dataset/           # Training/test trace data
├── reports/           # Tuning run outputs (gitignored)
├── scripts/           # build_dataset.py, run_optimizer.py
└── tests/             # Isolation, unit, integration tests
```

## Usage

```bash
# Install tooling deps (isolated from plugin runtime)
cd plugins/bmad/tools/evolve_command && pip install -e ".[dev]"

# Build dataset from session history
python scripts/build_dataset.py --sessions ~/.hermes/sessions/ --output dataset/ --seed 42

# Run optimizer
bmad-evolve-command --command dev-story --dataset dataset/tier1_v1 --budget 200 --cap 50
```

## TI-1/TI-2 Isolation

DSPy is declared ONLY in this pyproject.toml. CI enforces:
- `grep -r "import dspy" plugins/bmad/{lib,commands,hooks,scripts}/` returns empty
- No DSPy imports in plugin runtime code
