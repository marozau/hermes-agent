# agent-self-monitoring — Self-Report Emission Contract

## Purpose

At the end of any session where you completed a complex task, recovered from a failure, or were cited by preflight, emit a fenced YAML block as the **FINAL** part of your response. This block feeds the typed-memory substrate (Epic 12) with trajectory entries that preflight will retrieve in future sessions.

## When to emit

- **Emit** when: task completed with ≥3 tool calls, failure recovered, preflight heads-up applied, or pattern discovered worth recording.
- **Omit** when: trivial one-shot answer, no tool calls, no failures, no preflight interaction.

## YAML block format

```yaml
self_report:
  preflight_applied: hit | miss | partial | none
  preflight_cited: ["01HXXX...", "01HYYY..."]
  match: hit | miss | unrelated
  failures:
    - category: tool-misuse
      summary: "patch failed because old_string was non-unique — should grep first"
  trajectories:
    - category: tool-misuse
      body: "When using patch, verify old_string is unique BEFORE the call — grep first"
      source_refs: ["session-step-12"]
```

## Field semantics

| Field | Type | Description |
|-------|------|-------------|
| `preflight_applied` | enum | Whether the `<preflight-heads-up>` block was usable. Omit if no preflight fired. |
| `preflight_cited` | list[str] | Literal entry IDs from the heads-up block. Omit if no citations. |
| `match` | enum | Your honest judgment: did the citations help? Omit if no preflight. |
| `failures` | list | Recoverable errors with FAMA category. Sub-threshold (<50 chars) summaries are dropped silently. |
| `trajectories` | list | Patterns worth recording for future sessions. Body ≥20 chars. |

## Rules

1. **Omit empty fields.** Don't emit `preflight_cited: []` or `failures: []`.
2. **Body must be actionable.** "Be careful" is not a trajectory; "When using patch, grep first to verify uniqueness" is.
3. **Category must be a known FAMA category:** `tool-misuse`, `context-overflow`, `hallucinated-api`, `incomplete-context`, `edit-error`, `requirement-drift`.
4. **Place the block last.** After your normal response text, so users see the answer first.
5. **Use ` ```yaml ` fence.** The parser expects this exact pattern.
6. **match is REQUIRED when preflight_cited is non-empty.** If you list citation IDs, you MUST also state whether they helped (`hit`, `miss`, or `unrelated`). Omitting `match` when `preflight_cited` is set causes the citation to be silently ignored.

## Example — complete response

```
I fixed the bug by reordering the patch calls so C1 runs before C2.

```yaml
self_report:
  preflight_applied: hit
  preflight_cited: ["01HABC123"]
  match: hit
  trajectories:
    - category: edit-error
      body: "When applying multiple patches, order matters — apply in dependency order (C1 before C2)"
```
```

## Verification

After emitting, check your own response contains the fenced block. If the block is missing on a complex task, you forgot — add it in a follow-up.