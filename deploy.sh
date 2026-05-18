#!/bin/bash
# deploy.sh — Deploy dev-repo lib modules to live agent runtime path
#
# Usage: ./deploy.sh [--live-dir PATH]
#
# Copies lib/*.py from dev repo to ~/.hermes/lib/ (or specified live dir).
# After deploy, the live agent can import these modules.
#
# Run from dev repo root: cd ~/usr-local/hermes && ./deploy.sh

set -euo pipefail

LIVE_DIR="${1:-$HOME/.hermes/lib}"

if [ ! -d "$LIVE_DIR" ]; then
    echo "Error: target directory $LIVE_DIR does not exist"
    echo "Usage: $0 [--live-dir PATH]"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "Deploying lib modules from $SCRIPT_DIR/lib/ to $LIVE_DIR/"

for f in "$SCRIPT_DIR"/lib/hermes_providers*.py; do
    cp -v "$f" "$LIVE_DIR/"
done

echo "Done. Live agent can import provider adapters."
echo "Restart gateway if running: hermes gateway restart"
