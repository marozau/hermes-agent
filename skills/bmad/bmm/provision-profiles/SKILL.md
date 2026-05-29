---
name: provision-profiles
description: |
  Solutioning-phase skill: derive the Hermes profiles + skills the
  implementation phase will require, install/create what's missing, validate
  with smoke tests. Runs between solutioning-gate-check and sprint-planning
  as a hard gate for level >= 2 projects. Trigger on: "provision profiles",
  "what skills do we need", "set up profiles for implementation".
version: 6.6.0
author: BMAD Community (Hermes port by im)
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [bmad, bmm, solutioning, profile-provisioning, skill-discovery]
    category: bmad
    related_skills: [create-architecture, epics-stories, solutioning-gate-check, sprint-planning]
---

# Profile Provisioning

## Purpose

Between solutioning and implementation, decide what Hermes profiles and what
skills the implementation phase needs. If a profile is missing, create it. If
a skill is missing, fetch it from a known registry. Validate before
implementation starts.

## Inputs

- `planning-artifacts/prd-*.md`
- `planning-artifacts/architecture-*.md` (especially the `technology_choices:`
  YAML block if present — see "Architecture preparation" below)
- `planning-artifacts/epics-stories-*.md`
- `bmad/config.yaml` — optional `profile_mapping:` + `profile_provisioning:`
  overrides
- `~/.hermes/profiles/` — current installed profiles

## Outputs

| File | Purpose |
|---|---|
| `planning-artifacts/profile-requirements.yaml` | Internal: capability inventory + per-profile requirements + provisioning plan |
| `~/.hermes/profiles/<name>/` (new) | New profile scaffolds when needed |
| `~/.hermes/skills/<scope>/<name>/` (new) | New skill installs with `_provenance.yaml` |
| `planning-artifacts/profile-validation-report.md` | User-facing: what profiles are healthy, what's needed, what's blocked |

Status slot: `solutioning.profile-provisioning` flips to `complete` when the
validation report says all required profiles are HEALTHY.

## Protocol (6 steps)

### Step 1: Capability extraction

Read PRD, architecture, and epics-stories. Build a capability inventory:

```yaml
capabilities:
  languages: {typescript: [...sources...], python: [...]}
  test_frameworks: {pytest: [...], playwright: [...]}
  build_tools: {pnpm: [...], cargo: [...]}
  databases: {sqlite: [...], postgres: [...]}
  external_services: {github: [...], slack: [...]}
  workflows: {code-review: [...], test-design: [...]}
```

If architecture has a structured `technology_choices:` YAML block, prefer it
(higher confidence). Otherwise fall back to prose pattern-matching with these
heuristics:

- "use X" / "we will use X" / "X for Y" → language/framework signal
- "X version Y" / "X (Y+)" → version constraint
- Code blocks tagged with a language → language signal
- "configure X" / "set up X" → tool requirement

Tag each capability with `confidence` (HIGH from structured block, MED from
prose, LOW from inference).

### Step 2: Capability → profile mapping

Apply the mapping (configurable in `bmad/config.yaml` `profile_mapping:`,
defaults below):

```yaml
default_mapping:
  default:
    capabilities: [languages.python, test_frameworks.pytest]
  developer-typescript:
    capabilities: [languages.typescript, test_frameworks.playwright, build_tools.pnpm]
  qa:
    capabilities: [test_frameworks.*, workflows.test-design, workflows.test-review]
  security-audit:
    capabilities: [security_review, workflows.adversarial-review]
  docs:
    capabilities: [workflows.tech-writer]
```

Prefer existing profiles over new ones. Cap new profiles at 2 per project
unless user overrides (avoids profile sprawl).

### Step 3: Profile state check

For each required profile:
- Read `~/.hermes/profiles/<name>/config.yaml` (or note absence)
- Read its `skills/` directory
- For each required capability, check if any installed skill declares it
  (via `metadata.hermes.tags` or `_provenance.yaml`)
- Mark profile as `existing-complete`, `existing-incomplete`, or `missing`

