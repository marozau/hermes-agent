#!/usr/bin/env bash
# smoke-test.sh — health check for the profile-soul-author skill
#
# Validates the skill is correctly deployed + the verify-soul.sh tool
# catches what it's supposed to catch. Safe to run anytime after deploy
# or on a cron. Exit 0 if everything is healthy; non-zero on any failure.
#
# Modes:
#   smoke-test.sh                # run all checks
#   smoke-test.sh --sweep-only   # only the cross-profile sweep (read-only)

set -u

# Locate the skill root relative to this script (works from dev or live).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
SK="$(cd "$SCRIPT_DIR/.." && pwd)"
VERIFY="$SK/scripts/verify-soul.sh"

PASS=0
FAIL=0
pass() { echo "  ✓ $*"; PASS=$((PASS + 1)); }
fail() { echo "  ✗ $*"; FAIL=$((FAIL + 1)); }

print_header() {
  echo "════════════════════════════════════════════════════════════════"
  echo "  profile-soul-author — smoke test"
  echo "  skill root: $SK"
  echo "════════════════════════════════════════════════════════════════"
}

print_footer() {
  echo ""
  echo "════════════════════════════════════════════════════════════════"
  echo "  $PASS passed, $FAIL failed"
  echo "════════════════════════════════════════════════════════════════"
}

