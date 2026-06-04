---
spec:
  persona: Dev
  phase: implementation
  imperative_preamble: true
  verification:
    - "Migration plan generated"
    - "Waves executed atomically"
    - "Git commits created per wave"
---

Migrate this BMAD project to the current schema version.

## Flags

- `--plan` — Show migration plan without executing
- `--apply` — Execute migration waves
- `--dry-run` — Simulate execution (no git commits)
- `--wave N` — Execute only wave N
- `--resume` — Resume from last successful wave

## Waves

1. Workspace Pattern Fix
2. Config Schema Upgrade
3. Epic Structure Repair
4. Story Audit (diagnostic)
5. OCR Status Check (diagnostic)

Each wave produces one atomic git commit. Use `git revert <SHA>` to rollback.
