# CLAUDE.md — Hermes Auto-Dream Implementation Playbook

> Read on session start when working in `~/usr-local/hermes/`. The PRD and architecture are the contract; this file is the index + the non-negotiables. Anything ambiguous after reading this file is in the planning artifacts — read them, don't invent.

---

## Mission (one paragraph)

Land the **Hermes Auto-Dream substrate** — a copy-on-write memory consolidation pipeline plus pre-task context injection that closes FAMA Tier-2 #3 — so Hermes' continuous-improvement loop actually closes. The work is engineering execution on the Hermes Agent runtime (`~/.hermes/hermes-agent/` and the broader `~/.hermes/` workspace). It is upstream-aligned with `NousResearch/hermes-agent#10771`: vocabulary, CLI verbs, and artifact layout match byte-for-byte; private extensions are confined to `~/.hermes/dreams/<dream_id>/.hermes-private/`.

## Read order (always)

| # | File | What it answers |
|---|---|---|
| 1 | `planning-artifacts/product-brief.md` | Why; vision; scope |
| 2 | `planning-artifacts/prd-hermes-2026-05-12.md` | 47 FRs + 25 NFRs + risks + acceptance criteria — **the contract** |
| 3 | `planning-artifacts/architecture.md` | 12 ADRs + 7 pattern groups + file layout — **the how** |
| 4 | `planning-artifacts/epics.md` | 7 epics, 34 stories, Given/When/Then ACs — **the work** |
| 5 | `planning-artifacts/workflow-status.yaml` | Where in the phase we are |
| 6 | `planning-artifacts/research/technical-hermes-issue-10771-2026-05-12.md` | Upstream design consensus (typed entries, staged artifacts, dry-run gate) |
| 7 | `planning-artifacts/research/technical-auto-dream-full-2026-05-12.md` | Verbatim auto-dream article + Hermes adaptation; the full dream prompt |
| 8 | `planning-artifacts/research/technical-pretask-context-injection-2026-05-12.md` | Preflight implementation spec |
| 9 | `planning-artifacts/research/technical-llm-prefect-reliable-2026-05-12.md` | Provider routing + Prefect patterns |
| 10 | `planning-artifacts/research/technical-fama-skill-improvement-2026-05-12.md` | FAMA gap analysis (the "why") |
| 11 | `planning-artifacts/research/technical-auto-dream-for-hermes-2026-05-12.md` | Claude Code source dive (lock pattern, gates) |

Read 1→4 always; 5 to know status; 6–11 when the specific subsystem is touched.

---

## Implementation order (dependency-driven)

The architecture's §7.3 mapped to epic.story:

1. Epic 1 stories 1.1 → 1.5 — typed memory + canonical writer + reader changes
2. Epic 1 → migrate existing memory writers to `add_entry()` (Story 1.5; grep until clean)
3. Epic 2 stories 2.1 → 2.3 — append-only raw layer + SessionDB index + weekly CI rebuild check
4. Epic 3 stories 3.1 → 3.5 — `providers.yaml` + `hermes_llm.llm_call` + cross-provider fallback + Pydantic gate + Anthropic cache breakpoints
5. Epic 4 stories 4.1 → 4.2 — memory-dream flow + artifact layout
6. Epic 5 stories 5.1 → 5.3 — recall regression harness (gates apply)
7. Epic 4 stories 4.3 → 4.7 — `hermes dream` CLI verbs (create/status/diff/apply/discard) + lock mechanics
8. Epic 6 stories 6.1 → 6.4 — trust-plane wiring (attestation pre-flight + carve-out + signing + rebaseline)
9. Epic 7 stories 7.1 → 7.6 — preflight plugin in shadow mode (telemetry only, no injection) for 5 days
10. Epic 7 → flip preflight to live; wire verify self-report fields (Story 7.7)
11. Phase-4 readiness review → unpause Prefect schedules

**Do not skip ahead.** Each later story assumes earlier ones are in place; in particular, Epic 4 cannot ship before Epic 1 (typed entries) and Epic 2 (raw layer) are in.

---

## Hard invariants (boundary rules — DO NOT violate)

These are AI-agent-consistency anchors. An implementer **could decide differently** if not pinned, and doing so breaks the design.

