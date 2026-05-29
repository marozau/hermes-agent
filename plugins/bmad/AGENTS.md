# AGENTS.md — hermes-bmad

> **For AI agents working in this repo.** Read this file first. Everything else points off it.

## What this project is

A first-class **BMAD Method v6.6.0 plugin for the Hermes agent framework**. Brings BMAD's structured product-development workflows (Analysis → Planning → Solutioning → Implementation) to Hermes users with **feature parity to the Claude Code BMAD adapter plus enhancements** Hermes uniquely enables: hook-driven phase gating, hook-driven progress tracking, isolated sub-agent delegation via `delegate_task`, and a live status header on every prompt.

**Project state today (2026-05-16):** Planning complete (brief → PRD → architecture → epics → sprint plan all produced and validated). Implementation has not started. 36 stories backlog'd across 5 sprints. Existing partial port at `~/.hermes/skills/bmad/` (9 persona skills + 6 templates) is the migration target — Story 2.3 reorganizes it into the new layout.

**Project level:** 2 (medium feature set, 5–15 stories — actual count 36 due to scope expansion via locked decisions D-1, D-4b).
**Effort estimate:** 4–6 days for v1 (per PRD §14 revision).
**Hermes upstream:** `NousResearch/hermes-agent`; user's fork at `marozau/hermes-agent` running ≥ v0.14.0.
**BMAD upstream:** `bmad-code-org/BMAD-METHOD` v6.6.0.

## How to use this repo

The repo itself contains **no implementation code** — it is the **planning workspace** that produced the design for the plugin. The actual plugin will live at `~/.hermes/hermes-agent/plugins/bmad/` once Sprint 1 begins.

This is intentional: BMAD treats planning artifacts as long-lived deliverables that survive the implementation. The repo is the source-of-truth for *why* the plugin is built the way it is.

## Read in this order (planning artifacts)

| # | Document | Why |
|---|---|---|
| 1 | [`planning-artifacts/product-brief.md`](planning-artifacts/product-brief.md) | The pitch: vision, scope, risks, phasing |
| 2 | [`planning-artifacts/research/technical-bmad-hermes-port-research-2026-05-16.md`](planning-artifacts/research/technical-bmad-hermes-port-research-2026-05-16.md) | Feature-parity matrix: every BMAD feature vs Hermes capability vs current port state |
| 3 | [`planning-artifacts/research/technical-design-decisions-research-2026-05-16.md`](planning-artifacts/research/technical-design-decisions-research-2026-05-16.md) | The 8 locked design decisions (D-1…D-8) with falsification evidence |
| 4 | [`planning-artifacts/prd-hermes-bmad-2026-05-16.md`](planning-artifacts/prd-hermes-bmad-2026-05-16.md) | PRD: 18 FRs, 13 NFRs, 15 ACs, 10 risks, decisions baked in |
| 5 | [`planning-artifacts/prd-validation-report-2026-05-16.md`](planning-artifacts/prd-validation-report-2026-05-16.md) | PRD validation: 5/5 APPROVED after P0+P1 fixes |
| 6 | [`planning-artifacts/architecture-hermes-bmad-2026-05-16.md`](planning-artifacts/architecture-hermes-bmad-2026-05-16.md) | Architecture: 14 decisions A-1…A-14, complete plugin tree, naming conventions, enforcement rules |
| 7 | [`planning-artifacts/epics-stories-hermes-bmad-2026-05-16.md`](planning-artifacts/epics-stories-hermes-bmad-2026-05-16.md) | 5 epics, 36 stories with Given/When/Then ACs |
| 8 | [`planning-artifacts/sprint-plan-hermes-bmad-2026-05-16.md`](planning-artifacts/sprint-plan-hermes-bmad-2026-05-16.md) | Sprint plan: 5 sprints, dependencies, critical path, demos |

**Living state ledgers** (not docs; updated as work progresses):
- [`planning-artifacts/workflow-status.yaml`](planning-artifacts/workflow-status.yaml) — phase-by-slot state (analysis/planning/solutioning/implementation)
- [`planning-artifacts/sprint-status.yaml`](planning-artifacts/sprint-status.yaml) — per-story status (backlog / ready-for-dev / in-progress / review / done) for all 36 stories
- [`bmad/config.yaml`](bmad/config.yaml) — project config (project_name, level=2, etc.)

