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

## Execution Steps

Follow the BMAD step-file workflow. Each step is a separate file in
`skills/bmad/bmm/create-architecture/steps/`:

1. Read `step-01-init.md` — initialize the architecture document
2. Continue through `step-02-context.md` ... `step-08-complete.md`
3. After each step, save incremental progress to `planning-artifacts/architecture-*.md`
4. Use `step-01b-continue.md` to resume after interruptions

Use `skills/bmad/templates/architecture.md` **byte-identical from BMAD v6.2.2.0 upstream**
as the structural template. Do NOT edit the template structure — substitute
placeholders only.

```bash
cat skills/bmad/templates/architecture.md
```

The upstream template is 289 lines and includes all sections from Architectural
Drivers through Appendix C: Cost Estimation.

---

## Anti-patterns

- DO NOT introduce requirements not in the PRD — architecture serves the spec
- DO NOT skip security — every system has an attack surface
- DO NOT use "later" or "TBD" for critical decisions — make the call or flag it
- DO NOT optimize prematurely — solve for current scale, note future thresholds
