---
name: dev-story
description: "Implementation-phase skill for implementing a user story end-to-end with TDD — RED/GREEN/REFACTOR. Trigger on: implement story, dev story, develop story, TDD."
version: 6.6.0
author: BMAD Community (Hermes port by im)
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: ['bmad', 'bmm', 'implementation', 'dev-story']
    category: bmad
---
Follow the instructions in ./workflow.md.

---

## Anti-loop discipline (added 2026-05-21)

If the SAME tool call (Edit/Write/Bash/etc.) fails 2 times in a row with the
same error message (e.g. `"old_string not found"`, `"file already exists"`,
`"command not found"`), **you are looping**. STOP and apply ONE of:

### 1. Chunk the change

If the failure is an Edit on a large block, invoke the **`chunked-refactor`**
skill (`skills/bmad/_shared/chunked-refactor/SKILL.md`). The chances
the third retry succeeds are < 10% if the first two failed identically. The
chunked-refactor pattern brings the per-edit success rate back to normal by
keeping each Edit at single-block scale.

### 2. Verify the read

If the file has been edited between Reads, the anchor text may have moved.
Re-Read the EXACT region you intend to replace (use `offset` / `limit` if
allowed — but check Edit anchors against the **current bytes**, not your
memory of an earlier Read).

### 3. Profile-capability check

If you're stuck because a required tool isn't installed (e.g. `pytest: not
found`, `npm: not found`), escalate to provisioning rather than improvising:

1. Check whether the assigned profile is supposed to have the capability per
   the DAG node's `required_capabilities` field (or the story spec)
2. If yes but the capability is missing → file a `blocked:
   profile-capability-missing` and escalate to SM / main agent
3. Do NOT pivot to a different tool to "make do" — that creates technical
   debt the next story will hit too

### 4. Commit progress, escalate

If steps 1–3 don't unstick you:

1. **Commit whatever has actually landed.** Even an empty stub commit
   documents where the work stopped.
2. **Mark the story as `blocked:` in `sprint-status.yaml`** with one of
   these reason codes:
   - `blocked: chunked-refactor-needed` — large-edit thrashing
   - `blocked: profile-capability-missing` — missing tool
   - `blocked: spec-clarification-needed` — spec is ambiguous
   - `blocked: external-dependency` — waiting on something outside the
     story's scope
3. **Hand back to the SM** (or to main agent under autonomous DAG execution
   per `design-prefect-bridge-2026-05-20.md`).

### Anti-patterns — DO NOT

- DO NOT re-attempt the same Edit with cosmetic tweaks ("maybe add a
  space," "maybe escape that quote")
- DO NOT Re-Read the entire file more than 2 times in a row
- DO NOT pivot to `sed` / `python` / `execute_code` for content-generating
  rewrites — line numbers shift, splice becomes non-idempotent
- DO NOT attempt a > 100-line atomic Edit "just to see if it works"
- DO NOT bundle multiple stories into one commit ("I'll fix Z.2 through Z.11
  in one shot") — see `bmad-create-story` Anti-bundling section for why

### Reference

`planning-artifacts/research/domain-bmad-large-edit-failure-modes-2026-05-21.md`
documents the empirical failure pattern and the rationale for this
discipline.

