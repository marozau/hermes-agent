---
spec:
  persona: Dev
  phase: implementation
  imperative_preamble: true
  verification:
    - "Feature implemented"
    - "Tests pass"
    - "Code follows conventions"
---

Quick development workflow for small features (level 0-1):

1. **Clarify Intent** — Understand what needs to be built (one round of questions max)
2. **Quick Plan** — Design the implementation approach (3-5 bullet points)
3. **Implement** — Build the feature with tests
4. **Review** — Self-review against quality standards
5. **Present** — Summarize what was built

This is for single-turn implementation. For complex features requiring full PRD, architecture, and multiple stories, use the standard workflow.

Write implementation to the appropriate location in the project and document in `implementation-artifacts/quick-dev-{feature_name}-{date}.md`.