### Step 4: Skill discovery

For each missing skill, search prioritized registries (per
`research/technical-skill-ecosystems-2026-05-21.md` verified list):

| Priority | Registry | License | Notes |
|---|---|---|---|
| 1 | `~/.hermes/skills/` local | various | Already installed; first; offline |
| 2 | HermesHub (NousResearch) | various | First-party; highest portability |
| 3 | `anthropics/skills` | Apache-2 except 4 doc skills | Load as-is |
| 4 | `openai/skills` (Codex) | per-skill `LICENSE.txt` | Per-skill license audit |
| 5 | `alirezarezvani/claude-skills` | community | Recent activity |
| 6 | `sickn33/antigravity-awesome-skills` | community | Active |
| 7 | `obra/superpowers` | community | The "super skills" hub |

**Use the existing Hermes installer:**
`~/.hermes/hermes-agent/tools/skills_hub.py` already handles GitHub Contents
API, `.well-known/skills/index.json`, direct HTTPS, SHA validation,
quarantine, TTL caching, and trust-level dedup. DO NOT write a parallel
installer. Call it from the provisioning logic.

**License gate:** before installing, check the skill's license against
`bmad/config.yaml` `profile_provisioning.license_policy`:

```yaml
profile_provisioning:
  license_policy:
    allow_redistribution: false      # default
    require_acknowledged_license: [Apache-2.0, MIT, BSD-3-Clause]
    prompt_user_on_unknown: true
```

