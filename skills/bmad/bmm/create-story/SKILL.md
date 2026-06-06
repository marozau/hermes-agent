---
name: create-story
description: "Implementation-phase skill for creating rich story specifications — technical notes, implementation plan, testing strategy, edge cases. Trigger on: create story, story spec, user story, write story."
version: 6.6.0
author: BMAD Community (Hermes port by im)
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: ['bmad', 'bmm', 'implementation', 'create-story']
    category: bmad
---
Follow the instructions in ./workflow.md.

---

## Anti-bundling check — Pattern 13 (added 2026-05-21)

Before finalizing each story, ask these questions. If any answer is **yes**,
the story is at risk of producing the atomic-large-edit thrashing loop during
implementation (documented in
`planning-artifacts/research/domain-bmad-large-edit-failure-modes-2026-05-21.md`).

### Check 1: Could this story be split into N independent sub-stories?

If yes AND **N > 3**, split it. Each sub-story:

- Has its own acceptance criteria (Given/When/Then)
- Lands a green commit independently
- Is independently testable

**Example of what NOT to do:** "Fix Z.2 through Z.11" (10 disjoint test
rewrites) → bundle becomes one impossibly-wide change set, dev agent attempts
a 600-LOC atomic Edit, loops, burns tokens. **Correct:** 10 stories,
"Fix Z.2", "Fix Z.3", ..., "Fix Z.11", each ~50–80 LOC, each one commit.

### Check 2: Will this story require rewriting > 100 LOC in one file?

If yes, **list the regions** in the story spec under a "Refactor regions:"
section:

```yaml
refactor_regions:
  - file: tests/docker/acp-integration.test.ts
    blocks:
      - {id: Z.2, lines: 596-650, anchor: "describe('Z.2')"}
      - {id: Z.5, lines: 654-711, anchor: "describe('Z.5')"}
      ...
```

AND require the dev agent to use the **`chunked-refactor`** skill
(`skills/bmad/_shared/chunked-refactor/SKILL.md`). Mention this
explicitly in the story's "Implementation notes" section so the dev agent
sees it on story open.

### Check 3: Will this story produce > 1 logical commit?

If yes, EITHER:
- **Split the story** (preferred) so each commit is one story; OR
- **Document the commit boundary** inside the story spec under a
  "Commit plan:" section listing each intended commit + its scope

A story that's "one logical change" should land as one commit; a story
that's "five logical changes bundled for convenience" should be five
stories.

### Check 4: Does the story span multiple files where each file needs > 50
LOC of changes?

If yes, consider splitting by file. Cross-file changes that share a
single rationale (e.g., adding a new method + its test + its caller) can
stay together; cross-file changes that are independent (e.g., "fix lint
in 5 modules") should split.

### Why this matters

The atomic-large-edit-thrashing loop is the #1 cause of dev-story budget
overruns observed in 2026 production usage. The loop is downstream of
bundling: if SM had split the wide change set at story-creation, the dev
agent would never see the wide-edit need. **The Pattern 13 check at
story-creation is the right gate.**

### Soft override

If a story HAS to bundle N > 3 changes (e.g., they're entangled and can't
be split without forward-references), document the rationale explicitly
in the story spec:

```markdown
## Bundling rationale

This story bundles 5 changes because they share a transactional commit:
[list the 5 changes and explain why splitting creates an inconsistent
intermediate state]

The dev agent MUST use the chunked-refactor skill for the
implementation; the bundle is a planning concession, not an
implementation concession.
```

Without this section, the bundling check produces a warning at story
finalization.

### Reference

`planning-artifacts/research/domain-bmad-large-edit-failure-modes-2026-05-21.md`
— empirical evidence and the rationale for this check.

