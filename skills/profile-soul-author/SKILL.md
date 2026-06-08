---
name: profile-soul-author
description: Author, audit, and refactor `~/.hermes/profiles/<profile>/SOUL.md` files to the Profile-File Contract — durable persona + skill loadout + pointer block, no project state. Use when a profile SOUL has rotted (drift alerts, stale counts, broken cross-references, weekly-changing content) or when creating a new profile. Sibling of `soul-guardian` (which protects integrity); this skill authors the content soul-guardian then guards.
---

# profile-soul-author

Codifies the pattern documented in `~/.hermes/docs/PROFILE-FILE-CONTRACT.md`: a profile's `SOUL.md` is **identity**, not **state**. It defines persona, tone, thinking style, skill loadout, inviolable philosophy, and pointers to where project / runtime / memory state actually lives. Anything that changes in under two weeks belongs elsewhere.

This skill turns that contract into a procedure you can execute when a SOUL has rotted.

## When to use this skill

Trigger when **any** of these is true:

- `soul_guardian.py check` shows drift alerts for `profiles/<p>/SOUL.md` and the right fix is "update the baseline," not "restore."
- The SOUL is past ~120 lines (the contract caps at ~80; 100+ means content belongs elsewhere).
- The SOUL contains state that changes weekly: plugin counts, skill counts, current sprint, "current project" framing, gateway commit SHAs, profile list.
- A skill named in the loadout has been renamed, consolidated, or removed — the SOUL's cross-reference is dead.
- Creating a brand-new profile and you want SOUL right the first time.
- Auditing all profile SOULs (sweep mode, after the contract has changed).

Do **not** use this skill for `~/.hermes/SOUL.md` (workspace root) or `~/.hermes/AGENTS.md` — those are in `restore` mode and have a different lifecycle. This skill is for **per-profile** `SOUL.md` files only.

## The contract in 30 seconds

| Property | Value |
|---|---|
| Lifecycle | Slow (months) |
| Length target | 50–80 lines (≤100 is a hard ceiling) |
| Owner | Profile owner (the human) |
| Update trigger | Skill renames; persona drift; reading-order changes |
| soul-guardian mode | `alert` (intentionally user-editable; never auto-reverted) |
| Authoritative baseline | `~/.hermes/memory/soul-guardian/approved/profiles/<p>/SOUL.md` |

**The load-bearing rule:**

> If a file's content needs updating for a different reason than its `Update trigger` says, that content belongs in a different file.

Examples (worked):
- "Renovate now uses a classic PAT" → NOT SOUL — repo runbook.
- "Cluster moved from 3 nodes to 1" → NOT SOUL — `kubectl get nodes` (or repo AGENTS.md if a documented invariant).
- "Primary skill renamed from `infra-platform-dev` to `infrastructure-platform-development`" → IS SOUL — skill loadout.
- "Owner now prefers terse responses with no trailing summary" → IS SOUL — tone.

Reference: `~/.hermes/docs/PROFILE-FILE-CONTRACT.md` (the authoritative contract).

## The 7-section template

Use the file at `templates/SOUL.template.md` in this skill. The seven sections (in order) are:

1. **Title + persona paragraph** — `# Hermes Agent Persona — <Role Label>` + 5–8 lines on what this profile *is* and how it differs from sibling profiles.
2. **`## Domain (durable)`** — bulleted list of broad domain areas that don't change in weeks.
3. **`## Primary skill loadout`** — verified-to-exist skills with a one-line "load when" trigger each. Split into "load on every session" vs. "load when …".
4. **`## Inviolable philosophy`** — 5–7 numbered items, philosophy-level (not instance-level). Examples: falsification-first, reading discipline, confidence calibration, don't bypass canonical writers.
5. **`## Sources of truth (read at session start, do not cache)`** — three subgroups: project (paths), runtime (commands — never trust a snapshot), memory.
6. **`## Profile facts (true regardless of project)`** — workspace path, model (from `<profile>.json`), MEM0_USER_ID, webhook port, MCP endpoint (current + planned per-profile after the relevant Epic), soul-guardian mode + the `approve` command, recovery info.
7. **`## What NOT to put in this file`** — anti-rule list. Each entry pairs a forbidden topic with its rightful home.

Footer: `Contract reference: ~/.hermes/docs/PROFILE-FILE-CONTRACT.md`.

## Step-by-step procedure

### Step 0 — Workspace check (before anything else)

This procedure writes to two kinds of file:

