# BMAD Plugin Development — Session Launch Guide

> Handoff document for AI agent sessions. Read before starting work.

## Repositories

| Role | Path | Remote |
|---|---|---|
| **Development** | `~/usr-local/hermes/` | `origin → marozau/hermes-agent-private.git` |
| **Live instance** | `~/.hermes/hermes-agent/` | `origin → public`, `private → private`, `upstream → NousResearch` |

## Start a Development Session

```bash
# 1. Sync the private fork
cd ~/usr-local/hermes
git pull origin main

# 2. Deploy skills to live instance
./deploy-skills.sh --apply

# 3. Work in plugins/bmad/ or skills/bmad/
```

## Commit & Deploy

```bash
# Commit to private fork
git add plugins/bmad/ skills/bmad/
git commit -m "bmad: what changed and why"
git push origin main

# Deploy to live instance
cd ~/.hermes/hermes-agent
git pull private main
git push origin main          # deploy to public fork
hermes gateway restart        # pick up new plugin code
```

## Development Workflow

### Branch Convention
- `main` — stable, deployable to live
- `feat/<name>` — new features (e.g., `feat/party-mode`)
- `fix/<name>` — bug fixes

### Parallel Development
```bash
git checkout -b feat/party-mode      # branch A
# work...
git commit -am "bmad: party-mode scaffold"
git push origin feat/party-mode

git checkout main
git checkout -b feat/gds-module      # branch B
# work...

# Deploy specific branch:
cd ~/.hermes/hermes-agent
git fetch private
git checkout feat/party-mode         # switch live to branch
# or: git pull private feat/party-mode  # merge from private
```

## Plugin Architecture

```
plugins/bmad/
├── plugin.yaml              # Hermes plugin manifest
├── __init__.py              # register(ctx): imports + 39 slash commands + CLI
├── commands/                # 39 .md (body) + .py (handler) pairs
├── hooks/                   # 5 hooks (on_session_start, pre/post_tool_call,
│                           #   transform_terminal_output, subagent_stop)
├── lib/                     # 7 pure-functional modules (phases, status,
│                           #   delegation, subagent_log, templates, init, datetime)
├── scripts/                 # 2 scripts (bmad_init, port_completeness)
├── tests/                   # unit, integration, e2e (9 test files)
└── profile-template/        # FR-17: BMAD profile scaffold
```

## Skills Layout

```
skills/bmad/
├── core/    (11) — BMAD personas (analyst, pm, architect, dev, qa, sm, ...)
├── bmm/     (22) — Workflow skills (prd, brainstorm, research, sprint, dev-story, ...)
├── tea/      (8) — Test-Architect skills (nfr, atdd, test-design, ci, ...)
├── cis/      (6) — Creative Intelligence (Carson, Maya, Quinn, Victor, Sophia, Caravaggio)
├── bmb/      (3) — Builders (agent, module, workflow)
├── _shared/  (2+42) — Shared engines + 42 TEA knowledge fragments
└── templates/(10) — Shared Markdown + YAML templates
```

## Slash Commands (39 total)

**Phase-gated** (require preceding phase complete):
- Analysis (8): init, status, dashboard, product-brief, research, brainstorm, document-project, quick-spec
- Planning (4): create-prd, validate-prd, edit-prd, create-ux-design
- Solutioning (3): create-architecture, epics-stories, solutioning-gate-check
- Implementation (6): sprint-planning, create-story, dev-story, code-review, correct-course, quick-dev

**Ungated** (always available post-init):
- TEA (8): test-framework, atdd, test-design, test-review, trace, nfr, ci, automate
- CIS (6): brainstorming, design-thinking, problem-solving, innovation-strategy, storytelling, presentation
- BMB (3): agent-builder, module-builder, workflow-builder

**General** (1): help

## Deferred to v1.1

- `gds` module (D-4a)
- `/bmad:party-mode` (user decision 2026-05-16)
- Epic retrospectives (optional)

## Key Rules

1. **Develop in the fork, not the live instance.** Commit to `~/usr-local/hermes/`, deploy via `git pull private main` in the live instance.
2. **Skills are per-user, not bundled** (per architecture). They live at `~/.hermes/skills/bmad/` and deploy separately via `deploy-skills.sh`.
3. **Hooks NEVER raise.** Catch every exception; log at ERROR; return `None`.
4. **Atomic writes for status** — tmp file + fsync + os.replace().
5. **No path strings** — `pathlib.Path` everywhere in lib/.
6. **No cross-plugin imports.**
7. **`.gitignore` fix applied**: `/bmad/` (anchored to root) instead of `bmad/` (recursive). `plugins/bmad/` is trackable; root `bmad/config.yaml` stays ignored.
