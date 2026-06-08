# Parallel Document Composition Pattern

Reference for composing large BMAD planning documents via parallel sub-agents.
Derived from hermes-swarm architecture composition (2026-06-07): 3,490-line
architecture document written by 3 parallel agents and merged.

---

## When to Use

Use this pattern when a single planning document exceeds ~1,500 lines or
spans 6+ major sections. Serial composition risks context-window overflow
and inconsistent cross-references between early and late sections.

## Pattern

### Step 1: Define section boundaries

Break the document into 2–4 sections of roughly equal size. Each section
should be self-contained enough that an agent can write it given the PRD
and a section brief.

Example (architecture document):
- **Sections 1–3:** Drivers, System Overview, Component Model
- **Sections 4–5:** Data Model, API Design
- **Sections 6–8:** Security, Deployment, ADRs

### Step 2: Prepare shared context

Every parallel agent receives:
- The FULL PRD (so cross-references are consistent)
- The section-specific brief (which sections to write)
- The output file path (so agents write directly to disk)

### Step 3: Delegate in parallel

Use `delegate_task` with `tasks` array (batch mode) to run 2–4 agents
concurrently. Each agent writes its section(s) to a temporary file.

### Step 4: Merge and deduplicate

After all agents complete:
1. Concatenate section files in order
2. Remove duplicate frontmatter (keep only the first YAML block)
3. Verify section numbering is sequential
4. Check cross-references between sections

### Step 5: Run critics on merged document

The merged document will have inconsistencies. Run critics on the merged
document to surface cross-section gaps, contradictions, and undefined
references.

---

## Anti-Patterns

- **DO NOT** give each agent only its own section of the PRD — they need
  the full PRD to maintain consistent terminology and cross-references.
- **DO NOT** merge without deduplicating frontmatter.
- **DO NOT** skip the critics step after merging.

---

## Session Reference

- **Project:** hermes-swarm
- **Document:** architecture-hermes-swarm-2026-06-07.md (3,490 lines)
- **Agents:** 3 parallel writers + 6 critics in 2 batches
- **Result:** 48 findings, 5 P1 fixes applied
