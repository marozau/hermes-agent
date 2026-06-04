# CHANGES.md — Vendoring Notes

## Source

- **Repository**: NousResearch/hermes-agent-self-evolution
- **Commit**: 2377f9e0
- **License**: MIT (see LICENSE-hermes-agent-self-evolution)

## Vendored Files

| Upstream Path | Local Path | Changes |
|---|---|---|
| `evolution/core/fitness.py` | `_vendor/fitness.py` | Replaced `EvolutionConfig` import with standalone `FitnessConfig` dataclass; added `from __future__ import annotations`; made dataclasses frozen |
| `evolution/core/constraints.py` | `_vendor/constraints.py` | Replaced `EvolutionConfig` import with standalone `ConstraintConfig` dataclass; added `from __future__ import annotations`; made dataclasses frozen |
| `evolution/core/external_importers.py` | `_vendor/external_importers.py` | Replaced `EvalExample`/`EvalDataset` imports with plain dicts; removed `click` CLI (moved to local `cli.py`); added `from __future__ import annotations`; fixed type annotations for scoring dict values |
| `evolution/skills/skill_module.py` | `_vendor/skill_module.py` | Added `from __future__ import annotations`; updated return type annotations to use dict[str, object] |

## Rationale

These files are vendored rather than installed as a dependency because:

1. The upstream `hermes-agent-self-evolution` package requires `dspy>=3.0` which does not yet exist on PyPI.
2. We need to adapt imports to work with standalone minimal config dataclasses rather than the full `EvolutionConfig`.
3. BMAD's offline tuner has different orchestration needs than the upstream evolutionary skill optimizer.

## Adaptation Notes

- **fitness.py**: `FitnessConfig` replaces `EvolutionConfig` — only includes `eval_model` field.
- **constraints.py**: `ConstraintConfig` replaces `EvolutionConfig` — includes size/growth limit fields.
- **external_importers.py**: Returns `list[dict]` instead of `list[EvalExample]` to avoid importing `dataset_builder`.
- **skill_module.py**: Minimal changes; updated type hints for Python 3.9+ compatibility.