**Per-skill model overrides live in the Hermes profile config**, not the project config. The variation unit is per-profile (different profiles → different review policies on the same project).

```yaml
# ~/.hermes/profiles/<your-profile>/config.yaml
delegation:
  model: deepseek-v4-pro                    # profile default for every delegation
  provider: custom
  base_url: http://localhost:4000/v1
  api_key: sk-litellm-...
  skill_overrides:                          # NEW — per-skill overrides
    bmad-code-review:
      model: claude-opus-4-7
      provider: anthropic                   # optional — bypass LiteLLM if proxy doesn't expose Opus
      base_url: https://api.anthropic.com   # optional
      api_key: sk-ant-...                   # optional
      api_mode: messages                    # optional
```

Resolution precedence (top wins):
1. CLI flag: `/bmad:code-review --model claude-opus-4-7 --diff main..HEAD`
2. Profile config `delegation.skill_overrides.bmad-code-review`
3. Default constant `claude-opus-4-7`

**Why profile, not project:** different profiles do different things on the same project. A `bmad` profile might use DeepSeek for delegation + Opus for code review (cost-efficient default + strong reviewer). A `security-audit` profile on the same project might use Opus for delegation + Gemini for code review (heterogeneous-vendor judge mitigates same-vendor preference leakage per `research/technical-llm-accuracy-and-judge-2026-05-18.md`).

**Generalization path:** the resolver pattern is open to other skills. To add per-skill overrides for `bmad-grounded-review` or `bmad-dev-story`, replicate the `_resolve_reviewer_model` pattern with a different `_SKILL_KEY_FOR_OVERRIDES` constant — and surface them under the same `delegation.skill_overrides` block.

## Epic & sprint overview

Detail lives in (7) and (8) above. Headline:

| Epic | Goal | Stories | Sprint |
|---|---|---|---|
| **1: Get Started** | Plugin loads, project bootstraps (CLI + slash), live status header, `/bmad:status` + `/bmad:dashboard` | 12 | S1 + S2 |
| **2: Plan** | Analysis + Planning slash commands E2E; phase gates + auto-status from hooks | 8 | S2 + S3 |
| **3: Build** | Solutioning + Implementation + Quick-flow commands; sub-agent fan-out; dashboard Section 3 live | 8 | S4 |
| **4: Test** | TEA module (8 skills, 8 commands); 4-subagent fan-out validated | 4 | S5 |
| **5: Brainstorm** | 6 CIS doer skills (personas inlined per D-1, D-7); 3 BMB builders; deprecate `creative-intelligence/` monolith | 4 | S5 |

**Locked v1 modules:** core + bmm + bmb + cis + **tea** (gds deferred to v1.1 per D-4a).
**Locked deferral:** ~~`/bmad:party-mode` → v1.1~~ — **Implemented 2026-05-18.** Inline + `--fan-out` modes; 17 unit tests; round-table format across 3–5 BMAD personas via `~/.hermes/skills/bmad/_shared/agent-manifest.yaml`.

## Critical path

12 sequential dev-sessions (most stories within a sprint parallelize):

```
1.1 → 1.3 → 1.5 → 1.6 → 1.8 → 2.1 → 2.5 → 2.7 → 3.4 → 3.6 → 3.8 → 4.4
```

## Where the plugin code WILL live

```
~/.hermes/hermes-agent/plugins/bmad/
├── plugin.yaml                              # manifest (A-1)
├── __init__.py                              # register(ctx) entrypoint
├── commands/                                # 30+ slash command .md + .py pairs
├── hooks/                                   # 5 hooks
│   ├── on_session_start.py                  # FR-8
│   ├── pre_tool_call.py                     # FR-9 — enforces M1/M7 + phase gates
│   ├── post_tool_call.py                    # FR-10 — auto-updates workflow-status.yaml
│   ├── transform_terminal_output.py         # FR-11 — status header
│   └── subagent_stop.py                     # FR-12
├── scripts/
│   ├── bmad_init.py                         # FR-5 — config bootstrapper
│   └── port_completeness.py                 # FR-18 — 1:1 file equivalence check
├── lib/                                     # pure-functional helpers
│   ├── init.py                              # shared bootstrap (CLI + slash)
│   ├── templates.py                         # jinja2 + PreservingUndefined (A-8)
│   ├── phases.py                            # state machine (A-6)
│   ├── status.py                            # YAML R/W w/ atomic writes (A-7)
│   ├── delegation.py                        # delegate_task wrapper (A-11)
│   ├── subagent_log.py                      # append + rotation
│   └── manifest.py                          # agent-manifest.yaml parser
├── profile-template/                        # FR-17 scaffold
└── tests/{unit,integration,e2e}/            # ≥ 80% coverage on lib/ (NFR-9)
```

