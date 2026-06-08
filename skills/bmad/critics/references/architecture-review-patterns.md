# Architecture Document — Common Critic Findings

Reference for `/critics` when reviewing BMAD architecture documents. Based on
hermes-swarm architecture review (2026-06-07): 6 critics, 48 findings, 5 P1
bugs in a 3,400-line document.

---

## High-Yield Critic Lenses for Architecture

Not all 8 critics are equally valuable for architecture docs. Prioritize:

1. **Logician (TS-4)** — finds dependency inconsistencies, data-flow gaps,
   missing error-handling paths, unstated assumptions in pseudo-code
2. **Auditor (TS-2)** — cross-references PRD/brief against architecture;
   catches dropped criteria, missing NFR coverage, omitted components
3. **Falsifier (TS-5)** — tests performance targets, reliability claims,
   security assertions; surfaces unfalsifiable claims
4. **Root-Cause Hunter (TS-8)** — distinguishes symptom-fixes from root-cause
   fixes; flags workaround patterns in degradation/migration strategies

Skeptic and Calibrator are medium-value; Stylist and FP Auditor are low-value
for architecture docs (save them for research docs and decision memos).

---

## Recurring Bug Patterns (Architecture Pseudo-Code)

### 1. Variable Aliasing in `condition()` Predicates

**Example:** Dependency-unblocking loop looks up `a.assignmentId` instead of
`spec.assignmentId`, causing the condition to always evaluate false.

**Mitigation:** Auditor should trace every variable reference in pseudo-code
back to its binding site. If a variable is used in a closure/filter/find,
verify it refers to the correct iteration variable.

### 2. Computed-But-Unused Routing Criteria

**Example:** `hasRetryNeed` is computed via regex but never appears in the
`if` condition. A product-brief criterion is silently dropped.

**Mitigation:** Auditor should list every boolean computed in a decision
function and assert it appears in the final boolean expression. If a criterion
from the brief is missing, flag it explicitly.

### 3. Undefined Helper Functions

**Example:** `waitForFreshCheckpoint()` is referenced in Flow A step 11 and in
the `dispatchWorker` Activity, but never defined in the architecture. No polling
interval, timeout, retry strategy, or terminal behavior is specified.

**Mitigation:** Skeptic should ask "How would I verify this?" for every
function call. If the function is not defined in the document, flag it as
unverified.

### 4. Missing Terminal-State Hooks

**Example:** Workflow reaches `FAILED` state but no Activity notifies the
control plane. The mission silently fails in Temporal with no external signal.

**Mitigation:** Logician should trace every terminal state (COMPLETED, FAILED,
TIMED_OUT, CANCELED) to an explicit control-plane notification or persistence
step. If the trace ends in a vacuum, flag the gap.

### 5. Ghost Components

**Example:** "The control plane's exhaustion handler processes failures" —
referenced in prose but no file, interface, trigger mechanism, or integration
point is defined.

**Mitigation:** Auditor should build a component inventory from the document
and verify every named component has: (a) a file path, (b) an interface/
signature, (c) a trigger/invocation mechanism, (d) error handling.

---

## Recurring Design Smells

| Smell | Critic | Typical Finding |
|---|---|---|
| Dual authority | Logician | Two sources claim to be ground truth (e.g., Temporal event history vs `runtime.json`) with no conflict-resolution protocol |
| Divergent access paths | Logician | Same backend accessed via SDK (≤100ms) and CLI shell-out (unbounded latency) with no primary/secondary designation |
| Unbounded divergence | Root-Cause | Write-behind snapshot with no sync guarantee or push notification — divergence window grows without bound |
| Absolute claims | Falsifier | "Zero data loss", "zero-risk migration" — stated as absolutes without probabilistic calibration |
| Missing confidence | Calibrator | Performance targets stated as requirements without confidence estimates or latency budgets |
| Symptom-only fixes | Root-Cause | Degradation fallback drops durability (the very property Temporal was adopted for) without grace period or reconnection attempt |

---

## Suggested Critic Batch Plan for Architecture

Instead of the default 3 batches (all 8 critics), use 2 focused batches:

**Batch 1 — Structure & Correctness:**
- Logician (TS-4): dependency consistency, data flow, error handling
- Auditor (TS-2): PRD cross-reference, component completeness
- Falsifier (TS-5): falsifiable claims, performance targets

**Batch 2 — Quality & Depth:**
- Root-Cause Hunter (TS-8): symptom vs cause, workaround detection
- Skeptic (TS-1): verification questions for unverified claims
- Calibrator (TS-6): confidence gaps in estimates and targets

Skip Stylist and FP Auditor for architecture docs unless the document is
over 5,000 lines or the user explicitly requests editorial review.

---

*Session: hermes-swarm architecture, 2026-06-07*
*Architecture: 3,490 lines, 9 components, 3 workflows, 7+ activities*
*Critics: 6 critics, 48 findings, 5 P1 fixes applied*
