---
spec:
  persona: Analyst
  phase: analysis
  imperative_preamble: true
  verification:
    - "BMAD project initialized"
    - "Config created"
    - "Directory structure set up"
---

# /bmad:init

Scaffold a new BMAD project or workspace.

## Usage

### Standard (single-repo)

```
/bmad:init [--force]
```

Creates:
- `bmad/config.yaml` — project configuration
- `planning-artifacts/workflow-status.yaml` — state ledger
- `planning-artifacts/research/`
- `implementation-artifacts/stories/`

### Workspace mode (multi-repo)

When the user mentions working on multiple repos, projects, or worktrees — OR passes `--workspace` — use workspace mode.

```
/bmad:init --workspace --worktree NAME:UPSTREAM:BRANCH [--worktree ...]
```

Creates:
- `bmad/config.yaml` — workspace configuration (workspace_mode: true)
- `planning-artifacts/` — canonical plan (workspace root, never inside a worktree)
- `worktree/<name>/` — git worktrees for each --worktree spec
- `AGENTS.md` — agent orientation (generated from template)
- `CLAUDE.md` — symlink to AGENTS.md (Unix)
- `WORKTREES.md` — live session manifest

#### How to parse user intent

When the user describes a workspace in natural language, extract:

1. **NAME** — the repo/project name (e.g. "hermes-agent", "hermes-workspace")
2. **UPSTREAM** — the path to the existing repo clone (e.g. `~/usr-local/hermes`)
3. **BRANCH** — the branch to work on (default: `main` if not specified)

Common patterns the user might say:
- "work on hermes-agent and hermes-workspace" → look for repos at `~/usr-local/hermes-agent` and `~/usr-local/hermes-workspace`
- "hermes-agent (usr-local/hermes)" → NAME=hermes-agent, UPSTREAM=~/usr-local/hermes
- "branch feat/swarm" → BRANCH=feat/swarm
- "to improve hermes-swarm" → this is the feature goal, not a repo name

If UPSTREAM is not specified, try `~/usr-local/<NAME>` as default.
If BRANCH is not specified, use `main`.

Construct the full args string and call the handler with:
```
--workspace --worktree <NAME>:<UPSTREAM>:<BRANCH> [--worktree ...]
```

#### Options

- `--workspace` — Enable workspace mode (opt-in, per WI-1)
- `--worktree NAME:UPSTREAM:BRANCH` — Add a worktree (repeatable)
- `--force` — Overwrite existing config

#### Examples

User says: "set up a workspace for hermes-swarm across hermes-agent and hermes-workspace"
→ Call: `/bmad:init --workspace --worktree hermes-agent:~/usr-local/hermes:main --worktree hermes-workspace:~/usr-local/hermes-workspace:main`

User says: "i want to work on hermes-agent (branch feat/swarm) and hermes-workspace"
→ Call: `/bmad:init --workspace --worktree hermes-agent:~/usr-local/hermes:feat/swarm --worktree hermes-workspace:~/usr-local/hermes-workspace:main`

If the directory already contains a `bmad/config.yaml` the command will
refuse to overwrite it unless `--force` is passed.
