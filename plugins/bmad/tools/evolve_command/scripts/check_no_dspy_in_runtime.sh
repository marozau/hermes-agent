#!/usr/bin/env bash
# check_no_dspy_in_runtime.sh — TI-2 isolation gate
#
# Ensures no 'import dspy' or 'from dspy' appears in the runtime plugin code.
# DSPy must ONLY be imported inside tools/evolve_command/.
#
# Exit 0 = PASS (no dspy imports found)
# Exit 1 = FAIL (dspy imports found in runtime)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../../.." && pwd)"

# Runtime directories that must never import dspy
RUNTIME_DIRS=(
    "$REPO_ROOT/plugins/bmad/lib"
    "$REPO_ROOT/plugins/bmad/commands"
    "$REPO_ROOT/plugins/bmad/hooks"
    "$REPO_ROOT/plugins/bmad/scripts"
)

FOUND=0

for dir in "${RUNTIME_DIRS[@]}"; do
    if [ ! -d "$dir" ]; then
        continue
    fi
    MATCHES=$(grep -rn --include='*.py' -E '(import dspy|from dspy)' "$dir" 2>/dev/null || true)
    if [ -n "$MATCHES" ]; then
        echo "VIOLATION: dspy import found in runtime directory: $dir"
        echo "$MATCHES"
        FOUND=1
    fi
done

if [ "$FOUND" -eq 1 ]; then
    echo ""
    echo "TI-2 VIOLATION: 'import dspy' or 'from dspy' found in runtime plugin code."
    echo "DSPy must only be imported inside plugins/bmad/tools/evolve_command/."
    exit 1
fi

echo "TI-2 PASS: No dspy imports found in runtime plugin code."
exit 0