1. **Only `hermes_memory.add_entry()` writes typed memory entries** (FR-3). No direct file-system writes to `~/.hermes/agents/*/memory/` from skills, plugins, LLMs, or anywhere else. Siblings: `update_entry`, `supersede_entry`, `expire_entry`.
2. **Only `hermes_llm.llm_call(LLMSpec)` calls LLM providers** (FR-37, NFR-23). No `import anthropic` / `import openai` / `import deepseek` in skills or plugins.
3. **LLMs NEVER write to `~/.hermes/skills/`, any `SOUL.md`, or any `USER.md`** (NFR-13). All LLM-proposed mutations land as patches under `~/.hermes/dreams/<dream_id>/`.
4. **Dreams NEVER mutate live state** (ADR-2 / FR-14). Copy-on-write to `~/.hermes/dreams/<dream_id>/`; `apply` is the only effectful step; manual ack required.
5. **soul-guardian's protected set MUST exclude `~/.hermes/agents/*/memory/` and `~/.hermes/dreams/`** (FR-43). Verified at every `create` start; mis-config = abort.
6. **The MEMORY.md rebuildability invariant** (FR-11): MEMORY.md must be rebuildable from `~/.hermes/raw/` + sessions alone. Weekly CI check enforces.
7. **Kill ≠ crash on the dream lock** (ADR-8). Crash auto-rewinds lock mtime; user-initiated kill records audit + does NOT rewind. Removes the "kill to force re-run" attacker primitive.
8. **`valid_until` filters at read; NEVER deletes from disk** (FR-4). Stale entries are filtered from the prompt but kept as evidence.
9. **Apply is idempotent by `dream_id`** (FR-22). Re-applying the same dream returns "no changes." Content-hashed patches enforce this.
10. **Fallback is always cross-provider** (FR-38). Never DeepSeek→DeepSeek; never Anthropic→Anthropic. Single-provider failures must surface.
11. **Pydantic schemas gate every effectful LLM output** (FR-40, NFR-12). Free-text output is allowed only for narrative-only blocks (e.g., REPORT.md body).
12. **Anthropic prompt caching uses three explicit breakpoints** (ADR-7): system block, skills bundle, trajectory excerpts. Dynamic content goes below the last breakpoint. Bundle is byte-stable within a flow run (cache-break trap; see `AGENTS.md` § Auto-Dream Substrate → Cache-break trap).

---

## Canonical helpers (build these first, use everywhere)

```python
# ~/.hermes/lib/hermes_memory.py
def add_entry(type: Literal["preference","fact","procedure","episode","superseded","trajectory","unknown"],
              body: str, source: str, *,
              evidence: str | None = None,
              valid_until: datetime | None = None,
              supersedes: str | None = None) -> str:
    """Only sanctioned writer of typed memory entries.
    Emits frontmatter unconditionally (id=ULID, created_at, last_used_at).
    Pairs the typed write with a raw-layer append transactionally (FR-12).
    Runs secret-scanner pre-check (NFR-16); aborts on hit.
    Returns the new entry's ULID.
    """

def update_entry(id: str, body: str) -> None: ...
def supersede_entry(old_id: str, new_id: str) -> None: ...  # marks old type:superseded
def expire_entry(id: str) -> None: ...                       # sets valid_until=now
```

```python
# ~/.hermes/lib/hermes_llm.py
class LLMSpec(BaseModel):
    workload: str             # key into ~/.hermes/dreams/providers.yaml
    messages: list[dict]
    response_model: type[BaseModel] | None = None
    cache_breakpoints: list[int] = []
    idempotency_key: str | None = None

@task(retries=3, retry_delay_seconds=[2, 8, 30],
      cache_policy=INPUTS, cache_expiration=timedelta(minutes=10),
      timeout_seconds=120)
def llm_call(spec: LLMSpec) -> dict | BaseModel:
    """Only sanctioned LLM call site. Workload-keyed routing via providers.yaml.
    Cross-provider fallback inside. Pydantic-validated when response_model set.
    Emits one telemetry row to ~/.hermes/observability/llm_calls.jsonl per call.
    """
```

If you find yourself bypassing either helper — stop, read the relevant ADR (ADR-3), and route through the helper instead.

---

## Provider routing (workload-keyed)

`~/.hermes/dreams/providers.yaml`:

