---
spec:
  persona: PM
  phase: planning
  imperative_preamble: true
  verification:
    - "PRD written to planning-artifacts/"
    - "Functional requirements complete"
    - "Non-functional requirements defined"
    - "Epics and stories decomposed"
---

Create a Product Requirements Document (PRD) for the project:

1. **Executive Summary** — Problem statement, solution overview, success criteria
2. **Functional Requirements** — Detailed capability specifications, acceptance criteria
3. **Non-Functional Requirements** — Performance, security, scalability, reliability
4. **Epics and User Stories** — Decomposed work with priorities
5. **Dependencies and Constraints** — External systems, resource limits, assumptions

Use `delegate_task` for large PRDs:
- Agent 1: Functional Requirements section
- Agent 2: Non-Functional Requirements section
- Agent 3: Epics and User Stories section

Write to `planning-artifacts/prd-{project_name}-{date}.md`.

Use the `prd-template.md` from `~/.hermes/skills/bmad/templates/` if available.
