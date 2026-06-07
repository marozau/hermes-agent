---
name: bmad:product-brief
description: "Analysis-phase skill for creating product briefs — problem statement, target audience, proposed solution, success metrics, competitive landscape. Trigger on: create product brief, product brief, problem statement, discovery."
version: 6.6.0
author: BMAD Community (Hermes port by im)
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [bmad, bmm, product-brief]
    category: bmad
---

# /bmad:product-brief — Product Brief

**Goal:** Produce a 1-2 page executive product brief that frames problem,
audience, solution, success criteria, and competitive context — the
foundation for downstream PRD creation.

**Phase:** 1 - Analysis

---

## Execution

### Step 1: Gather inputs

Before writing, confirm:
- Project config exists at `bmad/config.yaml`
- User has shared the problem they're trying to solve (raw idea OK)
- Any existing market research or competitive analysis docs

Ask 1-2 clarifying questions ONLY if scope is wildly unclear. Otherwise
proceed — the brief is iterative.

### Step 2: Load template

Read `skills/bmad/templates/product-brief.template.md` for the output
structure. **Follow this template exactly**, substituting placeholders with
actual content.

### Step 3: Draft sections

Populate each section with specific, decision-grade content:

| Section | Required | Quality bar |
|---------|----------|-------------|
| Frontmatter | ✓ | project_name, date, author, version, status |
| Problem Statement | ✓ | Clear problem + why now + impact if unsolved |
| Target Audience | ✓ | Primary + secondary users with personas or roles |
| Proposed Solution | ✓ | Key features + differentiation |
| Success Metrics | ✓ | ≥3 measurable criteria with targets |
| Competitive Landscape | ✓ | ≥2 competitors analyzed with differentiation |

### Step 4: Validate

Self-check before output:
- Problem statement names the cost of inaction, not just the pain
- Target audience is segmented (primary vs secondary; persona name + role)
- Solution is concrete enough that engineering can scope it
- Every success metric has a number + timeframe
- Competitors are real (named) and the differentiation is honest

### Step 5: Output

Save to: `planning-artifacts/product-brief-{date}.md`

Return the file path and a 1-paragraph executive summary.

---

## Template Reference

```markdown
---
project_name: '{{project_name}}'
date: '{{date}}'
author: '{{user_name}}'
version: '0.1.0'
status: 'draft'
---

# Product Brief: {{project_name}}

**Date:** {{date}}
**Author:** {{user_name}}
**Status:** Draft v0.1

---

## Executive Summary

[2-3 sentences: what the product is, who it's for, what problem it solves.]

## Problem Statement

[The problem in plain language. Include:
- The pain (what's broken or missing today)
- Why now (timing, market shift, urgency)
- The impact if unsolved (cost, risk, opportunity lost)]

## Target Audience

[Who will use this. Segment into:
- **Primary users**: persona + role + key job-to-be-done
- **Secondary users**: persona + role + supporting use case]

## Proposed Solution

[How the product addresses the problem. Include:
- Key features (3-5 bullets)
- Differentiation vs existing approaches
- High-level user flow]

## Success Metrics

[At least 3 measurable criteria with targets and timeframes:
- Metric 1: target, by when
- Metric 2: target, by when
- Metric 3: target, by when]

## Competitive Landscape

[At least 2 competitors with:
- Competitor name + one-line description
- Their strengths and limits
- How this product differs / wins]

## Risks & Mitigations

[Top 3 risks (technical, market, execution) with mitigation approaches.]

## Out of Scope

[Explicit non-goals for this version. Prevents scope creep.]
```

---

## Anti-patterns

- DO NOT write a wishlist disguised as a problem statement
- DO NOT skip the "why now" framing — without it, the brief reads as
  optional
- DO NOT name a single "everyone" audience — segment primary vs secondary
- DO NOT leave success metrics qualitative ("delightful", "fast")
- DO NOT pretend there are no competitors — every product has alternatives,
  even if "the status quo" is one of them
