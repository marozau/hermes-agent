#!/usr/bin/env python3
"""
Orchestration script prompt fragment for Hermes Agent.

Inject this into the system prompt to teach the LLM how to write
multi-phase workflow orchestration scripts that run inside execute_code.

The LLM writes a Python script that orchestrates subagents via
delegate_task, manages phase transitions with script-level variables,
handles errors and timeouts, and returns only a synthesized summary
to the parent agent — never raw subagent results.

Key insight (from Claude Code's plan-in-code pattern):
Orchestration state lives in Python variables OUTSIDE the LLM's context
window. The script is the plan; the variables are the memory.
"""

WORKFLOW_ORCHESTRATION_PROMPT = """
## Workflow Orchestration Scripts

You have the ability to orchestrate complex multi-phase workflows by
generating Python scripts that run inside `execute_code`. These scripts
coordinate subagents via `delegate_task` to implement patterns like
research-pipeline, fan-out/fan-in, adversarial verification, and more.

### When to use orchestration scripts

Write an orchestration script when:
- The task spans 3+ phases (research → draft → review → final)
- You need parallel subagents with result merging
- You need adversarial verification (two agents cross-check each other)
- The workflow requires conditional branching between phases
- You want to keep intermediate results out of the parent context window

Do NOT use orchestration scripts for:
- Simple one-off tasks (use delegate_task directly)
- Interactive tasks requiring user input
- Single tool calls

### Available tools in the sandbox

Within execute_code, import tools from `hermes_tools`:

```python
from hermes_tools import (
    delegate_task,     # spawn subagents in phases
    checkpoint_save,   # save workflow checkpoint
    checkpoint_load,   # load workflow checkpoints
    terminal,          # shell commands
    read_file,         # read files
    write_file,        # write files
    search_files,      # search code
    web_search,        # web search
    web_extract,       # extract web content
    patch,             # targeted file edits
    json_parse,        # safe JSON parsing
    shell_quote,       # safe shell escaping
    retry,             # retry with backoff
)
```

### delegate_task in the sandbox

```python
# Single task
result = delegate_task(
    goal="Research the Rust async ecosystem",
    context="Focus on production-ready runtimes and their trade-offs.",
    toolsets=["terminal", "web", "file"],
)

# Batch (parallel) — up to 3 concurrent by default
results = delegate_task(tasks=[
    {"goal": "Research tokio", "context": "Focus on performance characteristics."},
    {"goal": "Research async-std", "context": "Focus on API design."},
    {"goal": "Research smol", "context": "Focus on minimalism."},
])
```

Each result is a dict with `summary` and optional metadata. The subagent
summary is compact — only the final answer, not intermediate tool calls.

### Canonical Patterns

#### Pattern 1: Fan-Out / Fan-In (parallel research)
```python
# Phase 1: Fan-out — parallel research
results = delegate_task(tasks=[
    {"goal": "Research topic A", "toolsets": ["web"]},
    {"goal": "Research topic B", "toolsets": ["web"]},
    {"goal": "Research topic C", "toolsets": ["web"]},
])

# Phase 2: Fan-in — synthesize
synthesis = delegate_task(
    goal="Synthesize the following research into a coherent report",
    context=json.dumps([r["summary"] for r in results]),
    toolsets=["file"],
)
```

#### Pattern 2: Pipeline with Gates (sequential phases)
```python
# Phase 1: Research
research = delegate_task(
    goal="Research the topic thoroughly",
    toolsets=["web", "terminal"],
)
if "error" in research:
    print(json.dumps({"status": "failed", "phase": "research", "error": research["error"]}))
    exit(1)

# Phase 2: Draft (gated on research success)
draft = delegate_task(
    goal="Write a draft based on the research",
    context=research["summary"],
    toolsets=["file"],
)
if "error" in draft:
    print(json.dumps({"status": "failed", "phase": "draft", "error": draft["error"]}))
    exit(1)

# Phase 3: Review (gated on draft success)
review = delegate_task(
    goal="Review the draft for accuracy and clarity",
    context=draft["summary"],
    toolsets=["file", "web"],
)

# Synthesize final result
print(json.dumps({
    "status": "complete",
    "research_summary": research["summary"],
    "draft": draft["summary"],
    "review_feedback": review["summary"],
}))
```

#### Pattern 3: Adversarial Verification (parameterized)

The `adversarial_review()` helper spawns N reviewers in parallel against
subagent results. Configure per-phase with `review_agents: N`:

```python
# Phase 1: Fan-out — N parallel workers produce results
results = delegate_task(tasks=[
    {"goal": "Research topic A", "toolsets": ["web"]},
    {"goal": "Research topic B", "toolsets": ["web"]},
    {"goal": "Research topic C", "toolsets": ["web"]},
])

# Phase 2: Adversarial review — spawn N reviewers in parallel.
# Set review_agents higher for critical tasks, 0 to skip review.
review_agents = 2  # per-phase config: 0=off, 1-3 recommended
verdict = adversarial_review(
    results=results,
    task_spec="Research topics A, B, C with factual accuracy. "
              "All claims must be verifiable. No speculation.",
    num_reviewers=review_agents,
    toolsets=["web", "terminal"],
)

# Phase 3: Synthesize — only results that survived review
if verdict["defects"]:
    print(json.dumps({
        "status": "reviewed",
        "warnings": len(verdict["defects"]),
        "defects": verdict["defects"],
    }))
# Pass only clean results to synthesis
clean_summaries = verdict["passed"]
synthesis = delegate_task(
    goal="Synthesize the verified research into a coherent report",
    context=json.dumps(clean_summaries),
    toolsets=["file"],
)

print(json.dumps({
    "status": "complete",
    "result": synthesis["summary"],
    "review_stats": {
        "total_results": len(results),
        "passed": len(verdict["passed"]),
        "defects_found": len(verdict["defects"]),
        "reviewers": review_agents,
    },
}))
```

**How adversarial_review works:** Each reviewer receives the original task
specification AND all primary subagent results. They are instructed to
identify contradictions, factual errors, and missing information. A result
is excluded if ANY reviewer flags it as defective. The returned dict has
`passed` (clean summaries), `defects` (list of findings), and `all_reviews`
(raw reviewer output for transparency).

**Per-phase configuration:** Set `review_agents: 0` to skip review for a
phase (faster, less reliable). Set `review_agents: 2` or higher for
critical phases where accuracy matters. The reviewers run in parallel
so N reviewers ≈ 1 reviewer wall-clock time.

### Error Handling & Resilience

Always handle errors at each phase. Don't let one failed subagent derail
the entire workflow.

```python
def safe_delegate(goal, context="", toolsets=None, max_retries=2):
    \"\"\"Call delegate_task with retry on transient failures.\"\"\"
    for attempt in range(max_retries + 1):
        try:
            result = delegate_task(goal=goal, context=context, toolsets=toolsets)
            if isinstance(result, dict) and "summary" in result:
                return result
            if isinstance(result, list) and result and "summary" in result[0]:
                return result
            if attempt < max_retries:
                # Non-summary response — retry
                continue
            return {"error": f"No summary after {max_retries+1} attempts"}
        except Exception as e:
            if attempt < max_retries:
                continue
            return {"error": str(e)}
    return {"error": "max_retries exhausted"}
```

### Timeout Management

The execute_code sandbox has a 5-minute timeout (configurable via
`code_execution.timeout`). For long workflows, use the built-in SQLite
checkpointing system — no need for manual file I/O.

### Checkpointing & Resumption

The sandbox provides `checkpoint_save` and `checkpoint_load` that persist
to the same SQLite database as the session store. For automatic
checkpointing, pass `workflow_id` and `phase` to `delegate_task` — the
result is cached on completion and marked 'completed' in the DB.

**Generating a workflow_id:** Use a unique string that identifies this
workflow run. On resumption, the same workflow_id reconnects to the
cached results. Good: `f"{task_id}-{uuid4().hex[:8]}"` so the id is
deterministic across retries.

```python
import json, os, uuid as _uuid

WORKFLOW_ID = os.environ.get("HERMES_KANBAN_TASK", "") + "-" + _uuid.uuid4().hex[:8]

# On resume: check what's already done
cached = checkpoint_load(WORKFLOW_ID)
completed = {p for p, v in cached.items() if v.get("status") == "completed"}

if "research" not in completed:
    result = delegate_task(
        goal="Research the topic thoroughly",
        toolsets=["web", "terminal"],
        workflow_id=WORKFLOW_ID,
        phase="research",
    )
    # result auto-checkpointed as 'completed' on success.
    # On failure, marked 'failed' — resume will re-run it.

# For non-delegate_task state, use explicit checkpoint_save:
checkpoint_save(WORKFLOW_ID, "setup", status="completed",
                result_cache=json.dumps({"config_loaded": True}))
```

**Resumption semantics:** `checkpoint_load(workflow_id)` returns
`{phase: {status, result_cache, timestamp, agent_id}}`. Phases with
`status == "completed"` can be skipped. Phases with any other status
(`pending`, `failed`, `running`) should be re-executed. After the
workflow completes, call `checkpoint_save(workflow_id, phase, status="completed")`
for the final phase to clean up, though orphaned checkpoints are harmless.

### Key Rules

1. **State lives in variables, not context.** Use Python variables to track
   phase results, not the LLM's context window.
2. **Print only JSON to stdout.** The parent agent reads stdout as the final
   result. Use a single `print(json.dumps({...}))` at the end.
3. **Summaries only.** Don't pass large subagent outputs to the parent —
   synthesize into a compact summary.
4. **Gate on results.** Check each phase's output before proceeding. Exit
   early with a clear error if a phase fails.
5. **Phases > parallelism.** Prefer clear phase structure over excessive
   parallelism. Max 3 concurrent subagents.
6. **Handle both single and batch results.** `delegate_task` with `goal`
   returns a single dict; with `tasks` returns a list of dicts.
7. **Checkpoint for resumption.** Pass `workflow_id` and `phase` to every
   `delegate_task` call in multi-phase workflows. On script start, call
   `checkpoint_load(workflow_id)` to skip already-completed phases.
   This makes workflows resilient to interruptions without duplicate work.
"""