Skills (per-user, not bundled with plugin):

```
~/.hermes/skills/bmad/
├── core/       # 10 BMAD personas (analyst, pm, architect, …) — Story 2.3 migrates existing 9
├── bmm/        # ~50 workflow skills — Stories 2.5, 2.6, 3.4, 3.5, 3.7
├── bmb/        # 3 builder skills — Story 5.3
├── cis/        # 6 doer skills with inlined personas — Story 5.1 (D-1, D-7)
├── tea/        # 8 test-architect skills — Story 4.2
├── _shared/    # workflow-engine, agent-manifest.yaml, tea-index.yaml — Story 2.4
└── templates/  # shared Markdown + YAML templates
```

## Conventions (load-bearing — don't deviate without a Decision update)

### Naming (architecture §3 A-3)
- **External / user-visible:** `kebab-case` — slash commands `bmad:create-prd`, CLI `bmad-init`, skill folders `bmm/create-prd/`, YAML keys `solutioning-gate-check`
- **Internal Python:** `snake_case` — `lib/phases.py`, `pre_tool_call.py`, `mark_complete()`
- **ID prefixes:** `FR-N` (PRD requirements), `NFR-N`, `AC-N`, `IO-N` (innovation opportunities), `SM-N` (success metrics), `A-N` (architecture decisions), `D-N` (locked design decisions), `M-N` / `R-N` (BMAD mandates from workflow.xml)

### Module dependency graph (architecture §3 A-2)
`hooks/` → `lib/`. `commands/` → `lib/`. `scripts/` → `lib/`. **Never:** `lib/` → `hooks/` or `lib/` → `commands/`. **Never:** cross-plugin imports. Tests can import anywhere.

### Code rules (architecture §4)
1. **Hooks NEVER raise.** Catch every exception inside hook callbacks; log at ERROR; return `None` (allow). A broken hook must not break the user's session.
2. **`lib/` is pure** for `phases.py` and `templates.py` (no file I/O, no `os.environ` reads). `lib/status.py` does I/O by design.
3. **Atomic writes for status:** tmp file in same dir → `fsync` → `os.replace()`. Use `lib/status._atomic_write` helper.
4. **No path strings in code:** `pathlib.Path` everywhere internally.
5. **No upstream Claude Code adapter prose edits.** Slash command `.md` bodies are ported verbatim. If a fix is needed, fix upstream and re-port.
6. **Skill content is ported verbatim** from BMAD source. Only frontmatter and path references may be edited.
7. **No new top-level dependencies.** Reuse vendored `jinja2` (`pyproject.toml:43`) and `pyyaml`.

### Format rules (architecture §4)
- YAML: `yaml.safe_dump(data, sort_keys=False)`
- Dates: `YYYY-MM-DD`; timestamps: `YYYY-MM-DDTHH:MM:SS` local
- Status YAML enum: lowercase kebab — `not-started | in-progress | complete | optional | required`
- Status paths: project-relative POSIX (`planning-artifacts/...`)
- Block decision return shape: `{"action": "block", "reason": str}` — never `{"blocked": True}`
- Hook log prefix: `[bmad:<hook_name>]`

### Anti-patterns (do NOT):
- Import from `hooks/` or `commands/` into `lib/`
- Mutate the cache dict from outside `lib/status.py`
- `eval()` / `exec()` / `__import__()` to dispatch slash commands
- Markdown command bodies > ~200 lines (BMAD bodies are typically 30-80)
- Put project-specific logic in `transform_terminal_output` beyond header rendering (it runs on every prompt)

