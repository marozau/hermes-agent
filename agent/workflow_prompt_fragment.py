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
    delegate_task,  # spawn subagents in phases
    terminal,       # shell commands
    read_file,      # read files
    write_file,     # write files
    search_files,   # search code
    web_search,     # web search
    web_extract,    # extract web content
    patch,          # targeted file edits
    json_parse,     # safe JSON parsing
    shell_quote,    # safe shell escaping
    retry,          # retry with backoff
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

#### Pattern 3: Adversarial Verification
```python
# Phase 1: Generate proposal
proposal = delegate_task(
    goal="Create a technical proposal for the architecture change",
    toolsets=["file", "terminal"],
)

# Phase 2: Adversarial review (explicitly told to find flaws)
critique = delegate_task(
    goal="You are an adversarial reviewer. Find every flaw, edge case, "
         "and missing requirement in this proposal. Be ruthlessly critical.",
    context=proposal["summary"],
    toolsets=["terminal", "web"],
)

# Phase 3: Defend or revise
if "no issues found" in critique["summary"].lower():
    final = proposal["summary"]
else:
    final = delegate_task(
        goal="Revise the proposal to address all critique points",
        context=f"PROPOSAL:\n{proposal['summary']}\n\nCRITIQUE:\n{critique['summary']}",
        toolsets=["file"],
    )["summary"]

print(json.dumps({"status": "complete", "result": final}))
```

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
`code_execution.timeout`). For long workflows, checkpoint intermediate
results to files:

```python
import json, os

CHECKPOINT_FILE = "/tmp/workflow_checkpoint.json"

# Resume from checkpoint if exists
completed_phases = set()
if os.path.exists(CHECKPOINT_FILE):
    with open(CHECKPOINT_FILE) as f:
        completed_phases = set(json.load(f).get("completed", []))

if "research" not in completed_phases:
    research = safe_delegate("Research the topic")
    completed_phases.add("research")
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump({"completed": list(completed_phases)}, f)

# ... continue with other phases
```

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
"""
