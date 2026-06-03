---
spec:
  persona: QA
  phase: implementation
  imperative_preamble: true
  verification:
    - "Code review completed"
    - "Findings categorized by severity"
    - "Actionable recommendations provided"
---

Review the implementation against acceptance criteria and quality standards:

1. **Functional Correctness** — Does the code match the story's acceptance criteria?
2. **Code Quality** — Readability, structure, patterns, maintainability
3. **Test Coverage** — Are there unit/integration/E2E tests? Do they pass?
4. **Security Review** — Common vulnerabilities (injection, auth, data exposure)
5. **Edge Cases** — Error handling, boundary conditions, missing validations
6. **Documentation** — Are changes documented? Updated README/API docs?

Use `delegate_task` for large reviews:
- Agent 1: Functional + Acceptance criteria check
- Agent 2: Code quality + Patterns
- Agent 3: Security + Edge cases

Report findings with severity: 🔴 Critical, 🟡 Warning, 🟢 OK.
