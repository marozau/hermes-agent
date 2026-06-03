---
spec:
  persona: Architect
  phase: solutioning
  imperative_preamble: true
  verification:
    - "PRD to Architecture alignment verified"
    - "Architecture to Epics alignment verified"
    - "No blocking gaps identified"
---

Run the solutioning gate check before proceeding to implementation:

Verify alignment across all planning artifacts:

1. **PRD ↔ Architecture** — Do all functional requirements have corresponding architecture components?
2. **Architecture ↔ Epics** — Does every architecture component decompose into epics/stories?
3. **PRD ↔ Epics** — Are all functional requirements covered by at least one epic?
4. **NFR Coverage** — Are non-functional requirements (performance, security, scalability) addressed?
5. **Risk Coverage** — Are identified risks mitigated in the design?
6. **Level Check** — Is the architecture appropriate for the project's BMAD level?

For each check:
- ✅ PASS — aligned and documented
- ⚠️ WARN — partially aligned, minor gap noted
- ❌ FAIL — gap requiring remediation before proceeding

Write report to `planning-artifacts/solutioning-gate-check-{project_name}-{date}.md`.

This gate is **required** for level ≥ 2 projects (D-8). All checks must pass (✅) to proceed to implementation.
