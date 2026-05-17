#!/bin/bash
# deploy-skills.sh — sync BMAD skills from fork → live Hermes instance
# Run from ~/usr-local/hermes/ after pulling latest changes.
#
# Usage:
#   ./deploy-skills.sh               # dry-run preview
#   ./deploy-skills.sh --apply       # actually sync

set -euo pipefail

SRC="$(dirname "$0")/skills/bmad"
DST="$HOME/.hermes/skills/bmad"
PLUGIN_SRC="$(dirname "$0")/plugins/bmad"
PLUGIN_DST="$HOME/.hermes/hermes-agent/plugins/bmad"

if [ ! -d "$SRC" ]; then
    echo "❌ skills/bmad/ not found. Run from ~/usr-local/hermes/ root."
    exit 1
fi

if [ "${1:-}" = "--apply" ]; then
    echo "=== Deploying BMAD skills ==="
    rsync -a --delete "$SRC/" "$DST/"
    echo "✅ Skills synced to $DST ($(find "$DST" -type f | wc -l) files)"

    echo ""
    echo "=== Deploying BMAD plugin ==="
    rsync -a --delete "$PLUGIN_SRC/" "$PLUGIN_DST/"
    # Remove __pycache__ after deploy
    find "$PLUGIN_DST" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
    echo "✅ Plugin synced to $PLUGIN_DST ($(find "$PLUGIN_DST" -type f | wc -l) files)"

    echo ""
    echo "ℹ️  Restart gateway to pick up plugin changes:"
    echo "   hermes gateway restart"
else
    echo "=== Dry-run: would sync ==="
    echo "  $SRC → $DST ($(find "$SRC" -type f | wc -l) files)"
    echo "  $PLUGIN_SRC → $PLUGIN_DST ($(find "$PLUGIN_SRC" -type f | wc -l) files)"
    echo ""
    echo "Pass --apply to execute:"
    echo "  ./deploy-skills.sh --apply"
fi