# --- Cross-profile sweep — read-only; reports current state of every SOUL ---
sweep_profiles() {
  echo ""
  echo "[sweep] Current state of every profile SOUL"
  while IFS= read -r p; do
    [[ -z "$p" ]] && continue
    SOUL="$HOME/.hermes/profiles/$p/SOUL.md"
    if [[ ! -f "$SOUL" ]]; then
      echo "  $p: (no SOUL.md)"
      continue
    fi
    LAST=$("$VERIFY" "$p" 2>&1 | tail -1 | tr -d '=' | tr -s ' ')
    echo "  $p:$LAST"
  done < <(ls -d "$HOME"/.hermes/profiles/*/  2>/dev/null | xargs -n1 basename)
}

if [[ "${1:-}" == "--sweep-only" ]]; then
  sweep_profiles
  exit 0
fi

print_header

# --- [1/7] Skill artifacts present ---
echo ""
echo "[1/7] Skill artifacts present"
[[ -f "$SK/SKILL.md" ]]                   && pass "SKILL.md"                    || fail "SKILL.md missing"
[[ -f "$SK/templates/SOUL.template.md" ]] && pass "templates/SOUL.template.md"  || fail "template missing"
[[ -x "$VERIFY" ]]                        && pass "scripts/verify-soul.sh (+x)" || fail "verify-soul.sh missing or not +x"

# --- [2/7] SKILL.md frontmatter ---
echo ""
echo "[2/7] SKILL.md frontmatter"
grep -qE '^name: profile-soul-author$' "$SK/SKILL.md" && pass "name field present" || fail "missing 'name: profile-soul-author'"
grep -qE '^description: '              "$SK/SKILL.md" && pass "description field present" || fail "missing description field"

# --- [3/7] Template has all required sections ---
echo ""
echo "[3/7] Template has all required sections"
for sec in \
  "^## Domain" \
  "^## Primary skill loadout" \
  "^## Inviolable philosophy" \
  "^## Sources of truth" \
  "^## Profile facts" \
  "^## What NOT to put"; do
  if grep -qE "$sec" "$SK/templates/SOUL.template.md"; then
    pass "section: ${sec#^## }"
  else
    fail "section missing: ${sec#^## }"
  fi
done

# --- [4/7] Per-profile deployment ---
echo ""
echo "[4/7] Per-profile deployment (skill copied into every profile)"
DEPLOYED=0
TOTAL=0
while IFS= read -r p; do
  [[ -z "$p" ]] && continue
  TOTAL=$((TOTAL + 1))
  if [[ -f "$HOME/.hermes/profiles/$p/skills/profile-soul-author/SKILL.md" ]]; then
    DEPLOYED=$((DEPLOYED + 1))
  else
    fail "profile '$p' missing the skill"
  fi
done < <(ls -d "$HOME"/.hermes/profiles/*/  2>/dev/null | xargs -n1 basename)
if (( DEPLOYED == TOTAL )) && (( TOTAL > 0 )); then
  pass "deployed to all $TOTAL profiles"
elif (( TOTAL == 0 )); then
  fail "no profiles found at ~/.hermes/profiles/ — is Hermes installed?"
else
  fail "deployed to only $DEPLOYED/$TOTAL profiles"
fi

# --- [5/7] Positive case — known-good SOUL must pass verify ---
echo ""
echo "[5/7] Positive case — verify-soul.sh on conforming SOULs"
# hermes-sre is the canonical worked example (refactor target on 2026-06-08).
# It must pass clean — that's the proof point that the contract is achievable.
KNOWN_GOOD=(hermes-sre)
for p in "${KNOWN_GOOD[@]}"; do
  if [[ ! -f "$HOME/.hermes/profiles/$p/SOUL.md" ]]; then
    fail "$p has no SOUL.md (cannot run positive case)"
    continue
  fi
  if "$VERIFY" "$p" > /tmp/smoke-soul-$p.log 2>&1; then
    pass "$p SOUL passes verify-soul.sh clean"
  else
    FAILS=$(grep -c '^FAIL:' /tmp/smoke-soul-$p.log)
    fail "$p SOUL: $FAILS failure(s); expected clean"
    grep '^FAIL:' /tmp/smoke-soul-$p.log | sed 's/^/      /'
  fi
done

# --- [6/7] Negative case — synthetic rotted SOUL must be flagged ---
echo ""
echo "[6/7] Negative case — verify-soul.sh catches a synthetic rotted SOUL"
SBX_PROFILE="__profile_soul_author_smoke__"
SBX="$HOME/.hermes/profiles/$SBX_PROFILE"
# Defensive: refuse to write to a real profile dir
if [[ -d "$SBX" ]]; then
  fail "sandbox dir already exists at $SBX — refusing to overwrite"
else
  mkdir -p "$SBX"
  # Write a minimal SOUL that violates the contract in 5 distinct ways:
  # (1) no required sections, (2) state-snapshot count ("8+ profiles"),
  # (3) wrong subcommand ('soul_guardian.py update-baseline'),
  # (4) no contract-reference footer,
  # (5) git-operation content (workspace-discipline anti-pattern from Check 8).
  cat > "$SBX/SOUL.md" <<'ROTTED'
# Profile: smoke-test — rotted SOUL fixture

This file deliberately violates the Profile-File Contract so the
smoke test can confirm verify-soul.sh catches the violations.

We have 8+ profiles, all running on commit a1b2c3d4 from 2026-05-01.

To bump the baseline, run:

    python3 ~/.hermes/skills/soul-guardian/scripts/soul_guardian.py update-baseline \
      --file profiles/smoke-test/SOUL.md --actor owner

For day-to-day work, just `git commit` and `git push` to main directly —
no branch needed. Run `hermes update` when production drifts. Use
`git worktree add` only when explicitly told to.

That's it. No structured sections. No mode line. No footer.
ROTTED

  "$VERIFY" "$SBX_PROFILE" > /tmp/smoke-rotted.log 2>&1
  EXIT=$?
  CAUGHT=$(grep -c '^FAIL:' /tmp/smoke-rotted.log)
  rm -rf "$SBX"

  if (( EXIT == 0 )); then
    fail "rotted SOUL passed verify-soul.sh (should have failed)"
  else
    # Require the script to catch at least the 5 deliberate violations.
    if (( CAUGHT >= 5 )); then
      pass "rotted SOUL flagged ($CAUGHT failures, exit $EXIT)"
    else
      fail "rotted SOUL flagged only $CAUGHT failures (expected ≥ 5)"
    fi
    # Show the specific smells caught (capped at 6 lines for readability)
    echo "      Smells caught (sample):"
    grep '^FAIL:' /tmp/smoke-rotted.log | head -6 | sed 's/^/        - /'
  fi
fi

# --- [7/7] Cross-profile sweep ---
sweep_profiles

print_footer
(( FAIL == 0 )) && exit 0 || exit 1
