---
spec:
  persona: Dev
  phase: implementation
  predicate_module: plugins.bmad.predicates.dev_story
  output_artifacts: []
  verification:
    - description: All tests pass
      predicate: predicates.dev_story.tests_pass
    - description: No regressions in existing test suite
      predicate: predicates.dev_story.no_regressions
    - description: Story acceptance criteria verified
      predicate: predicates.dev_story.ac_verified
    - description: Code follows project conventions
    - description: Diff is minimal and focused
---

## Instructions

Implement a user story end-to-end following TDD principles:

1. **Read Story Spec** — Load the full story from the provided path or epic-doc anchor
2. **Implementation Plan** — Design the code changes needed
3. **RED Phase** — Write failing tests that define the acceptance criteria
4. **GREEN Phase** — Implement the code to make tests pass
5. **REFACTOR Phase** — Clean up implementation while keeping tests green
6. **Verification** — Run all tests; verify against acceptance criteria

For each implementation step:
- Create test file(s) first
- Implement the production code
- Run tests to verify
- Document any design decisions

## Anti-patterns

- Do NOT skip the RED phase
- Do NOT implement without a failing test first
- Do NOT commit code that breaks existing tests
- Do NOT bundle multiple stories into one commit
