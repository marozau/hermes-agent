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

## IMPORTANT: How to handle user input

When the user describes what they want in natural language, YOU (the LLM) must:

1. **Parse their intent** — figure out if they want single-repo or workspace mode
2. **Extract worktree specs** — repo names, paths, branches from their description
3. **Construct the args** — build the proper `--workspace --worktree ...` args
4. **Call the handler** — pass the structured args to the Python handler

### When to use workspace mode

Use workspace mode when the user mentions:
- Multiple repos, projects, or codebases
- "both projects", "two projects", "across repos"
- Working on related changes across repos
- "workspace", "worktrees"
- Names of multiple repos (e.g. "hermes-agent and hermes-workspace")

### How to extract worktree specs from natural language

For each repo the user mentions:

1. **NAME** — the repo/project name (e.g. "hermes-agent", "hermes-workspace")
2. **UPSTREAM** — the path to their existing clone
   - If they say "usr-local/hermes" → `~/usr-local/hermes`
   - If they say "hermes-agent" without a path → try `~/usr-local/hermes-agent`
   - If they say "~/projects/foo" → use that
3. **BRANCH** — the branch to work on
   - If they say "branch feat/swarm" → `feat/swarm`
   - If they say "feat/xyz branch" → `feat/xyz`
   - If no branch mentioned → `main`

### Examples

User: "i want to work on hermes-agent and hermes-workspace to improve hermes-swarm"
→ You construct: `--workspace --worktree hermes-agent:~/usr-local/hermes:main --worktree hermes-workspace:~/usr-local/hermes-workspace:main`
→ Call: `/bmad:init --workspace --worktree hermes-agent:~/usr-local/hermes:main --worktree hermes-workspace:~/usr-local/hermes-workspace:main`

User: "set up workspace for hermes-agent (usr-local/hermes, branch feat/swarm) and hermes-workspace (usr-local/hermes-workspace)"
→ You construct: `--workspace --worktree hermes-agent:~/usr-local/hermes:feat/swarm --worktree hermes-workspace:~/usr-local/hermes-workspace:main`
→ Call: `/bmad:init --workspace --worktree hermes-agent:~/usr-local/hermes:feat/swarm --worktree hermes-workspace:~/usr-local/hermes-workspace:main`

User: "/bmad:init --force both projects hermes-agent (usr-local/hermes) and hermes-workspace (usr-local/hermes-workspace)"
→ You construct: `--force --workspace --worktree hermes-agent:~/usr-local/hermes:main --worktree hermes-workspace:~/usr-local/hermes-workspace:main`
→ Call: `/bmad:init --force --workspace --worktree hermes-agent:~/usr-local/hermes:main --worktree hermes-workspace:~/usr-local/hermes-workspace:main`

User: "init bmad for hermes-agent only"
→ Single repo mode: `/bmad:init`

### Single-repo mode

If the user only mentions ONE repo or doesn't mention multiple projects, use standard mode:
```
/bmad:init [--force]
```

Creates:
- `bmad/config.yaml` — project configuration
- `planning-artifacts/workflow-status.yaml` — state ledger
- `planning-artifacts/research/`
- `implementation-artifacts/stories/`

### Workspace mode

If you detect workspace intent (multiple repos), construct and call:
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

#### Options

- `--workspace` — Enable workspace mode (opt-in, per WI-1)
- `--worktree NAME:UPSTREAM:BRANCH` — Add a worktree (repeatable)
- `--force` — Overwrite existing config
