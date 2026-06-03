---
spec:
  persona: Analyst
  phase: analysis
  imperative_preamble: true
  verification:
    - "BMAD project initialized"
    - "Config created"
    - "Directory structure set up"
---

# /bmad:init

Scaffold a new BMAD project in the current directory.

Creates:
- `bmad/config.yaml` — project configuration
- `planning-artifacts/workflow-status.yaml` — state ledger
- `planning-artifacts/research/`
- `implementation-artifacts/stories/`

## Usage

```
/bmad:init [--force]
```

If the directory already contains a `bmad/config.yaml` the command will
refuse to overwrite it unless `--force` is passed.
