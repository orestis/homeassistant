#!/usr/bin/env bash
# Run the wall-display Flask app locally for development.
# Reads HA token from ../.ha-token and talks to HA at 192.168.1.48.
# Flask runs with debug/reload so changes are picked up automatically.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Activate project venv
source ../.venv/bin/activate

export HA_URL="http://192.168.1.48:8123"
export HA_TOKEN="$(cat ../.ha-token | tr -d '[:space:]')"
export FLASK_DEBUG=1
export TEMPLATES_AUTO_RELOAD=1
export PORT=5001

echo "Starting local dev server..."
echo "  HA_URL=$HA_URL"
echo "  HA_TOKEN=$(echo $HA_TOKEN | head -c 20)..."
echo "  http://localhost:$PORT"
echo ""

exec python3 -u app.py
