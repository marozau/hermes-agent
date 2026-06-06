---
spec:
  persona: Analyst
  phase: analysis
  imperative_preamble: true
  verification:
    - "BMAD project initialized"
    - "Config created"
    - "Directory structure set up"
    - "Worktrees created (workspace mode)"
    - "AGENTS.md authored (workspace mode)"
    - "WORKTREES.md authored (workspace mode)"
---

# /bmad:init

The mechanical bootstrap has already run. Your job now is to plan and
execute the remaining setup based on the user's intent.

## What's already done

- `bmad/config.yaml` created (with `workspace_mode: true` if workspace)
- `planning-artifacts/` scaffolded
- `worktree/<name>/` git worktrees created (if workspace mode)

## What you need to do next

### 1. Understand the user's goal

Read their original request (in `{{args}}`). Extract:
- What projects/repos they're working on
- What feature or goal they're pursuing (e.g. "improve hermes-swarm")
- Any specific branch or worktree preferences

### 2. Author AGENTS.md

Create `AGENTS.md` at the workspace root. It should contain:
- Project overview and goal
- Workspace layout (which worktree is which)
- Conventions for the feature work
- How to use the worktrees (cd into `worktree/<name>/` for code changes)
- Planning artifacts location (`planning-artifacts/` at workspace root)

### 3. Create CLAUDE.md symlink

On Unix: `ln -s AGENTS.md CLAUDE.md`

### 4. Author WORKTREES.md

Create `WORKTREES.md` with:
- Table of worktrees: name, upstream, branch, status
- Claim/release protocol for multi-agent coordination
- Current assignments (if any)

### 5. Verify

After setup, verify:
- `bmad/config.yaml` exists and has `workspace_mode: true`
- Each worktree directory exists and is a valid git worktree
- `AGENTS.md` exists and covers the workspace layout
- `CLAUDE.md` is a symlink to `AGENTS.md`
- `WORKTREES.md` exists with worktree manifest

### 6. Next steps

Suggest what the user should do next:
- `/bmad:create-prd` to start planning
- `/bmad:epics-stories` to decompose work
- Direct code exploration in worktrees
