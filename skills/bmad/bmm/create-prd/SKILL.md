---
name: bmad:create-prd
description: |
  Planning-phase skill for creating Product Requirements Documents (PRDs).
  Functional requirements, non-functional requirements, epics, user stories,
  prioritization. Trigger on: create PRD, PRD, product requirements document,
  requirements.
version: 6.6.0
author: BMAD Community (Hermes port by im)
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [bmad, bmm, create-prd]
    category: bmad
---

# /bmad:create-prd — Product Requirements Document

**Goal:** Create a comprehensive PRD that serves as the source of truth for
what will be built.

**Phase:** 2 - Planning

---

## Execution

### Step 1: Gather inputs

Before writing, confirm:
- Product Brief exists at `planning-artifacts/product-brief.md`
- Project config exists at `bmad/config.yaml`
- User has clarified scope boundaries

If Product Brief is missing, run `/bmad:product-brief` first.

### Step 2: Load template

Read `skills/bmad/templates/prd.md` for output structure.
**Follow this template exactly**, substituting placeholders with actual content.

### Step 3: Write PRD sections

Populate each section with specific, testable content:

| Section | Required | Quality bar |
|---------|----------|-------------|
| Executive Summary | ✓ | ≥3 sentences: what, why, scope |
| Product Goals | ✓ | Business objectives + user goals |
| Functional Requirements | ✓ | ≥5 requirements with IDs |
| Non-Functional Requirements | ✓ | Performance, security, reliability |
| Success Metrics | ✓ | ≥3 measurable criteria |
| Out of Scope | ✓ | Explicit exclusions |
| Open Questions | ✓ | Risks and decisions pending |

### Step 4: Validate

Self-check before output:
- Every requirement has a unique ID (FR-1, FR-2, ...)
- Every requirement is testable (verifiable pass/fail)
- No vague words: "fast", "user-friendly", "robust"
- NFRs have quantitative targets where possible

### Step 5: Output

Save to: `planning-artifacts/prd-{project_name}.md`

Return the file path and a 1-paragraph summary of what the PRD covers.

---

## Template Reference

```markdown
# Product Requirements Document: {{project_name}}

**Date:** {{date}}
**Author:** {{user_name}}
**Version:** 1.0
**Status:** Draft

---

## Executive Summary

[What, why, scope — 3+ sentences]

## Product Goals

### Business Objectives

[Measurable business outcomes]

### Success Metrics

[KPIs with targets]

## Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-1 | [Specific, testable requirement] | P0 |

## Non-Functional Requirements

| Category | Requirement | Target |
|----------|-------------|--------|
| Performance | [Quantitative target] | [Value] |

## Out of Scope

[Explicit exclusions]

## Open Questions

[Decisions pending]
```

---

## Anti-patterns

- DO NOT write requirements that are opinions ("should be fast" → "p95 < 200ms")
- DO NOT skip the Out of Scope section — scope creep kills projects
- DO NOT duplicate product brief content — link to it, don't repeat it
- DO NOT leave success metrics vague — every metric needs a number