```yaml
workloads:
  classify_intent:           # preflight hot path
    primary:  { provider: deepseek, model: deepseek-v4-flash, max_tokens: 80,   timeout: 3 }
    fallback: []             # if it fails, fall back to rule-based; never block
  preflight_polish:
    primary:  { provider: deepseek, model: deepseek-v4-flash, max_tokens: 300,  timeout: 8 }
    fallback: [{ provider: anthropic, model: claude-haiku-4-5-20251001, max_tokens: 300, timeout: 8 }]
  skill_dream_reflect:
    primary:  { provider: anthropic, model: claude-sonnet-4-6, max_tokens: 4000, timeout: 120 }
    fallback: [{ provider: deepseek, model: deepseek-v4-pro, max_tokens: 4000, timeout: 120 }]
  memory_dream_consolidate:
    primary:  { provider: anthropic, model: claude-sonnet-4-6, max_tokens: 6000, timeout: 240 }
    fallback: [{ provider: deepseek, model: deepseek-v4-pro, max_tokens: 6000, timeout: 240 }]
  board_dream_synthesize:    # V2 (weekly)
    primary:  { provider: anthropic, model: claude-opus-4-7,   max_tokens: 8000, timeout: 600 }
    fallback: [{ provider: anthropic, model: claude-sonnet-4-6, max_tokens: 8000, timeout: 600 }]
```

Principles: **DeepSeek for hot paths** (auto context caching, cheap, fast); **Anthropic for thinking** (cache_control breakpoints, quality on Sonnet/Opus); **fallback is always cross-provider**. Workload name is the *only* thing callers specify — never inline a model.

---

## Upstream vocabulary (match byte-for-byte)

From `NousResearch/hermes-agent#10771`. Diverging = rebase pain later.

| Concept | Term to use |
|---|---|
| Artifact dir | `~/.hermes/dreams/<dream_id>/` |
| CLI verbs | `hermes dream {create|status|diff|apply|discard}` |
| In-session triggers | `/dream`, `/dream status`, `/dream diff` |
| Manifest file | `manifest.json` |
| Report file | `REPORT.md` |
| Memory diff | `memory.patch` |
| User diff | `user.patch` |
| Per-skill diff | `skills.proposed/<skill>.patch` |
| Provenance | `sources.jsonl` |
| Private extensions | `<dream_id>/.hermes-private/` |