Skills with CC-BY-NC (e.g. `midudev/autoskills`) or source-available (e.g.
Anthropic's 4 document skills) licenses can be installed for **personal use**
only — never bundled into BMAD-shipped profile templates.

**Skip these as redistribution targets:**
- `midudev/autoskills` (CC-BY-NC)
- Awesome-lists (pointer-only, too brittle for programmatic resolution)
- Aider, smolagents (no native skill catalogs)

### Step 5: Install + transform

For each chosen skill:

1. **Install** via `skills_hub.install(candidate)` to
   `~/.hermes/skills/<scope>/<skill-name>/`
2. **Validate** — frontmatter parses; SKILL.md exists; safe imports
3. **Enrich** (if needed) — add Hermes-specific metadata:
   ```yaml
   metadata:
     hermes:
       tags: [<from capability tags>]
       category: bmad           # or 'community' for non-BMAD
       requires_toolsets: []    # populate if skill body references
       fallback_for_toolsets: []
   ```
   Most spec-compliant skills don't need transformation. Enrichment is a
   ~10-line frontmatter addition.
4. **Provenance** — write `_provenance.yaml` next to the skill:
   ```yaml
   source_hub: anthropic-agent-skills
   source_url: https://github.com/anthropics/skills/.../playwright-e2e
   version: 1.2.0
   commit_sha: abc123...
   license: MIT
   transform: hermes-frontmatter-v1-enrichment
   installed_at: 2026-05-29T15:00:00Z
   installed_by: bmad-provision-profiles
   declared_capabilities: [test_frameworks.playwright, languages.typescript]
   ```

For each new profile:

1. **Generate** `~/.hermes/profiles/<name>/config.yaml` from
   `plugins/bmad/profile-template/config.yaml.template`, with deltas applied
   from the per-capability rules in `lib/profile_deltas.py` (e.g.
   languages.typescript → adds `prompt_caching: true`)
2. **Generate** `AGENTS.md` + `SOUL.md` from templates
3. **Wire skills** by symlinking or copying into `~/.hermes/profiles/<name>/skills/`
4. **Preserve** any existing profile content — never overwrite without
   `--force` (per AC-15 precedent)

### Step 6: Validate

For each newly provisioned profile, run a smoke test:

```bash
hermes --profile <name> -p "list your top 3 installed skills and confirm they load"
```

Expected output: profile loads; LLM names ≥ 3 skills; no error.

For each profile's required capabilities, run a micro-task that exercises
the capability:

- `languages.typescript`: "write a one-line valid TypeScript expression"
- `test_frameworks.playwright`: "write a one-line Playwright `expect` call"
- `build_tools.pnpm`: "what's the pnpm command to install dependencies?"

Cap micro-tasks at 5 per profile (cost control). Mark profile HEALTHY,
UNHEALTHY-MISSING-CAPABILITY, or UNHEALTHY-VALIDATION-FAILED.

### Output: validation report

```markdown
# Profile Validation Report — 2026-05-29

## Healthy profiles (2)
- bmad (existing, all required capabilities present)
- developer-typescript (newly created; smoke test PASS; 4/4 capability micro-tasks PASS)

## Unhealthy profiles (0)

## Skills installed (5)
- playwright-e2e (from anthropics/skills v1.2.0, MIT, no transform)
- pnpm-runner (from HermesHub v0.4.1, Apache-2.0, no transform)
- typescript-strict (from openai/skills v1.0.0, MIT, enriched)

## License notes
- All installed skills are redistribution-safe (Apache-2.0 / MIT)
- No CC-BY-NC or source-available skills installed

## Manual action required (0)
```

Flip `solutioning.profile-provisioning: complete`. Sprint-planning can now run.

If any profile is UNHEALTHY → flip to `provision-profiles-blocked`,
escalate to user. Implementation cannot start.

## Integration with the DAG (per `design-dag-and-decision-gates-2026-05-20.md` §16a)

When `/bmad:plan-dag` runs after this skill, it:
- Reads `profile-requirements.yaml`
- Emits a `bootstrap_nodes:` section with `provision-profiles` as a node
- Adds `blocked_by: [provision-profiles]` to every implementation node
- Annotates each implementation node with `required_capabilities` from this
  skill's capability inventory

Under autonomous DAG execution (Prefect/Argo/Temporal), the provisioning
runs as the first node; all implementation waits on it.

## Architecture preparation

For best results, `bmad-create-architecture` should emit a structured YAML
block in `architecture-*.md`:

````markdown
## Technology choices

```yaml
technology_choices:
  languages: [typescript, python]
  frontend: react
  backend: fastapi
  test:
    e2e: playwright
    unit: pytest, vitest
  build: pnpm
  databases: [sqlite, postgres]
  external_services: [github, slack]
```
````

If this block is present, capability extraction is HIGH-confidence. Without
it, falls back to prose pattern-matching (MED-confidence).

## Failure modes — what to watch for

| # | Failure | Mitigation |
|---|---|---|
| 1 | Capability inventory misses a tool the dev needs later | `dev-story` skill re-checks at story-open; escalates back here |
| 2 | Low-quality hub skill | Validation + smoke test catches gross failures; license gate filters |
| 3 | Hub URL breaks | `skills_hub.py` has TTL cache + quarantine; provenance enables archive.org fallback |
| 4 | Profile sprawl over time | Default cap of 2 new profiles per project; `bmad-cleanup-profiles` skill on retro |
| 5 | Smoke test too weak | Per-capability micro-tasks layered on top of "list skills" test |
| 6 | Bootstrap node takes hours under autonomous DAG | 15-min timeout; partial report so user can decide to skip incomplete profiles |

## When NOT to run this skill

- Level 0 / 1 projects — `provision-profiles` is optional; quick-flow work
  uses the default profile and existing skills
- User explicitly opts out via `bmad/config.yaml` `profile_provisioning.enabled: false`
- All required capabilities are already present in current profiles (the
  skill detects this in Step 3 and short-circuits to a no-op success)

## References

- `planning-artifacts/design-profile-provisioning-2026-05-21.md` — the
  full design rationale
- `planning-artifacts/research/technical-skill-ecosystems-2026-05-21.md` —
  verified hub list + license lattice
- `planning-artifacts/design-dag-and-decision-gates-2026-05-20.md` §16a —
  DAG integration
- `~/.hermes/hermes-agent/tools/skills_hub.py` — the installer this skill
  composes with
