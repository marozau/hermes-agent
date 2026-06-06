---
name: bmad:create-architecture
description: |
  Solutioning-phase skill for system architecture design — component model,
  data model, API design, security model, deployment architecture.
  Trigger on: create architecture, architecture design, system design.
version: 6.6.0
author: BMAD Community (Hermes port by im)
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [bmad, bmm, solutioning, create-architecture]
    category: bmad
---

# /bmad:create-architecture — System Architecture

**Goal:** Produce a technical blueprint that addresses all PRD requirements.

**Phase:** 3 - Solutioning

---

## Execution

### Step 1: Gather inputs

Required inputs:
- PRD at `planning-artifacts/prd-{project}.md`
- Product Brief at `planning-artifacts/product-brief.md`

If PRD is missing, run `/bmad:create-prd` first.

### Step 2: Load template

Read `skills/bmad/templates/architecture.md` for output structure.
**Follow this template exactly**.

### Step 3: Design and document

| Section | Required | Quality bar |
|---------|----------|-------------|
| Architectural Drivers | ✓ | ≥3 drivers from PRD/NFRs |
| System Overview | ✓ | High-level diagram or component list |
| Component Model | ✓ | ≥3 components with responsibilities |
| Data Model | ✓ | Entities, relationships, storage |
| API Design | ✓ | Key endpoints with methods/payloads |
| Security Model | ✓ | Auth, authorization, data protection |
| Deployment Architecture | ✓ | Environments, scaling, failover |

### Step 4: Validate against PRD

Cross-check:
- Every functional requirement maps to a component or API
- Every NFR has a corresponding architectural decision
- No new requirements introduced (architecture serves PRD, doesn't expand it)

### Step 5: Output

Save to: `planning-artifacts/architecture-{project}.md`

---

## Template Reference

```markdown
# System Architecture: {{project_name}}

**Date:** {{date}}
**Architect:** {{user_name}}
**Version:** 1.0
**Status:** Draft

---

## Architectural Drivers

[Requirements that heavily influence decisions]

## System Overview

[High-level description with diagram]

## Component Model

| Component | Responsibility | Interface |
|-----------|---------------|-----------|
| [Name] | [What it does] | [How others talk to it] |

## Data Model

[Entities, relationships, storage strategy]

## API Design

| Endpoint | Method | Purpose |
|----------|--------|---------|
| /api/v1/... | GET/POST | [What it does] |

## Security Model

[Auth, authorization, data protection]

## Deployment Architecture

[Environments, scaling, failover]
```

---

## Anti-patterns

- DO NOT introduce requirements not in the PRD — architecture serves the spec
- DO NOT skip security — every system has an attack surface
- DO NOT use "later" or "TBD" for critical decisions — make the call or flag it
- DO NOT optimize prematurely — solve for current scale, note future thresholds