## Workflow for picking up the next piece of work

1. Read `planning-artifacts/sprint-status.yaml` — find the next `backlog` story in the active sprint (or start S1 if none active).
2. Run `/bmad:create-story <story-id>` to generate a rich story spec at `implementation-artifacts/stories/<epic>-<story>-<slug>.md`. SM typically creates one or two stories ahead — not the whole sprint up front, so each story can incorporate learnings from prior ones.
3. Flip the story to `ready-for-dev` in `sprint-status.yaml`.
4. Run `/bmad:dev-story implementation-artifacts/stories/<file>.md` to implement.
5. Move to `review`; run `/bmad:code-review` (ideally in fresh context).
6. Move to `done`. If last story in epic, prompt user for `/bmad:retrospective`.

## The 8 locked design decisions (cite by D-N)

| # | Decision | Brief |
|---|---|---|
| **D-1** | CIS persona structure | Split into 5 doer skills (mandatory data carriers); persona shells inlined |
| **D-2** | Template rendering | Hybrid: jinja2 pre-render of 15 deterministic vars, LLM for content vars |
| **D-3** | `bmad_init` packaging | Both CLI (`hermes bmad-init`) + slash (`/bmad:init`) via shared `lib/init.bootstrap()` |
| **D-4a** | `gds` module | Defer to v1.1 (or never) |
| **D-4b** | `tea` module | Ship in v1 |
| **D-5** | `workflow.xml` enforcement | Hybrid: Python-enforce 7 mechanical mandates (M1/M3/M4/M5/M7/M9/R1/R2), prose for 3 judgment (M8/M10/R3) |
| **D-6** | Sub-agent auto-approve | Keep `false` (the flag is misnamed — gates dangerous-shell *inside* children, not delegation) |
| **D-7** | CIS persona shells | Inline into doer-skill frontmatter — no standalone shells |
| **D-8** | `solutioning-gate-check` | Required for level ≥ 2 — phase gate enforced |

Full rationale in `planning-artifacts/research/technical-design-decisions-research-2026-05-16.md`.

## External references

