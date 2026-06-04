# BMAD Doctor & Migrate Guide

## /bmad:doctor

Read-only diagnostic for BMAD projects. Checks 10 categories and produces a severity-ranked report.

### Usage

```
/bmad:doctor                    # Scan current project
/bmad:doctor /path/to/project   # Scan specific project
```

### Categories

1. **Workspace Pattern** — config.yaml, worktree directories
2. **Config Schema** — required fields, YAML validity
3. **Status Drift** — sprint-status.yaml consistency
4. **Missing Artifacts** — PRD, architecture, epics
5. **Epic Structure** — epics-stories documents
6. **Schema Version** — config version field
7. **Runtime Drift** — plugin __init__.py hooks
8. **Story Audit (diagnostic)** — story ID format
9. **OCR Status Check (diagnostic)** — OCR CLI availability
10. **Spec Blocks** — Epic 12 adoption

### Severity Levels

- 🔴 CRITICAL — must fix before any sprint work
- 🟠 HIGH — should fix soon
- 🟡 MEDIUM — fix when convenient
- 🔵 LOW — nice to have
- ℹ️ INFO — informational only

## /bmad:migrate

Per-wave BMAD project migration with atomic git commits.

### Usage

```
/bmad:migrate --plan              # Show migration plan
/bmad:migrate --apply             # Execute all waves
/bmad:migrate --dry-run           # Simulate (no commits)
/bmad:migrate --wave 3            # Execute only wave 3
/bmad:migrate --apply --resume    # Resume from last success
```

### Migration Waves

1. **Workspace Pattern Fix** — ensure bmad/config.yaml exists
2. **Config Schema Upgrade** — add version field
3. **Epic Structure Repair** — ensure planning-artifacts/
4. **Story Audit (diagnostic)** — standardize story IDs
5. **OCR Status Check (diagnostic)** — check OCR CLI (optional)

### Rollback

Each wave produces one atomic git commit. To rollback a wave:

```bash
git revert <commit-sha>
```

## Phase Overrides

Projects can declare intentionally-skipped phases in `bmad/config.yaml`:

```yaml
phase_overrides:
  analysis: skipped        # Started at planning
  solutioning: not_needed  # Level-0 project
```

Valid states: `skipped`, `not_needed`, `deferred`.

Doctor honors these markers and won't flag them as drift.
