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

---

## DAG Data Model

### Node Types

| Type | Value | Purpose |
|------|-------|---------|
| Task | `task` | A unit of work (code, test, deploy) |
| Gate | `gate` | Blocks downstream until condition met |
| Sub-workflow | `subworkflow` | Loads and executes a sub-DAG |

### Node States

| State | Value | Meaning |
|-------|-------|---------|
| Pending | `pending` | Not yet started |
| Running | `running` | Currently executing |
| Succeeded | `succeeded` | Completed successfully |
| Failed | `failed` | Execution failed |
| Blocked-at-gate | `blocked-at-gate` | Waiting for gate condition |
| Skipped | `skipped` | Skipped (e.g., deadline exceeded with on_failure=skip) |

### Acyclicity Enforcement

The DAG model enforces acyclicity at insertion time using Kahn's algorithm. Adding a node that would create a cycle raises `DAGValidationError`. Self-loops and unknown dependency references are also rejected.

### Persistence

DAG state is persisted to YAML files in `bmad/dag-state/`. Each save increments a `version` field for optimistic locking — concurrent modifications are detected and rejected.

### Deadline Awareness

DAGs and nodes can have optional `deadline` fields (ISO 8601). The system checks deadlines at:
- Workflow start (logs warning if approaching/exceeded)
- Before gate evaluation (exceeded deadline can trigger on_failure behavior)

---

## Gate Configuration

### Gate Types

#### Manual Gate (default)

```yaml
- id: quality-review
  type: gate
  dependencies: [task-tests]
  gate_condition:
    type: manual
    description: "Code review by senior engineer"
    required_approvals: 1
```

Requires explicit `approve`, `reject`, or `override` via CLI.

#### Script Gate

```yaml
- id: coverage-check
  type: gate
  dependencies: [task-tests]
  gate_condition:
    type: script
    script: "test $(cat coverage.txt) -ge 80"
    timeout_seconds: 60
    on_failure: block
```

Runs a shell command. Exit 0 = pass, non-zero = fail. Upstream outputs are injected as `UPSTREAM_OUTPUT_<NODE_ID>` environment variables.

#### Threshold Gate

```yaml
- id: coverage-gate
  type: gate
  dependencies: [task-tests]
  gate_condition:
    type: threshold
    threshold: 80.0
    threshold_field: coverage
    threshold_operator: ">="
```

Extracts a numeric value from upstream task output (JSON) and compares against threshold. Supported operators: `>=`, `<=`, `==`, `!=`, `>`, `<`.

#### Artifact Check Gate

```yaml
- id: artifact-exists
  type: gate
  dependencies: [task-build]
  gate_condition:
    type: artifact_check
    script: "test -f build/output.tar.gz"
```

Verifies artifacts exist using a shell script.

### Gate Behaviors

- `on_failure: block` (default) — blocks downstream, sets state to `blocked-at-gate`
- `on_failure: fail` — marks gate as failed, stops execution
- `on_failure: skip` — skips the gate, continues downstream

### Gate Lifecycle

```
pending → (evaluate) → blocked-at-gate / succeeded
                     ↓
              (manual override) → succeeded
              (manual reject) → failed
              (reset) → pending
```

---

## Workspace CRUD API

### Create

```python
from plugins.bmad.commands.workspace_dag import workspace_create
ws = workspace_create(project_dir, "My Workspace", "description")
```

### List

```python
from plugins.bmad.commands.workspace_dag import workspace_list
workspaces = workspace_list(project_dir)
```

### View

```python
ws = workspace_view(project_dir, "ws-abc123")
```

### Update

```python
ws = workspace_update(project_dir, "ws-abc123", name="New Name", description="updated")
```

### Archive

```python
ws = workspace_archive(project_dir, "ws-abc123")  # status → "archived"
```

### Delete

```python
workspace_delete(project_dir, "ws-abc123")  # removes state file
```

---

## DAG API

### Create

