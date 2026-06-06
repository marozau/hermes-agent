# BMAD Automation Gaps

> Reference: what's documented vs what actually runs in the BMAD Hermes profile.
> Kanban task `t_f016e9f3` tracks the implementation.

## Current State: Skills-Only

Hermes BMAD is built as 9 SKILL.md files under `skills/bmad/`.
These are prompt documentation — they instruct the LLM how to respond when a
user requests BMAD work. No autonomous processes, no persistent state, no
automated phase transitions.

## What Real BMAD Does (Target State)

| Feature | Real BMAD | Current Hermes |
|---------|-----------|----------------|
| Sub-agents | Spawns specialists per phase (analyst, PM, architect, developer) | Documented in SKILL.md as "you could use delegate_task" — never auto-spawned |
| YAML tracking | `bmm-workflow-status.yaml` auto-read/written on phase transitions | Templates exist as files, no auto-rw between sessions |
| Phase gates | Validates gate criteria before advancing | Described as "check before moving" — no enforcement |
| Cross-session | Works across chat sessions | No persistence — status lost between sessions |
| Auto-routing | Detects phase completion, routes to next specialist | Requires user to say the right keyword |

## Sub-Agent Gap

The orchestrator describes using `delegate_task` to spawn sub-agents for
parallel work (e.g., 3 sub-agents for project initialization). This works
within a single session but the sub-agents:
- Are **ephemeral** — no persistent session, no resume across turns
- Share the **same profile** — can't delegate to `business-analyst` profile
- Have no **cross-profile ACP** — the ACP adapter exists (`acp_adapter/`)
  but only supports Copilot CLI subprocesses, not Hermes-to-Hermes

**To close this:** The ACP adapter needs `--profile` support so profile A can
delegate to profile B with session persistence. ~2-3 days of dev work.

## YAML Tracking Gap

`bmm-workflow-status.template.yaml` and `sprint-status.template.yaml` exist
as templates at `skills/bmad/templates/`. But:
- No cron job reads/writes them between sessions
- No Prefect flow tracks state transitions
- The orchestrator skill documents how to read/write them via file tools,
  but only when the user explicitly says "check workflow status"

## Phase Gate Gap

Gate criteria are documented (e.g., "PRD reviewed before Solutioning") but
never validated automatically. No cron job or Prefect task checks:
- Is the product brief complete before recommending PRD?
- Is the PRD reviewed before architecture design?
- Are all stories done before closing a phase?

## Recommended Approaches

### B — Hermes Cron Job
- A recurring cron job checks BMAD workflow status and reports progress
- Works across sessions
- Cannot spawn sub-agents or run complex orchestration

### C — Prefect Flow (Preferred — user already runs Prefect)
- Prefect runs at localhost:4200 with 14 existing deployments
- Flow: `bmad-orchestrator` with tasks for each phase
- State stored in Prefect task results + YAML files
- Gate validation as Prefect task before phase transitions
- Telegram alerts on phase advancement
- Follow existing layout: `src/flows/`, `src/collectors/`

### D — ACP Cross-Profile Delegation
- Build Hermes-to-Hermes ACP server
- Profile A (orchestrator) delegates to profile B (specialist) with context
- ~2-3 days dev on `acp_adapter/`
- Most flexible long-term

## Prefect Integration Notes

- PREFECT_API_URL must be set explicitly
- `prefect-flows/` has no `.git/` — needs reinit
- Profile parameter available for multi-profile flows
- Existing patterns: mem0-sync, vault-rl, soul-guardian all as Prefect flows
- Layout: `src/flows/bmad/`, `src/collectors/bmad/`
