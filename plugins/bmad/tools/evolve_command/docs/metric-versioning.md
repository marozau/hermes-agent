# Metric Versioning — TI-3 Freeze Discipline

> **Invariant TI-3:** Metric formula is frozen after first run.

## Why Freeze?

The metric `dev_story_composite_v1` defines what "good" means for a tuned
command body. If the metric changes between runs, comparisons are invalid —
a score of 0.85 on Monday isn't the same 0.85 on Tuesday.

Freezing ensures:
- Reproducibility: same input → same score, always
- Fairness: candidates from different rounds are comparable
- Auditability: CI can verify the metric hasn't drifted

## Freeze Date

**`2026-06-04`** — set in `metrics/dev_story_composite_v1.yaml` field `freeze_date`.

## What's Frozen

| Field | Status | Change Process |
|-------|--------|----------------|
| `formula.weights` | FROZEN | New version (v2) required |
| `formula.hard_gates` | FROZEN | New version (v2) required |
| `description` | Mutable | Cosmetic, no version bump |
| `freeze_date` | Immutable | Set once, never changed |

## How to Unfreeze (Emergency Procedure)

If a metric correction is needed:

1. Create `metrics/dev_story_composite_v2.yaml` with the corrected formula
2. Set `freeze_date` to the new freeze date
3. Add `supersedes: dev_story_composite_v1` to the YAML
4. Update all tuning commands to reference the new metric
5. Archive the old metric (don't delete — historical reports reference it)
6. Update CI gate `check_metric_frozen.py` to check v2's freeze date

## CI Enforcement

`check_metric_frozen.py` verifies:
1. The metric file exists at the expected path
2. The file's `freeze_date` field is present and valid (ISO date)
3. The file's git modification time is not after the freeze date

If the file was modified after the freeze date, CI fails with:

```
METRIC FREEZE VIOLATION: metrics/dev_story_composite_v1.yaml
modified at 2026-06-10 but freeze_date is 2026-06-04.
Create a new metric version instead.
```

## Version Naming

Pattern: `<metric_name>_v<N>.yaml`

- `dev_story_composite_v1.yaml` — first version, frozen 2026-06-04
- `dev_story_composite_v2.yaml` — hypothetical second version

Each version is permanent. Never mutate a frozen version's weights or gates.
