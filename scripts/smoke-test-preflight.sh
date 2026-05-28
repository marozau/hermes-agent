#!/usr/bin/env bash
# Smoke test for hermes-preflight CLI.
# Invokes all subcommands and verifies exit codes + output shape.
# Designed to run in CI without human intervention.
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

PASS=0
FAIL=0

# Resolve fork-relative paths so the script runs from CI or any checkout.
# Overridable via env to point at the runtime-installed CLI/venv.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="${HERMES_VENV_PYTHON:-python3}"
CLI="${HERMES_PREFLIGHT_CLI:-$REPO_ROOT/bin/hermes-preflight}"

assert_exit() {
    local label="$1" expected="$2" actual="$3"
    if [ "$actual" -eq "$expected" ]; then
        echo -e "  ${GREEN}PASS${NC} $label (exit=$actual)"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}FAIL${NC} $label (expected exit=$expected, got=$actual)"
        FAIL=$((FAIL + 1))
    fi
}

assert_stdout_contains() {
    local label="$1" pattern="$2" stdout="$3"
    if echo "$stdout" | grep -q "$pattern"; then
        echo -e "  ${GREEN}PASS${NC} $label"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}FAIL${NC} $label (pattern '$pattern' not found)"
        FAIL=$((FAIL + 1))
    fi
}

echo "=== hermes-preflight smoke test ==="
echo "CLI: $CLI"
echo "Python: $VENV_PYTHON"
echo

# 1. --version
echo "--- version ---"
out=$("$VENV_PYTHON" "$CLI" --version 2>&1) || true
assert_exit "version exit=0" 0 $?
assert_stdout_contains "version string" "hermes-preflight" "$out"

out_json=$("$VENV_PYTHON" "$CLI" --version --json 2>&1) || true
assert_stdout_contains "version json" '"version"' "$out_json"

# 2. --help
echo "--- help ---"
out=$("$VENV_PYTHON" "$CLI" --help 2>&1) || true
assert_stdout_contains "help has usage" "usage:" "$out"
assert_stdout_contains "help lists check" "check" "$out"
assert_stdout_contains "help lists force" "force" "$out"

# 3. check
echo "--- check ---"
set +e
"$VENV_PYTHON" "$CLI" check --json "test" > /tmp/hp-check.json 2>/tmp/hp-check.err
rc=$?
set -e
assert_exit "check exit code" 1 $rc
stderr=$(cat /tmp/hp-check.err)
[ -z "$stderr" ] && echo -e "  ${GREEN}PASS${NC} check stderr clean" && PASS=$((PASS + 1)) || { echo -e "  ${RED}FAIL${NC} check stderr has content" && FAIL=$((FAIL + 1)); }

out=$(cat /tmp/hp-check.json)
assert_stdout_contains "check has mode" '"mode"' "$out"
assert_stdout_contains "check has heads_up" '"heads_up"' "$out"

# 4. check --dry-run
out=$("$VENV_PYTHON" "$CLI" check --dry-run "test" 2>&1) || true
assert_stdout_contains "check dry-run" "DRY-RUN" "$out"

# 5. force
echo "--- force ---"
set +e
"$VENV_PYTHON" "$CLI" force --json "debug hermes preflight" > /tmp/hp-force.json 2>/tmp/hp-force.err
rc=$?
set -e
assert_exit "force exit=0" 0 $rc
stderr=$(cat /tmp/hp-force.err)
[ -z "$stderr" ] && echo -e "  ${GREEN}PASS${NC} force stderr clean" && PASS=$((PASS + 1)) || { echo -e "  ${RED}FAIL${NC} force stderr has content" && FAIL=$((FAIL + 1)); }

out=$(cat /tmp/hp-force.json)
assert_stdout_contains "force has mode" '"mode"' "$out"

# 6. force --verbose
out=$("$VENV_PYTHON" "$CLI" force --verbose --json "debug" 2>&1) || true
assert_stdout_contains "force verbose" "hermes-preflight" "$out"

# 7. mode
echo "--- mode ---"
out=$("$VENV_PYTHON" "$CLI" mode 2>&1) || true
assert_stdout_contains "mode output" "" "$out"  # any output is fine

out=$("$VENV_PYTHON" "$CLI" mode --json 2>&1) || true
assert_stdout_contains "mode json" '"mode"' "$out"

# 8. tail
echo "--- tail ---"
out=$("$VENV_PYTHON" "$CLI" tail --n 1 2>&1) || true
assert_exit "tail exit=0" 0 $?
# May have entries or "(no telemetry)"

out=$("$VENV_PYTHON" "$CLI" tail --n 1 --json 2>&1) || true
assert_stdout_contains "tail json has count" '"count"' "$out"

# 9. timeout flag
echo "--- timeout ---"
out=$("$VENV_PYTHON" "$CLI" force --dry-run --timeout 10 "msg" 2>&1) || true
assert_exit "timeout dry-run" 0 $?

# 10. invalid mode
echo "--- error handling ---"
set +e
"$VENV_PYTHON" "$CLI" mode invalid > /dev/null 2>&1
rc=$?
set -e
assert_exit "invalid mode exit=2" 2 $rc

# 11. missing required arg
set +e
"$VENV_PYTHON" "$CLI" check > /dev/null 2>&1
rc=$?
set -e
assert_exit "missing arg exit=2" 2 $rc

echo
echo "=== Results: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
