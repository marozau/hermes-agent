# Epic 6 — Workspace-Mode: Documentation

## Overview

Workspace-mode makes every BMAD project optionally adopt a dual-layer layout: planning at a workspace root, code repos underneath as git worktrees. This enables cross-repo features, multi-agent isolation, and clean feature-branch diffs.

**Opt-in only.** Existing single-repo BMAD projects are byte-for-byte unaffected.

## Quick Start

```bash
# Initialize a workspace with two worktrees
hermes bmad-init --workspace \
  --worktree hermes-agent:~/usr-local/hermes:feat/my-feature \
  --worktree hermes-bmad:~/usr-local/hermes-bmad:feat/my-feature \
  --project-name "My Feature Workspace"

# Result:
# my-feature-workspace/
# ├── AGENTS.md              ← agent orientation (auto-generated)
# ├── CLAUDE.md              ← symlink → AGENTS.md
# ├── WORKTREES.md           ← live session manifest
# ├── bmad/config.yaml       ← workspace_mode: true
# ├── planning-artifacts/    ← THE PLAN
# └── worktree/
#     ├── hermes-agent/      ← git worktree on feat/my-feature
#     └── hermes-bmad/       ← git worktree on feat/my-feature
```

## Config Schema (`bmad/config.yaml`)

```yaml
project_name: "My Feature"
workspace_mode: true          # Enables all workspace features
worktrees:
  - name: hermes-agent        # Identifier (alphanumeric + hyphens/underscores)
    upstream: ~/usr-local/hermes  # Path to upstream git repo
    branch: feat/my-feature      # Branch to check out
    path: worktree/hermes-agent  # Relative path from workspace root
    runtime_mirror: ~/.hermes/hermes-agent  # Optional: auto-sync target
  - name: hermes-bmad
    upstream: ~/usr-local/hermes-bmad
    branch: feat/my-feature
    path: worktree/hermes-bmad
```

### Pydantic Models

- `WorktreeSpec(name, upstream, branch, path, runtime_mirror=None)` — frozen, validates path safety
- `WorkspaceConfig(workspace_mode=False, worktrees=[])` — top-level config

### Validation Rules

- `name`: alphanumeric + hyphens/underscores only
- `path`: must be relative, start with `worktree/<name>`, no `..` components
- `runtime_mirror`: optional string, defaults to `None`

## CLI Commands

### `bmad-init --workspace`

```bash
hermes bmad-init --workspace \
  --worktree NAME:UPSTREAM:BRANCH [...] \
  [--project-name NAME] \
  [--envrc]
```

- Creates workspace layout with git worktrees
- Renders AGENTS.md from Jinja2 template
- Creates CLAUDE.md symlink (Unix) or copy (Windows)
- Renders WORKTREES.md with initial idle state
- Idempotent guard: refuses if workspace already initialized
- Git failure handling: rolls back partial scaffold on error

### `/bmad:worktree-status`

```bash
# Read-only display
/bmad:worktree-status

# Claim a worktree
/bmad:worktree-status --claim hermes-agent --task "Story 6.3"

# Release a worktree
/bmad:worktree-status --release hermes-agent

# Force override a claim
/bmad:worktree-status --claim hermes-agent --task "Urgent fix" --force
```

## Write Boundary (pre_tool_call hook)

When `workspace_mode: true`, the `pre_tool_call` hook blocks writes to paths outside:
- `planning-artifacts/` (workspace root)
- `worktree/<name>/` (any declared worktree)
- `bmad/` (config files)
- Workspace root files (AGENTS.md, CLAUDE.md, WORKTREES.md)

```
# BLOCKED: write to upstream repo
Write ~/usr-local/hermes/lib/foo.py → "workspace_mode: write outside planning-artifacts/ and worktree/*"

# ALLOWED: write to worktree
Write worktree/hermes-agent/lib/foo.py → OK

# ALLOWED: write to planning
Write planning-artifacts/prd.md → OK
```

## DAG Integration

DAG nodes can target a specific worktree via the `worktree:` field:

```yaml
nodes:
  - id: story-6.3
    worktree: hermes-agent        # Execute in this worktree
    required_capabilities: [python3, git]
  - id: story-6.7
    worktree: hermes-bmad         # Execute in this worktree
    required_capabilities: [python3]
  - id: planning
    # No worktree = workspace root
```

### Concurrency (WI-3)

One worktree → one agent at a time. Per-worktree `threading.Lock` enforces serial execution of nodes targeting the same worktree.

### Validation

- `worktree: nonexistent` → error: "unknown worktree"
- `worktree: X` when `workspace_mode: false` → error

## Capability Check

`lib/capability_check.py` inventories each worktree's available tools:

```python
from plugins.bmad.lib.capability_check import (
    inventory_worktree_capabilities,
    check_dag_worktree_capabilities,
    generate_capability_report,
)

# Detect capabilities
caps = inventory_worktree_capabilities(Path("worktree/repo-a"))
# → ['git', 'node', 'node-project', 'npm', 'pnpm', 'python-project', 'python3', 'pytest']

# Cross-check against DAG
mismatches = check_dag_worktree_capabilities(dag, workspace_root, ws_config)
# → [{'node_id': 'needs-go', 'worktree': 'repo-a', 'missing': ['go']}]
```

## Runtime Mirror (Story 6.8)

When a worktree declares `runtime_mirror:`, writes to that worktree are automatically synced to the runtime location:

```yaml
worktrees:
  - name: hermes-agent
    runtime_mirror: ~/.hermes/hermes-agent  # Auto-sync target
```

**Behavior:**
- Single-file `cp` only (never recursive)
- Skips if content is byte-identical (idempotent)
- Cleans matching `__pycache__/*.pyc` files
- Warns (not errors) if mirror dir doesn't exist
- Logs each mirror event: `[bmad:runtime_mirror] <src> → <dest>`

## Hard Invariants

| # | Invariant | Enforcement |
|---|---|---|
| WI-1 | Opt-in: missing `workspace_mode` = old behavior | `WorkspaceConfig` defaults to `False` |
| WI-2 | Planning at root, never in worktree | Write boundary blocks `planning-artifacts/` inside worktrees |
| WI-3 | One worktree → one agent | Per-worktree `threading.Lock` in `dag_runner.py` |
| WI-4 | No upstream merge | Orchestrator never emits git push/merge/rebase |
| WI-5 | `runtime_mirror` opt-in, single-file only | `post_tool_call` hook does single `shutil.copy2` |
| WI-6 | CLAUDE.md = AGENTS.md symlink | `os.symlink` on Unix, copy on Windows |

## File Reference

| File | Story | Purpose |
|---|---|---|
| `lib/config.py` | 6.2 | Pydantic models + loader |
| `lib/workspace.py` | 6.4 | Write boundary helpers |
| `lib/dag_runner.py` | 6.5 | DAG dispatch + worktree locking |
| `lib/capability_check.py` | 6.6 | Per-worktree capability inventory |
| `scripts/bmad_init.py` | 6.3 | `bmad-init --workspace` scaffolder |
| `hooks/pre_tool_call.py` | 6.4 | Write boundary enforcement |
| `hooks/post_tool_call.py` | 6.8 | Runtime mirror hook |
| `commands/worktree_status.py` | 6.7 | `/bmad:worktree-status` command |
| `templates/AGENTS.md.j2` | 6.1 | Agent orientation template |
| `templates/WORKTREES.md.j2` | 6.1 | Session manifest template |
| `templates/.envrc.example` | 6.1 | direnv stub |
