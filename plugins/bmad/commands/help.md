---
spec:
  persona: System
  phase: informational
  imperative_preamble: false
  verification:
    - "help output displayed correctly"
---

# /bmad:help

List all BMAD slash commands. **Every BMAD command is namespaced under
`bmad:`** to avoid conflicts with other Hermes plugins. Use the full
namespaced form (e.g. `/bmad:brainstorm`, not `/brainstorm`).

## Canonical command list

### Meta
- `/bmad:init` — scaffold a BMAD project in cwd
- `/bmad:status` — show current phase + slot state + recommend next command
- `/bmad:dashboard` — render workflow + sprint + sub-agent activity
- `/bmad:help` — this list
- `/bmad:party-mode` — multi-persona round-table discussion

### Phase 1 — Analysis
- `/bmad:product-brief` — create a product brief
- `/bmad:research` — domain / technical / market research
- `/bmad:brainstorm` — creative ideation session
- `/bmad:document-project` — document an existing brownfield project
- `/bmad:quick-spec` — quick-flow level-0/1 spec

### Phase 2 — Planning
- `/bmad:create-prd` — create a PRD from scratch
- `/bmad:validate-prd` — validate an existing PRD against standards
- `/bmad:edit-prd` — improve an existing PRD
- `/bmad:create-ux-design` — UX design and patterns

### Phase 3 — Solutioning
- `/bmad:create-architecture` — architecture decisions doc
- `/bmad:epics-stories` — decompose PRD into epics + stories
- `/bmad:solutioning-gate-check` — verify PRD ↔ architecture ↔ epics alignment

### Phase 4 — Implementation
- `/bmad:sprint-planning` — sprint plan + sprint-status.yaml
- `/bmad:create-story` — rich story spec for a single sprint story
- `/bmad:dev-story` — implement a story end-to-end with TDD
- `/bmad:code-review` — multi-reviewer adversarial fan-out (Blind Hunter +
  Edge Case Hunter + Acceptance Auditor + optional OCR static analysis)
- `/bmad:correct-course` — fix off-track implementation
- `/bmad:quick-dev` — quick-flow level-0/1 implementation

### TEA (Test Architecture)
- `/bmad:test-framework` — initialize test framework (Playwright / Cypress / pytest)
- `/bmad:atdd` — acceptance test-driven development
- `/bmad:test-design` — test plans (system / epic level)
- `/bmad:test-review` — test quality review
- `/bmad:trace` — traceability matrix + quality gate decision
- `/bmad:nfr` — NFR assessment (security, performance, reliability, scalability)
- `/bmad:ci` — CI/CD quality pipeline scaffold
- `/bmad:automate` — expand test automation coverage

### CIS (Creative Intelligence Suite)
- `/bmad:brainstorming` — Carson (brainstorming coach)
- `/bmad:design-thinking` — Maya (design thinking)
- `/bmad:problem-solving` — Dr. Quinn (problem solving)
- `/bmad:innovation-strategy` — Victor (innovation strategy)
- `/bmad:storytelling` — Sophia (storytelling)
- `/bmad:presentation` — Caravaggio (presentation expert)

### BMB (BMAD Builder)
- `/bmad:agent-builder` — Bond (agent builder)
- `/bmad:module-builder` — Morgan (module builder)
- `/bmad:workflow-builder` — Wendy (workflow builder)

## Namespacing rules — IMPORTANT for documentation + memory

- **Every BMAD slash command is registered as `bmad:<verb-noun>`**
  (e.g. the registered name is `bmad:brainstorm`, never bare `brainstorm`)
- **When writing prose, memory entries, or documentation about BMAD
  commands, always use the full namespaced form.** Bare names like
  `/brainstorm` will fail to invoke and confuse users about which plugin
  owns the command.
- **The `bmad:` prefix is what makes BMAD commands distinguishable from
  other Hermes plugins.** Do not drop it.

## CLI commands (not slash)

Two CLI commands are registered for shell use (no `bmad:` prefix in CLI
because shell doesn't have the same conflict surface):

- `hermes bmad-init` — bootstrap a BMAD project from a terminal
- `hermes bmad-check-port` — verify port completeness vs BMAD v6.6.0 source
