#!/bin/bash
# deploy.sh — DEPRECATED: autodream is now a first-class package via editable install.
#
# Usage: ./deploy.sh [LIVE_DIR]
#   LIVE_DIR defaults to $HOME/.hermes/hermes-agent/autodream
#
# Kept for backward compatibility with existing workflows. The canonical
# deployment is: cd ~/.hermes/hermes-agent && pip install -e . --no-deps

set -euo pipefail

LIVE_DIR="${1:-$HOME/.hermes/hermes-agent/autodream}"

if [ ! -d "$LIVE_DIR" ]; then
    mkdir -p "$LIVE_DIR"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "[DEPRECATED] deploy.sh is obsolete — autodream is installed via editable wheel."
echo "Copying autodream modules from $SCRIPT_DIR/autodream/ to $LIVE_DIR/ anyway..."

for f in "$SCRIPT_DIR"/autodream/*.py; do
    cp -v "$f" "$LIVE_DIR/"
done

echo "Done. Prefer: cd ~/.hermes/hermes-agent && pip install -e . --no-deps"
