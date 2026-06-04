---
spec:
  persona: QA
  phase: implementation
  imperative_preamble: true
  verification:
    - "Doctor report generated"
    - "All 10 categories checked"
    - "Findings severity-ranked"
---

Run a read-only diagnostic on this BMAD project.

## What doctor checks (10 categories)

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

## Output

Markdown report with severity-ranked findings:
- 🔴 CRITICAL — must fix before any sprint work
- 🟠 HIGH — should fix soon
- 🟡 MEDIUM — fix when convenient
- 🔵 LOW — nice to have
- ℹ️ INFO — informational only

Use `delegate_task` to run the diagnostic in background if the project is large.