Memory frontmatter spec (alexzhu0's, verbatim — Story 1.1):

```yaml
---
id: <ULID>
type: preference         # preference | fact | procedure | episode | superseded | trajectory | unknown
created_at: <ISO8601 +TZ>
last_used_at: <ISO8601 +TZ>
source: user-correction  # user-correction | self-derived | dogfood-incident | session:<id> | trajectory | import:<origin>
valid_until: null
supersedes: null
evidence: null
---
{body}
```

---

## Anti-patterns refused

Never:
- "Let the agent pick the model" → workload-keyed routing only.
- "Cache the whole conversation" → cache breakpoints on byte-stable blocks only.
- "Use Sonnet for everything because it's smarter" → DeepSeek V4 Flash for classification + light rewrites; Sonnet/Opus only for reflection + synthesis.
- "Skip Pydantic validation, the model's good" → schemas are the bounded-agency primitive; non-negotiable on any effectful output.
- "Increase retries to 10" → past 3, you're papering over a provider problem; fallback chain handles it.
- "Auto-apply this high-confidence proposal" → forever-out for V1 (PRD §10.3). Even confidence-high additive proposals require manual ack.
- "Skill modifies itself at runtime" → always proposal → ack → apply (FR-19).
- "Just write the audit row from the LLM" → audit rows are deterministic tasks after LLM success.
- "Build a dream bundle mid-flow" → cache-break trap; bundle assembled once per flow run.
- "Edit MEMORY.md directly because it's faster" → only through `hermes_memory.add_entry()` and siblings.
- "Edit SKILL.md to fix a bug" → propose via dream artifact; apply via gated workflow; soul-guardian audits.

---

## How to pick up a story

1. Open `planning-artifacts/epics.md`; find the next pending story in the implementation order (§"Implementation order" above).
2. Open the relevant FR in `planning-artifacts/prd-hermes-2026-05-12.md` (the FR Coverage Map in epics.md tells you which FR a story closes).
3. Open the relevant ADR / pattern in `planning-artifacts/architecture.md`.
4. Implement against the Given/When/Then ACs in the story.
5. Update `planning-artifacts/workflow-status.yaml` when the epic-level milestone changes.
6. If you discover an ambiguity not resolved by docs 1–4, look at the relevant research doc (read order above). If still ambiguous, write it down as an Open Question and surface it — do not silently invent.

---

## Key paths (cheat sheet)

```
~/usr-local/hermes/                      ← BMAD project root (you are here)
  planning-artifacts/                    ← the plan
  CLAUDE.md                              ← this file

~/.hermes/                               ← Hermes runtime workspace (the substrate)
  hermes-agent/                          ← the Python codebase to extend
    hermes_cli/plugins.py                ← VALID_HOOKS lives here (add pre_task_start in Phase 4)
    agent/transports/                    ← provider transports (anthropic/chat_completions/codex/bedrock)
    agent/prompt_caching.py              ← cache_control breakpoint plumbing
    agent/retry_utils.py                 ← jittered backoff
  lib/                                   ← NEW: hermes_memory.py + hermes_llm.py go here
  plugins/preflight/                     ← NEW: the preflight plugin
  dreams/                                ← NEW: providers.yaml, audit.jsonl, <dream_id>/...
  raw/<project>/<role>/YYYY-MM-DD.jsonl  ← NEW: immutable raw layer
  preflight/                             ← NEW: config.yaml, domain-vocab.txt, log/...
  dream-orchestrator/flows/              ← NEW: Prefect flow definitions
  skills/agent/{trajectory-memory,failure-taxonomy,verify}/SKILL.md  ← existing FAMA-derived
  skills/{hermes-attestation-guardian,soul-guardian}/                ← trust-plane skills
  observability/{llm_calls.jsonl, advisory.jsonl}                    ← telemetry sinks
```

---

## Telemetry expectations

Every implementation must emit:
- **Per LLM call** → one JSONL row to `~/.hermes/observability/llm_calls.jsonl` (workload, model, tokens, cache_read, latency, schema status, idempotency_key).
- **Per dream `create`** → `manifest.json` with scope, gates fired, model, cost, signal-density score, recall verdict.
- **Per `apply` / `discard` / `force-override`** → one row to `~/.hermes/dreams/audit.jsonl` (hash-chained).
- **Per preflight invocation** → one row to `~/.hermes/preflight/log/<YYYY-MM-DD>.jsonl`.
- **Per verify run** that fires after a preflight → augmented self-report with `preflight applied: yes|no|partial` and `preflight-cited: <id_or_none>`.

If a code path can do effectful work without emitting telemetry, the path is wrong.

---

## Definition of done (V1)

The 13-item checklist from PRD §18 is the gate. Highlights:
- `hermes_memory.add_entry()` is the only writer of MEMORY.md across the codebase.
- ≥95% of new entries carry frontmatter; legacy entries tolerated.
- `valid_until` filtering measurably reduces stale-context tokens at session start (≥10% drop).
- `hermes dream create` on the current memory dir produces a non-empty artifact with a human-readable REPORT.md.
- Dry-run recall test set catches at least one real regression in the first week.
- One `apply` cycle performed manually; resulting MEMORY.md still passes recall tests one week later.
- Preflight hit-rate ≥ 0.3 on top-1 by week 4.
- Anthropic prompt caching ≥ 70% input-cost reduction on dream flows.
- Re-running the same flow with the same `since` window hits the cache (zero provider cost on second run).
- Simulated DeepSeek 503 triggers Anthropic fallback transparently.
- Schema-validation failure path verified end-to-end.
- Transactional task group rolls back a half-completed dream.
- A simulated attestation drift event aborts `create` cleanly.

---

## Out of scope for V1 (do not implement; tracked for V2)

Per PRD §10.2: auto-scheduling activation, per-role dreams (CEO/CTO/CFO), reflection split (the `reflect` sibling), skill-dream (FR-45–47), board-dream, persona-proposals. If a story implies V2 work, stop and reconfirm scope.

---

*If this file disagrees with PRD or architecture, those win. This file is the index, not the source of truth.*

---
<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **hermes-agent** (33996 symbols, 35770 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/hermes-agent/context` | Codebase overview, check index freshness |
| `gitnexus://repo/hermes-agent/clusters` | All functional areas |
| `gitnexus://repo/hermes-agent/processes` | All execution flows |
| `gitnexus://repo/hermes-agent/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