```python
dag = dag_create(project_dir, workspace_id, "Sprint 1", "description", deadline="2026-06-30")
```

### Add Node

```python
dag = dag_add_node(project_dir, dag.id, "task-code", node_type="task")
dag = dag_add_node(project_dir, dag.id, "gate-review", node_type="gate",
                   dependencies=["task-code"],
                   gate_condition={"type": "manual"})
dag = dag_add_node(project_dir, dag.id, "task-deploy", node_type="task",
                   dependencies=["gate-review"])
```

### Execute

```python
dag = dag_execute(project_dir, dag.id)          # full execution
dag = dag_execute(project_dir, dag.id, dry_run=True)  # validate only
```

### Status

```python
status = dag_status(project_dir, dag.id)
# → {'dag_id': ..., 'total_nodes': 3, 'states': {...}, 'all_succeeded': True, ...}
```

### List

```python
dags = dag_list(project_dir)                        # all DAGs
dags = dag_list(project_dir, workspace_id="ws-123") # filtered
```

---

## Gate API

```python
dag = gate_approve(project_dir, dag.id, "gate-review", "alice", "LGTM")
dag = gate_reject(project_dir, dag.id, "gate-review", "bob", "quality too low")
dag = gate_override(project_dir, dag.id, "gate-review", "admin", "emergency")
dag = gate_reset(project_dir, dag.id, "gate-review")  # back to pending
```

---

## DAG Visualization

```python
from plugins.bmad.commands.workspace_dag import dag_visualize
print(dag_visualize(dag))
```

Output:

```
DAG: Sprint 1
Deadline: 2026-06-30

  ○ task-code (task)
  ◆ gate-review (gate) [hermes-agent] ← task-code — Code review
    └─ gate: approved by alice
  ● task-deploy (task) ← gate-review — Deploy to staging

Summary: 2 succeeded, 1 blocked-at-gate
```

State icons: ○=pending, ◐=running, ●=succeeded, ✗=failed, ◆=blocked-at-gate, ◌=skipped

---

## Worktree Dispatch

```python
from plugins.bmad.commands.workspace_dag import dag_add_node

# Node targets a specific worktree
dag = dag_add_node(project_dir, dag.id, "story-6.3",
                   node_type="task",
                   worktree="hermes-agent",
                   description="Implement in hermes-agent repo")
```

When a node declares `worktree:`, the execution engine:
1. Resolves the worktree path from `bmad/config.yaml`
2. Acquires a per-worktree `threading.Lock` (WI-3: one agent per worktree)
3. Sets `cwd` to the worktree directory
4. Releases the lock after execution

---

## CLI Commands

### `bmad-init --workspace`

```bash
hermes bmad-init --workspace \
  --worktree NAME:UPSTREAM:BRANCH [...] \
  [--project-name NAME] \
  [--envrc]
```

### `/bmad:worktree-status`

```bash
/bmad:worktree-status                           # read-only display
/bmad:worktree-status --claim hermes-agent --task "Story 6.3"
/bmad:worktree-status --release hermes-agent
/bmad:worktree-status --claim hermes-agent --task "Urgent fix" --force
```

---

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

---

## Capability Check

```python
from plugins.bmad.lib.capability_check import (
    inventory_worktree_capabilities,
    check_dag_worktree_capabilities,
)

caps = inventory_worktree_capabilities(Path("worktree/repo-a"))
# → ['git', 'node', 'python3', 'pytest']

mismatches = check_dag_worktree_capabilities(dag, workspace_root, ws_config)
# → [{'node_id': 'needs-go', 'worktree': 'repo-a', 'missing': ['go']}]
```

---

## Runtime Mirror (opt-in)

```yaml
worktrees:
  - name: hermes-agent
    runtime_mirror: ~/.hermes/hermes-agent  # auto-sync target
```

- Single-file `cp` only (never recursive)
- Skips if content is byte-identical
- Cleans matching `__pycache__/*.pyc` files

---

## Concurrent Safety

