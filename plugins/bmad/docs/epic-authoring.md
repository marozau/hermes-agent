# Epic Authoring Guide

How to write epic documents that `/bmad:orchestrate` can parse and execute.

## Quick Start

1. Use the epics-stories skill (`/bmad:epics-stories`) to generate the epic breakdown
2. Ensure every story has the consolidated section shape (see below)
3. Run `/bmad:orchestrate <epic>` to execute

## Consolidated Story Section Shape

Every story MUST have these sections:

```markdown
### Story 7.1: Config schema additions

**Description:** Add workspace_mode, worktrees:, and runtime_mirror: fields
to the BMAD config schema with validation.

**Dependencies:** none

**Acceptance Criteria:**

- **Given** a bmad/config.yaml without workspace_mode
- **When** bmad-init --workspace runs
- **Then** workspace_mode: true is set with validated worktrees list

**success_predicates:**
- file_exists:plugins/bmad/lib/config.py
- tests_pass:plugins/bmad/tests/unit/test_config_workspace.py
- grep:workspace_mode:plugins/bmad/lib/config.py
- coverage_at_least:80

**verification_gate:** none

**failure_action:**
  max_attempts: 2
  action: retry_then_escalate

**Effort:** 1.5h
**Touches:** lib/config.py, tests/unit/test_config_workspace.py
```

## Predicate Kinds

| Kind | Payload | Description |
|------|---------|-------------|
| `file_exists` | relative path | Checks file exists at `{project_dir}/{path}` |
| `tests_pass` | glob pattern | Runs `pytest {pattern} -q` |
| `grep` | `pattern:file` | Regex search in file |
| `coverage_at_least` | integer | Threshold check |
| `shell` | command | Allowlisted read-only command |

### Security (B-7)

Predicates MUST use `kind:payload` format. Bare shell commands are rejected.
This prevents arbitrary code execution from epic doc content.

Shell predicates are restricted to an allowlist:
`ls`, `cat`, `head`, `tail`, `wc`, `grep`, `find`, `test`, `true`, `false`,
`git status`, `git diff`, `git log`, `git show`, `pytest`, `coverage`

## Verification Gates

- `none` (default): no additional verification after predicates pass
- `adversarial`: spawns a read-only Opus reviewer that checks the
  implementation against ACs. The reviewer is restricted to read-only
  tools (Read, Grep, Glob, Bash with read-only commands). If the
  reviewer dispatch fails, the gate FAILS CLOSED (not open).

## Dependencies and Wave DAG

Stories declare dependencies on other stories via the `Dependencies:` field.
The orchestrator builds a topological sort (wave DAG) and executes stories
in parallel within each wave.

- Wave 0: stories with no dependencies
- Wave 1: stories that depend only on wave 0 stories
- Wave N: stories that depend only on wave 0..N-1 stories

Cycles are detected and raise `CyclicDependencyError` (M-9).

## Failure Actions

```yaml
failure_action:
  max_attempts: 2        # How many retries before halting
  action: retry_then_escalate  # What to do after max_attempts
```

- `retry_then_escalate` (default): retry up to max_attempts, then halt the epic
- `skip`: skip the story and continue (for non-critical stories)

## Example: Epic 7 Story Spec

See `planning-artifacts/epics-stories-orchestrate-2026-05-31.md` for a
complete example of the consolidated shape in production use.

## Anti-Bundling (Pattern 13)

Before finalizing each story, check:
1. Could this split into > 3 independent sub-stories? → Split it
2. Will this rewrite > 100 LOC in one file? → Document refactor regions
3. Will this produce > 1 logical commit? → Split or document commit plan
4. Does this span multiple files each needing > 50 LOC? → Consider splitting

See `planning-artifacts/research/domain-bmad-large-edit-failure-modes-2026-05-21.md`
for empirical evidence.
