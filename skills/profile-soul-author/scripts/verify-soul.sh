#!/usr/bin/env bash
# verify-soul.sh — mechanical checks for a per-profile SOUL.md
# Usage: verify-soul.sh <profile>
# Returns 0 on clean, non-zero on any failure.

set -u
PROFILE="${1:-}"
if [[ -z "$PROFILE" ]]; then
  echo "Usage: $0 <profile>" >&2
  exit 2
fi

SOUL="$HOME/.hermes/profiles/$PROFILE/SOUL.md"
if [[ ! -f "$SOUL" ]]; then
  echo "FAIL: $SOUL does not exist" >&2
  exit 2
fi

FAILURES=0
fail() { echo "FAIL: $*"; FAILURES=$((FAILURES + 1)); }
ok()   { echo "OK:   $*"; }
warn() { echo "WARN: $*"; }

echo "=== verify-soul $PROFILE ==="

# 1. Line count
LINES=$(wc -l < "$SOUL")
if (( LINES > 100 )); then
  fail "line count $LINES > 100 (target 50–80); content belongs elsewhere"
elif (( LINES > 80 )); then
  warn "line count $LINES > 80 (target 50–80); consider trimming"
else
  ok "line count $LINES within target"
fi

# 2. Required sections present
for section in \
  "^## Domain" \
  "^## Primary skill loadout" \
  "^## Inviolable philosophy" \
  "^## Sources of truth" \
  "^## Profile facts" \
  "^## What NOT to put"; do
  if grep -qE "$section" "$SOUL"; then
    ok "section present: ${section#^## }"
  else
    fail "missing required section: ${section#^## }"
  fi
done

# 3. State-snapshot smells — counts, dates, commit SHAs
SMELLS=()
if grep -qE '[0-9]+\+? (profiles?|skills?|hooks?|commands?)' "$SOUL"; then
  SMELLS+=("counts (N+ profiles/skills/hooks/commands — state, not identity)")
fi
# Allow dates ONLY in the Epic-36 planning-artifact reference; reject all others
NONALLOWED_DATES=$(grep -nE '20[0-9]{2}-[0-9]{2}-[0-9]{2}' "$SOUL" | grep -vE 'epics-toolhive|planning-artifacts/research/technical' || true)
if [[ -n "$NONALLOWED_DATES" ]]; then
  SMELLS+=("dated facts: $(echo "$NONALLOWED_DATES" | head -1 | cut -c1-80)")
fi
if grep -qE '\bcommit [0-9a-f]{7,}|`[0-9a-f]{7,}`' "$SOUL"; then
  SMELLS+=("commit SHAs (belong in skill / runbook, not persona)")
fi
if (( ${#SMELLS[@]} > 0 )); then
  for s in "${SMELLS[@]}"; do fail "state-snapshot smell: $s"; done
else
  ok "no state-snapshot smells (counts, dates, SHAs)"
fi

# 4. Dead skill references
# Only consider the FIRST backticked token of each bullet line within the
# Primary skill loadout section — that's the skill name. Later backticks
# on the same line are path/trigger context, not skill identifiers.
TMP_SKILLS=$(mktemp)
awk '/^## Primary skill loadout/{flag=1;next} /^## /{flag=0} flag && /^- `/' "$SOUL" \
  | sed -nE 's/^- `([^`]+)`.*/\1/p' > "$TMP_SKILLS"
DEAD=0
while read -r skill; do
  [[ -z "$skill" ]] && continue
  # Plugin-namespaced skills (contain a colon, e.g. `bmad:bmad-code-review`)
  # are resolved by the plugin's registration logic at runtime, not by the
  # static name field. Skip the existence check for those — verifying them
  # statically requires understanding each plugin's prefix convention.
  if [[ "$skill" == *:* ]]; then
    ok "plugin-namespaced skill (delegated to plugin runtime): $skill"
    continue
  fi
  # Static check: find a SKILL.md with `name: <skill>` OR a directory whose
  # name matches (some skills lack a name field but still resolve by dir).
  base=$(basename "$skill")
  found=$(find ~/.hermes/skills ~/.hermes/profiles/"$PROFILE"/skills \
    -maxdepth 6 -name SKILL.md 2>/dev/null \
    | xargs grep -l -E "^name: ($skill|$base)\$" 2>/dev/null | head -1)
  if [[ -z "$found" ]]; then
    dirmatch=$(find ~/.hermes/skills ~/.hermes/profiles/"$PROFILE"/skills \
      -maxdepth 6 -type d -name "$base" 2>/dev/null | head -1)
    if [[ -z "$dirmatch" ]]; then
      fail "skill loadout references nonexistent skill: $skill"
      DEAD=$((DEAD + 1))
    else
      ok "skill resolves by directory name: $skill"
    fi
  else
    ok "skill resolves: $skill"
  fi
done < "$TMP_SKILLS"
rm -f "$TMP_SKILLS"

# 5. `update-baseline` is the WRONG subcommand
# Flag only command-line invocations, not parenthetical mentions that
# explicitly disambiguate (e.g. "The subcommand is `approve`, not `update-baseline`.").
if grep -qE 'soul_guardian\.py[[:space:]]+update-baseline' "$SOUL"; then
  fail "uses command-form 'soul_guardian.py update-baseline' — actual subcommand is 'approve'"
else
  ok "no command-form 'update-baseline' usage"
fi

# 6. soul-guardian mode line present and correct
if grep -qE 'soul-guardian.*alert' "$SOUL"; then
  ok "soul-guardian mode line present"
else
  fail "missing soul-guardian mode line (should mention 'alert' mode + approve workflow)"
fi

# 7. Footer present — accept backticked or bare path
if grep -qE 'Contract reference: \`?~/.hermes/docs/PROFILE-FILE-CONTRACT\.md' "$SOUL"; then
  ok "contract-reference footer present"
else
  fail "missing footer: 'Contract reference: ~/.hermes/docs/PROFILE-FILE-CONTRACT.md'"
fi

# 8. Git / workspace operational content — should live in <repo>/AGENTS.md, not SOUL.
# WARN, not FAIL: the regex matches both persona-level mentions ("this profile
# operates the `hermes update` deployment workflow" in a Domain bullet) and
# directive content ("never commit; run `hermes update`" in Inviolable philosophy).
# The first is fine; the second is a contract violation. Mechanical regex can't
# distinguish — operator judgment per WARN line decides.
# Exempts the soul-guardian `approve` command block (Profile facts).
GIT_HITS=$(grep -nE '\b(git[[:space:]]+(commit|push|checkout|merge|rebase|branch|reset|worktree)|hermes[[:space:]]+update|worktree[[:space:]]+(add|list|remove))\b' "$SOUL" \
  | grep -vE 'soul_guardian|--file profiles/|approve' || true)
if [[ -n "$GIT_HITS" ]]; then
  COUNT=$(echo "$GIT_HITS" | wc -l | tr -d ' ')
  warn "$COUNT git/workspace mention(s) — review each: directive content belongs in <repo>/AGENTS.md, descriptive (Domain bullet) is OK"
  echo "$GIT_HITS" | head -3 | sed 's/^/      /'
else
  ok "no git/workspace ops content"
fi

echo "=== $FAILURES failure(s) ==="
exit $FAILURES