### Hermes side (paths on this machine)
- Plugin system canonical reference: `/Users/im/.hermes/hermes-agent/hermes_cli/plugins.py` (1561 lines)
- Hook list: `plugins.py:128-168` (17 hooks)
- `register_cli_command`: `plugins.py:387-408`
- `register_command` (slash): `plugins.py:412-464`
- Approval hooks (observer-only): `plugins.py:154-167`
- Sub-agent delegation: `/Users/im/.hermes/hermes-agent/tools/delegate_tool.py`
- `DELEGATE_BLOCKED_TOOLS`: `delegate_tool.py:40-106`
- Concurrency caps: `delegate_tool.py:324-420`
- Profile resolution: `/Users/im/.hermes/hermes-agent/hermes_constants.py:14-108`
- jinja2 vendored: `/Users/im/.hermes/hermes-agent/pyproject.toml:43`
- Example CLI+slash plugin: `/Users/im/.hermes/hermes-agent/plugins/google_meet/__init__.py:92-103`
- Existing partial port: `/Users/im/.hermes/skills/bmad/` (9 skills, 6 templates — Story 2.3 migrates)
- Existing BMAD profile shell: `/Users/im/.hermes/profiles/bmad/` (config.yaml, AGENTS.md, SOUL.md — preserve, don't overwrite)

### BMAD side
- Upstream: `github.com/bmad-code-org/BMAD-METHOD` v6.6.0 (authoritative)
- Claude Code adapter reference: `github.com/aj-geddes/claude-code-bmad-skills` (`bmad-v6/` tree)
- Local cache: `/Users/im/.claude/plugins/cache/bmad-method/bmad/6.2.2.0/` (v6.2.2 — newer than this lives on GitHub `main`)
- Orchestration engine: `…/6.2.2.0/_shared/tasks/workflow.xml`
- Agent manifest (CSV): `…/6.2.2.0/_shared/agent-manifest.csv`
- TEA index (CSV, 43 fragments): `…/6.2.2.0/_shared/tea-index.csv`
- BMAD config bootstrapper: `…/6.2.2.0/skills/bmad-init/scripts/bmad_init.py`

### BMAD methodology references (for understanding *why* things are shaped the way they are)
- Workflow philosophy: every workflow ships as `SKILL.md` + step files + templates; never load multiple step files at once
- "Read complete files" mandate (M1): LLMs reflexively use `Read(offset, limit)` — BMAD blocks this on workflow files; we enforce via `pre_tool_call` hook (Story 2.1)
- "Save after every `<template-output>`" mandate (M4): handled by template-output checker in `lib/phases.template_outputs_satisfied()` (Story 1.4)
- "Step-File Architecture" used by TEA (v5+ skills): each skill ships `steps-c/`, `steps-e/`, `steps-v/` subdirectories — port verbatim (Story 4.2)

## When making changes

- **Adding a new requirement?** Update PRD (numbered FR-N), then trace to a story in the epics doc, then add to sprint-status.yaml.
- **Adding a new architectural decision?** Append as A-N in architecture doc with rationale; update affected stories.
- **Changing a locked decision (D-N)?** Re-open the design-decisions research doc; falsify the new option against the old; document the override.
- **Adding a new hook?** Update `plugin.yaml` `hooks:` list + add file in `hooks/` + integration test; respect "hooks never raise" rule.
- **Adding a new slash command?** Add `.md` body + `.py` handler pair in `commands/`; register in `__init__.py`; add `phases.COMMAND_PHASE` entry if phase-gated; update `/bmad:help` body.
- **Adding a new skill?** Add `SKILL.md` under correct module dir (`core/bmm/bmb/cis/tea/_shared`); port body verbatim from BMAD source; update agent-manifest.yaml if persona-bearing.

## Things to flag to the user (not autonomous decisions)

- Any change that touches a locked decision (D-1…D-8) without an updated rationale.
- Any new top-level dependency (jinja2 + pyyaml are the only allowed).
- Skipping NFR-9 coverage target (≥ 80% on `lib/`) for any new lib module.
- Changing the `bmad` profile config without explicit user approval (D-6 keeps `subagent_auto_approve: false`).
- Adding gds module skills (deferred per D-4a).
- ~~Adding `/bmad:party-mode` command (deferred per user decision 2026-05-16).~~ — shipped 2026-05-18.

## Refactor discipline (added 2026-05-21)

Edit-tool failure modes are predictable. Follow the size heuristics below
to avoid the atomic-large-edit thrashing loop documented in
`planning-artifacts/research/domain-bmad-large-edit-failure-modes-2026-05-21.md`.

### Atomic Edit is fine when ALL hold

- `old_string` ≤ ~50 lines
- `new_string` ≤ ~100 lines
- Target text has a stable, unique anchor (function signature, unique
  comment, etc.)

### Chunk when ANY holds

- `old_string` > 100 lines OR `new_string` > 100 lines
- Change touches > 3 disjoint regions of one file
- The last 2 Edits failed identically (you are looping; STOP and reach for
  the `chunked-refactor` skill)

When chunking: **one block = one Edit = one commit**. Reference the
`chunked-refactor` skill at
`~/.hermes/skills/bmad/_shared/chunked-refactor/SKILL.md`.

### Never

- Use `sed` / `python` / `execute_code` to splice content-generating
  rewrites — line numbers shift after every partial edit; the splice
  becomes non-idempotent
- Re-Read the same file > 2 times in a row hoping the anchor will match —
  it won't; the file hasn't changed
- Attempt a > 100-line atomic Edit "just to see if it works" — the
  failure mode is well-documented; trust the data
- Bundle multiple stories into one commit — Pattern 13 anti-bundling; see
  `bmad-create-story` for the SM-side check

### When stuck

If steps in the `chunked-refactor` skill don't unstick you, commit what
has landed, mark the story as `blocked:` with a reason code, and hand back
to the SM (or main agent under autonomous DAG execution). DO NOT loop.

---

## Conventions for this AGENTS.md itself

- Reference paths by absolute path when external (`/Users/im/.hermes/...`), relative when in-repo (`planning-artifacts/...`).
- Cite decisions by ID — `D-2` is more durable than "the template decision".
- When something here drifts from reality, update this file first, then propagate.