- **Optimistic locking**: Each DAG save increments a `version` field. Stale saves are rejected with `DAGValidationError("Concurrent modification detected")`.
- **Per-worktree locks**: `threading.Lock` per worktree name prevents two agents from running in the same checkout simultaneously.
- **Atomic writes**: DAG state is written to a temp file and atomically renamed via `os.replace()`.

---

## Hard Invariants

| # | Invariant | Enforcement |
|---|---|---|
| WI-1 | Opt-in: missing `workspace_mode` = old behavior | `WorkspaceConfig` defaults to `False` |
| WI-2 | Planning at root, never in worktree | Write boundary blocks `planning-artifacts/` inside worktrees |
| WI-3 | One worktree → one agent | Per-worktree `threading.Lock` in `dag_runner.py` |
| WI-4 | No upstream merge | Orchestrator never emits git push/merge/rebase |
| WI-5 | `runtime_mirror` opt-in, single-file only | `post_tool_call` hook does single `shutil.copy2` |
| WI-6 | CLAUDE.md = AGENTS.md symlink | `os.symlink` on Unix, copy on Windows |

---

## Worked Example

Complete lifecycle: workspace → DAG → gate blocked → gate approved → all succeed.

```python
from pathlib import Path
from plugins.bmad.commands.workspace_dag import *

project = Path("/tmp/my-workspace")

# 1. Create workspace
ws = workspace_create(project, "Feature X", "cross-repo feature")

# 2. Create DAG
dag = dag_create(project, ws.id, "Sprint 1", deadline="2026-06-30")

# 3. Add nodes
dag = dag_add_node(project, dag.id, "implement", node_type="task", worktree="hermes-agent")
dag = dag_add_node(project, dag.id, "test", node_type="task", dependencies=["implement"])
dag = dag_add_node(project, dag.id, "review", node_type="gate", dependencies=["test"],
                   gate_condition={"type": "manual", "description": "Code review"})
dag = dag_add_node(project, dag.id, "deploy", node_type="task", dependencies=["review"])

# 4. Execute — gate blocks downstream
dag = dag_execute(project, dag.id)
print(dag_status(project, dag.id))
# → {'any_blocked': True, 'all_succeeded': False, ...}

# 5. Approve gate
dag = gate_approve(project, dag.id, "review", "alice", "LGTM")

# 6. Re-execute — all succeed
dag = dag_execute(project, dag.id)
print(dag_status(project, dag.id))
# → {'all_succeeded': True, 'any_blocked': False, ...}

# 7. Visualize
print(dag_visualize(dag))
```

---

## File Reference

| File | Story | Purpose |
|---|---|---|
| `lib/config.py` | 6.2 | Pydantic models + loader |
| `lib/workspace.py` | 6.4 | Write boundary helpers |
| `lib/dag_model.py` | 6.2+ | DAG data model, acyclicity, persistence, deadlines |
| `lib/dag_engine.py` | 6.5+ | DAG execution engine with gate hold/release |
| `lib/dag_runner.py` | 6.5 | Worktree dispatch + locking |
| `lib/gate_evaluator.py` | 6.2+ | Gate evaluation (manual/script/threshold/artifact) |
| `lib/capability_check.py` | 6.6 | Per-worktree capability inventory |
| `scripts/bmad_init.py` | 6.3 | `bmad-init --workspace` scaffolder |
| `hooks/pre_tool_call.py` | 6.4 | Write boundary enforcement |
| `hooks/post_tool_call.py` | 6.8 | Runtime mirror hook |
| `commands/worktree_status.py` | 6.7 | `/bmad:worktree-status` command |
| `commands/workspace_dag.py` | 6.2+ | Workspace/DAG/gate CRUD + visualization |
| `templates/AGENTS.md.j2` | 6.1 | Agent orientation template |
| `templates/WORKTREES.md.j2` | 6.1 | Session manifest template |
| `templates/.envrc.example` | 6.1 | direnv stub |
