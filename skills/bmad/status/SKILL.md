---
name: bmad:status
description: |
  Show BMAD workflow status and suggest next steps. Use when: user asks
  "what's next", "show status", "where are we", or invokes /bmad:status.
  Triggers: "/bmad:status", "what's next", "bmad status", "show progress".
version: 1.0.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [bmad, status, workflow, progress]
    category: bmad
    related_skills: [bmad:init, bmad:sprint-planning, bmad:dev-story]
---

# /bmad:status — Workflow status + next steps

**Goal:** Show the current BMAD workflow state and suggest concrete next actions.

**Your role:** Run the status command, analyze the output, and recommend
what to do next based on the current phase and completion state.

## EXECUTION

### Step 1: Get current status

Run in terminal:
```
hermes bmad-status
```

If the command fails or returns "Not a BMAD project", tell the user to
run `/bmad:init` first.

### Step 2: Analyze and suggest

Based on the status output, suggest concrete next actions:

- **Analysis phase incomplete** → `/bmad:create-prd`, `/bmad:product-brief`,
  `/bmad:research`, or `/bmad:brainstorm`
- **Planning phase incomplete** → `/bmad:epics-stories`, `/bmad:sprint-planning`
- **Solutioning phase incomplete** → `/bmad:create-architecture`,
  `/bmad:solutioning-gate-check`
- **Implementation phase incomplete** → `/bmad:dev-story` for the next
  unblocked story
- **All phases complete** → Suggest running `/bmad:doctor` to check for
  drift or inconsistencies

Also consider the user's question context. If they asked "what's next?",
prioritize the NEXT actionable step. If they asked "show status", present
the full status first, then suggest next steps.

### Step 3: Present

Show the status output, then a "Recommended next steps" section with
1-3 concrete actions, each as a slash command they can run.

## Anti-patterns

- DO NOT invent status data — always run `hermes bmad-status` first
- DO NOT suggest commands that are blocked by incomplete prerequisites
- DO NOT overwhelm with all possible next steps — pick the 1-3 most relevant