- **Runtime files** (`~/.hermes/profiles/<p>/SOUL.md`, the soul-guardian baseline at `~/.hermes/memory/soul-guardian/approved/profiles/<p>/SOUL.md`) — not in any git tree; edit live.
- **Source-tree files** (`<repo>/AGENTS.md`, `<repo>/docs/...`, in-repo skill files — Step 3's migration destinations) — IN git; need a feature branch.

Before starting, verify two things:

**A. You are NOT inside the live Hermes runtime at `~/.hermes/hermes-agent/`.**

```bash
pwd | grep -qE '(^|/)\.hermes/hermes-agent(/|$)' && {
  echo "STOP. cwd is inside live runtime. cd to a worktree and rerun."; exit 1; }
```

The runtime is what's executing right now. Even though it is a git checkout, editing or committing there is forbidden (see `~/usr-local/hermes/AGENTS.md` §"Workspace Discipline"). The runtime syncs ONE-WAY from origin via `hermes update`. Commit there and you'll either mutate the running binary or lose the changes on the next update.

**B. You are on a feature branch in a worktree, not on `main` in the dev tree.**

If Step 3's migration list ends up empty (the SOUL had no operational content to migrate), the branch step is optional — but the cwd check (A) is mandatory regardless. When in doubt, branch:

```bash
cd <repo>                                # the source tree whose files Step 3 will touch
                                         # for Hermes itself: ~/usr-local/hermes/
git status                               # must be clean
git branch --show-current                # must NOT be 'main' / 'master'

# If on main → create a worktree (preferred over a same-tree branch):
git worktree add ~/usr-local/<project>/worktree/<name> \
                  -b refactor/profile-<p>-soul main
cd ~/usr-local/<project>/worktree/<name>
```

Never commit Step 3 migrations to `main` directly. The procedure that follows assumes you're on a feature branch in a worktree from this point on.

### Step 1 — Decide what the profile IS (not what it knows)

In one paragraph, in your head: what does this profile *operate*? What's its domain? How does it differ from its sibling profiles (so the persona statement can name the distinction)?

Verify the distinction is real:

```bash
# Sibling profile SOULs — read the persona line of each to make sure
# your new persona is distinct
for p in $(ls -d ~/.hermes/profiles/*/); do
  echo "=== $(basename $p) ==="; head -3 "$p/SOUL.md" 2>/dev/null
done
```

If you can't articulate the distinction in one sentence, the profile shouldn't exist as a separate profile — collapse it into the closest sibling.

### Step 2 — Audit what's currently in SOUL.md (when refactoring)

Skip for new profiles. For existing rotted SOULs:

```bash
PROFILE=<name>
SOUL=~/.hermes/profiles/$PROFILE/SOUL.md
wc -l $SOUL
grep -nE '^#+ ' $SOUL                          # see all sections
grep -nE '[0-9]+\+? (profiles?|skills?|hooks?|commands?)' $SOUL   # state-snapshot smell
grep -nE '20[0-9]{2}-[0-9]{2}-[0-9]{2}|commit [0-9a-f]{7,}' $SOUL  # date / commit-SHA smell
```

Each match is a candidate for migration out. Categorize each section by the **load-bearing rule** above.

### Step 3 — Migrate operational content OUT before deleting

For every section that fails the rule, find its rightful home **before** deleting from SOUL. Do not paraphrase from memory — copy verbatim where the content represents hard-won knowledge (race conditions, specific commit SHAs, recovery procedures). Knowledge that costs you time to lose belongs in a skill or runbook, not in your head between rewrites.

Common destinations:

| What | Goes to |
|---|---|
| `hermes update` procedure, gateway hygiene, profile-creation hazards, triage order | `~/.hermes/skills/devops/hermes-operations/SKILL.md` |
| Repo topology, dev/live tree workflow, branch convention, plugin architecture | `<repo>/AGENTS.md` |
| Cluster topology, deployed services, sprint state | `<repo>/AGENTS.md` or live `kubectl` |
| Tech-stack tables, version pins | `<repo>/docs/ARCHITECTURE.md` |
| Architectural decisions with rationale | `<repo>/docs/ARCHITECTURE.md` + `git log` |
| Specific inviolable rules (e.g. "no IngressRoute") | `<repo>/AGENTS.md` |
| Provider routing, workload-keyed model choice | `<repo>/dreams/providers.yaml` (or the equivalent project file) |

Every `<repo>/...` destination in this table is a source-tree edit. Step 0 already established that you're on a feature branch in a worktree (NOT inside `~/.hermes/hermes-agent/`, NOT on `main`). All migration edits land on that branch. Open ONE PR for the whole migration — splitting per-file makes review harder (the reviewer needs to see what came out of SOUL and where it went, as one diff).

Verify destinations *before* deletion:

```bash
# Did the gateway hygiene lore really land in hermes-operations?
grep -E "Race A|Race B|name drift|sys.path|bootstrap" ~/.hermes/skills/devops/hermes-operations/SKILL.md
# Did the repo topology land in the project AGENTS.md?
grep -E "DEV TREE|LIVE TREE|hermes update" <repo>/AGENTS.md
```

If the destination doesn't have the content yet, append it there **before** rewriting SOUL.

### Step 4 — Back up the old file

```bash
DATE=$(date -u +%Y-%m-%d)
cp ~/.hermes/profiles/<profile>/SOUL.md ~/.hermes/profiles/<profile>/SOUL.md.bak.$DATE
```

The file isn't under git — this backup is your only diff history. Delete the backup **after** Step 7 verifies clean.

### Step 5 — Write the new SOUL.md from template

```bash
cp ~/.hermes/skills/profile-soul-author/templates/SOUL.template.md \
   ~/.hermes/profiles/<profile>/SOUL.md
# Then edit to fill in the angle-bracket placeholders.
```

**Pull each profile fact from a verified source**, not from memory:

| Field | Source |
|---|---|
| Model | `~/.hermes/profiles/<profile>.json` → `.model.model` |
| MCP endpoint | shared `https://mcp.localhost/sse` today; planned per-profile after Epic 36 (`~/usr-local/infra/planning-artifacts/epics-toolhive-multi-tenancy-refactor-2026-05-31.md`) |
| Webhook port | port-assignment table in `~/.hermes/skills/devops/hermes-operations/SKILL.md` |
| Workspace path | `~/.hermes/profiles/<profile>/` |
| soul-guardian mode | `alert` for profile SOULs (per `~/.hermes/skills/soul-guardian/SKILL.md` policy table) |

**Verify every skill in the loadout actually exists:**

```bash
for s in <skill> <skill> <skill>; do
  find ~/.hermes/skills ~/.hermes/profiles/<profile>/skills -maxdepth 4 -name SKILL.md \
    | xargs grep -l "^name: $s\$" 2>/dev/null | head -1
done
```

If a skill returns nothing, do not list it. Phantom skills make the file lie.

### Step 6 — Run the verification checklist

Use `scripts/verify-soul.sh <profile>` in this skill — runs the seven mechanical checks:

```bash
~/.hermes/skills/profile-soul-author/scripts/verify-soul.sh <profile>
```

What it checks:

1. **Line count ≤ 100** (aim 50–80).
2. **All required sections present** (Domain, Primary skill loadout, Inviolable philosophy, Sources of truth, Profile facts, What NOT to put here).
3. **No state-snapshot smells**: counts ("N+ profiles", "N skills"), dates, commit SHAs.
4. **No dead skill references**: every `name:` mentioned in the loadout resolves to an actual `SKILL.md`.
5. **No `update-baseline` wording** — the actual subcommand is `approve` (the contract doc has a stale `update-baseline` reference; don't propagate it).
6. **soul-guardian mode line present and correct**.
7. **Footer present**: `Contract reference: ~/.hermes/docs/PROFILE-FILE-CONTRACT.md`.

Fix anything that fails. Do not proceed to Step 7 with failures.

### Step 7 — Approve the new baseline

```bash
cd ~/.hermes
python3 skills/soul-guardian/scripts/soul_guardian.py approve \
  --file profiles/<profile>/SOUL.md \
  --actor owner \
  --note "<short rationale>"
```

The subcommand is **`approve`**, not `update-baseline` (the contract doc has a typo on this point).

Verify silence:

```bash
cd ~/.hermes && python3 skills/soul-guardian/scripts/soul_guardian.py status \
  | python3 -c "import sys,json; d=json.load(sys.stdin); \
    [print(x) for x in d.get('files',[]) if 'profiles/<profile>/SOUL.md' in x.get('path','')]"
# expected: ok: true; approvedSha == currentSha
```

Verify the hash-chained audit log accepted the new approval:

```bash
cd ~/.hermes && python3 skills/soul-guardian/scripts/soul_guardian.py verify-audit
# expected: "OK: audit log hash chain verified"
```

Tail the audit log to confirm:

```bash
grep "profiles/<profile>/SOUL.md" ~/.hermes/memory/soul-guardian/audit.jsonl | tail -2
# expected: most recent row is event=approve, actor=owner, note matches what you supplied
```

### Step 8 — Delete the backup (when satisfied)

After at least one session has used the new SOUL successfully:

```bash
rm ~/.hermes/profiles/<profile>/SOUL.md.bak.<date>
```

## Anticipated challenges (and how to handle them)

1. **The destination skill/file already covers some of the migrated content.** Diff before appending; duplicate runbook entries decay faster than single ones. If the destination has the *concept* but a different framing, augment with what's missing rather than copy-pasting.
2. **Skill loadout uncertainty.** Don't guess skill names from memory. `find ~/.hermes/skills -maxdepth 4 -name SKILL.md | xargs grep -l "^name: <candidate>"` proves existence. The other-session research that prompted refactoring may have guessed; verify.
3. **MCP endpoint claims.** Today the shared `https://mcp.localhost/sse` serves all profiles. Per-profile `mcp-<profile>.localhost/sse` lands with the Toolhive multi-tenancy refactor (Epic 36). Do not state the per-profile endpoint as live — frame it as "planned after Epic 36."
4. **soul-guardian's hourly cron alerts during the edit window.** Acceptable noise; minimize by doing Steps 5–7 in one sitting.
5. **The contract doc's `update-baseline` typo.** Use `approve` everywhere — both in the verification commands and in the soul-guardian-mode line of the new SOUL. Note the discrepancy when you're in a position to fix the contract.
6. **Orphan per-profile baselines.** Some profiles have a stale `~/.hermes/profiles/<p>/memory/soul-guardian/approved/SOUL.md` left over from an earlier per-profile state-dir config. The current authoritative baseline lives at `~/.hermes/memory/soul-guardian/approved/profiles/<p>/SOUL.md`. The orphan is harmless but stale; delete it if you want to clean up, but don't trust it for diffs.
7. **`<profile>.json` description field drift.** When refactoring a profile, also check `~/.hermes/profiles/<profile>.json`'s `description` field — it often duplicates another profile's role from a clone. Fix in the same pass (one-line JSON edit).
8. **Tribal-knowledge preservation.** Race conditions, specific failure-mode descriptions, recovery procedures — these cost time to re-learn. Migrate verbatim, not paraphrased. Lossy paraphrasing of failure modes is how production lore disappears.
9. **Don't create `IDENTITY.md`/`USER.md`/`TOOLS.md`/`AGENTS.md` in the same pass** unless you're chartered to. The contract envisions them but they're separate workstreams. Keeping refactors single-file makes review easier.
10. **Cap the recursion.** If you find yourself wanting to update both this skill *and* the contract *and* multiple profile SOULs *and* hermes-operations all at once — stop. Refactor one SOUL, observe how the procedure went, then update this skill if the procedure was missing a step.

## Sweep mode — auditing all profile SOULs

Quick read-only snapshot of every profile's conformance:

```bash
~/.hermes/skills/profile-soul-author/scripts/smoke-test.sh --sweep-only
```

Output is one line per profile: `<profile>: N failure(s)`. Triage into "rewrite now" (≥3 failures) vs. "leave for now" (1–2 cosmetic failures). Don't try to fix all profiles in one session — each one is a separate judgment call about persona + skill loadout + sources-of-truth.

## Smoke test — validating the skill itself

```bash
~/.hermes/skills/profile-soul-author/scripts/smoke-test.sh
```

Runs seven checks: skill artifacts present, frontmatter parses, template has the required sections, deployed to every profile, positive case (a known-conforming SOUL passes), negative case (a synthetic rotted SOUL is correctly flagged with ≥ 4 violations), and the cross-profile sweep. Safe to run anytime; exits non-zero on any failure. Use post-deploy or on a cron to detect drift.

## References

- **Contract:** `~/.hermes/docs/PROFILE-FILE-CONTRACT.md`
- **Worked examples:**
  - `~/.hermes/profiles/cto/SOUL.md` — first profile refactored (2026-06-08, 146 → 80 lines)
  - `~/.hermes/profiles/hermes-sre/SOUL.md` — second profile refactored (2026-06-08, 283 → 93 lines)
- **Rationale:** `~/usr-local/infra/planning-artifacts/research/technical-soul-md-maintenance-2026-06-08.md`
- **Sibling skill:** `~/.hermes/skills/soul-guardian/SKILL.md` — drift-detection + baseline integrity guard (this skill authors what soul-guardian then guards)
- **Migration target for ops content:** `~/.hermes/skills/devops/hermes-operations/SKILL.md`

## What this skill is NOT for

- **Workspace-level `~/.hermes/SOUL.md` or `~/.hermes/AGENTS.md`** — those are in `restore` mode (auto-reverted on drift). Different lifecycle, different procedure. Don't use this skill on them.
- **`IDENTITY.md`, `USER.md`, `TOOLS.md`, per-profile `AGENTS.md`, `MEMORY.md`, `HEARTBEAT.md`** — each has its own owner and update trigger per the contract. This skill covers `SOUL.md` only.
- **Editing the contract itself.** If the contract is wrong, fix the contract — don't paper over it in a SOUL.
