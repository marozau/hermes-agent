---
name: bmad:epics-stories
description: |
  Solutioning-phase skill for decomposing architecture into epics and user
  stories. Trigger on: create epics, user stories, story mapping, decompose,
  backlog.
version: 6.6.0
author: BMAD Community (Hermes port by im)
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [bmad, bmm, solutioning, epics-stories]
    category: bmad
---

# /bmad:epics-stories — Epic & Story Decomposition

**Goal:** Break architecture into deliverable epics and user stories with
acceptance criteria.

**Phase:** 3 - Solutioning

---

## Execution

### Step 1: Gather inputs

Required:
- Architecture at `planning-artifacts/architecture-{project}.md`
- PRD at `planning-artifacts/prd-{project}.md`

If Architecture is missing, run `/bmad:create-architecture` first.

### Step 2: Identify epics

Group stories into epics by functional area or component:
- Each epic delivers user-visible value
- Each epic fits in 1-2 sprints
- Epic count: 3-7 for most projects

### Step 3: Write user stories

For each epic, write stories in format:
> **As a** [role], **I want** [capability], **so that** [benefit]

Quality checks:
- INVEST: Independent, Negotiable, Valuable, Estimable, Small, Testable
- Every story has ≥2 acceptance criteria
- Stories are small enough for 1-3 days of work

### Step 4: Prioritize

Rank by: Business value ÷ Effort (highest first)
Tag each story: P0 (must), P1 (should), P2 (could)

### Step 5: Output

Save to: `planning-artifacts/epics-stories-{project}.md`

---

## Execution Steps

Follow the BMAD step-file workflow. Each step is a separate file in
`skills/bmad/bmm/epics-stories/steps/`:

1. Read `step-01-init.md` — initialize the epic decomposition
2. Continue through remaining step files
3. After each step, save incremental progress to `planning-artifacts/epics-stories-*.md`
4. Use resume step files to continue after interruptions

Use `skills/bmad/templates/epics-stories.template.md` **byte-identical from BMAD v6.2.2.0 upstream**
as the structural template. Do NOT edit the template structure — substitute
placeholders only.

```bash
cat skills/bmad/templates/epics-stories.template.md
```

The upstream template is 61 lines and includes epic structure with business
value, priority, user stories (As a/I want/so that), acceptance criteria
(Given/When/Then), estimates, and revision history.

---

## Anti-patterns

- DO NOT write stories without acceptance criteria — they're not testable
- DO NOT make stories too large (≥3 days) — break them down
- DO NOT skip prioritization — everything P0 means nothing is P0
- DO NOT write technical tasks as user stories — stories deliver user value
