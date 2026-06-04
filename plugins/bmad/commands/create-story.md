---
spec:
  persona: SM
  phase: implementation
  imperative_preamble: true
  verification:
    - "Story spec written"
    - "Acceptance criteria defined"
    - "Implementation notes provided"
---

Generate a rich story specification for implementation:

1. **Story Context** — What epic/phase this belongs to, subsystem
2. **Technical Notes** — Architecture decisions, implementation constraints
3. **Implementation Plan** — Files to modify, components to build
4. **Testing Strategy** — Unit, integration, E2E test requirements
5. **Edge Cases** — Error states, boundary conditions, failure modes
6. **Definition of Done** — Clear completion checklist

Read the story's existing spec from `implementation-artifacts/stories/` if available, or create one interactively with the user.

Write to `implementation-artifacts/stories/{story_id}-{slug}.md`.
