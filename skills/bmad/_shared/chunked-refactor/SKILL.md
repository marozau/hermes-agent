---
name: chunked-refactor
description: |
  Pattern for safely rewriting many disjoint blocks of one file. Use when
  an Edit fails twice in a row on a wide change, when the planned change
  touches more than 3 regions of one file, or when old_string > 100 lines.
  Avoids the atomic-large-edit thrashing loop documented in
  planning-artifacts/research/domain-bmad-large-edit-failure-modes-2026-05-21.md.
  Trigger on: "chunked refactor", "edit failed twice", "rewrite multiple blocks",
  "large rewrite", "atomic edit failing".
version: 6.6.0
author: BMAD Community (Hermes port by im)
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [bmad, refactor, edit-discipline, anti-loop, recovery-pattern]
    category: bmad
    related_skills: [dev-story, code-review]
---

# Chunked Refactor Pattern

## When to use this skill

Invoke when ANY of these conditions hold:

- About to rewrite more than 100 LOC in a single Edit
- Last 2 Edits returned `"old_string not found"` or equivalent
- Planned change touches more than 3 disjoint regions of one file
- File is > 500 LOC and you're rewriting > 20% of it

## When NOT to use

- Edits ≤ 50 lines with a unique anchor → atomic Edit is fine
- New-file creation → use Write
- Mechanical rename across many files → use grep+sed in Bash (deterministic transform, not content generation)

## Protocol — strict

### Step 1: List the regions

Produce a numbered table of every block to rewrite:

| # | Block ID | Start line | End line | Anchor (1 stable line above) |
|---|---|---|---|---|
| 1 | `describe("Z.2")` | 596 | 650 | `// === Gap story Z.2 ===` |
| 2 | `describe("Z.3")` | 905 | 979 | `// === Gap story Z.3 ===` |
| ... | ... | ... | ... | ... |

Record the table in the conversation. This is the work plan.

### Step 2: Commit unrelated work first

If you've already landed a helper, test util, or unrelated fix in the working
tree, commit it BEFORE attempting the risky chunks. A green helper-only commit
proves the pipeline works before any risky rewrite lands.

### Step 3: One block per Edit. One commit per block.

For each row in the table:

1. **Read the block** (current bytes — don't trust memory)
2. **Construct `old_string`** = block contents + 1 line of stable context above + 1 line below
3. **Construct `new_string`** = the rewritten block + the same context lines
4. **Edit** — single tool call, that block only
5. **Verify** — file builds/lints/parses; tests still compile
6. **Commit** — one logical change per commit (BMAD Pattern 13)

If the edit succeeds: move to the next row.

### Step 4: Stuck on a particular block? Delete-then-append fallback

When in-place Edit keeps failing on the same block:

1. **Edit 1: delete the block.** Replace block contents with empty string. A
   deletion-only Edit often succeeds where a substitution fails because the
   `new_string` is trivial.
2. **Edit 2: append the new block at end of file.** Use a stable EOF anchor
   (last line of file, or a `// EOF` marker).
3. **Optional follow-up commit:** move the block to its logical position
   using a small Edit on the file's outline.

### Step 5: Anti-patterns — DO NOT

- **DO NOT** retry the same atomic Edit > 2 times with cosmetic tweaks ("maybe
  add a space," "maybe escape that quote"). If the first 2 attempts failed
  identically, the 3rd will not succeed.
- **DO NOT** Re-Read the same file > 2 times in a row hoping the anchor will
  match. The file hasn't changed between Reads; your mental model of the
  anchor is wrong.
- **DO NOT** pivot to `sed` / `python` / `execute_code` for content-generating
  rewrites. Line numbers shift after every partial edit; the splice becomes
  non-idempotent and one failure leaves the file in a worse state than where
  it started.
- **DO NOT** attempt a > 100-line atomic Edit "just to see if it works." The
  failure mode is well-documented; trust the data.

## Recovery when truly stuck

If you've followed Step 1–4 and are still failing after 2 attempts on the
same block:

1. **Commit whatever has actually landed** so the next agent or human starts
   from a checkpoint, not from the beginning.
2. **Mark the story as `blocked: chunked-refactor-needed`** in
   `sprint-status.yaml`.
3. **Escalate to SM (or main agent under autonomous DAG execution per
   `design-prefect-bridge-2026-05-20.md`)** with the table from Step 1, the
   blocks that landed, and the blocks that didn't.
4. **Do NOT continue to loop.** The right next action is escalation, not
   retry.

## Why this pattern works

`old_string` must match byte-for-byte across the full replaced span. As span
size grows, the probability of any whitespace drift, line-wrap difference, or
escaped-char mismatch grows superlinearly. By keeping each Edit small
(~50–80 lines, single coherent block), each Edit has a high success rate
independently, and the per-block commits create checkpoints so a failure
loses one block of work, not the whole refactor.

## Integration with BMAD

- `bmad-dev-story` references this skill in its Anti-loop section as the
  go-to recovery pattern.
- `bmad-create-story` references this skill in its Anti-bundling check —
  if a story will require > 3 disjoint rewrites in one file, the SM is
  prompted to either split the story OR explicitly require the dev agent
  to use this skill.

## References

- `planning-artifacts/research/domain-bmad-large-edit-failure-modes-2026-05-21.md`
  — empirical analysis of the atomic-large-edit loop pattern
- `planning-artifacts/design-prefect-bridge-2026-05-20.md` — escalation
  semantics under autonomous DAG execution
