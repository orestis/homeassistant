#!/usr/bin/env bash
# Deploy wall-display add-on to HA via Samba share.
# Usage: ./deploy.sh
#
# Mounts the Samba share if needed, copies all add-on files, then unmounts.
# Credentials: set HA_SAMBA_USER and HA_SAMBA_PASS env vars,
#   or they default to homeassistant / tejhyd-zoqCab-wepgu2.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HA_HOST="${HA_HOST:-192.168.1.48}"
HA_SAMBA_USER="${HA_SAMBA_USER:-homeassistant}"
HA_SAMBA_PASS="${HA_SAMBA_PASS:-tejhyd-zoqCab-wepgu2}"
MOUNT_POINT="$HOME/mnt/ha_addons"
ADDON_DIR="wall_display"

FILES=(app.py dashboard_config.json Dockerfile build.json config.json requirements.txt run.sh setup-wd-correction.py)
DIRS=(templates static)

# ha_client.py lives in the shared package now
HA_CLIENT_SRC="$SCRIPT_DIR/../src/ha_tools/ha_client.py"

# Mount if not already
if ! mount | grep -q "$MOUNT_POINT"; then
    echo "Mounting Samba share..."
    mkdir -p "$MOUNT_POINT"
    mount_smbfs "//${HA_SAMBA_USER}:${HA_SAMBA_PASS}@${HA_HOST}/addons" "$MOUNT_POINT"
fi

echo "Copying files to $MOUNT_POINT/$ADDON_DIR/..."
mkdir -p "$MOUNT_POINT/$ADDON_DIR"
for f in "${FILES[@]}"; do
    if [ -f "$SCRIPT_DIR/$f" ]; then
        cp "$SCRIPT_DIR/$f" "$MOUNT_POINT/$ADDON_DIR/$f"
        echo "  ✓ $f"
    else
        echo "  ⚠ $f not found, skipping"
    fi
done

# Copy ha_client.py from shared package
if [ -f "$HA_CLIENT_SRC" ]; then
    cp "$HA_CLIENT_SRC" "$MOUNT_POINT/$ADDON_DIR/ha_client.py"
    echo "  ✓ ha_client.py (from src/ha_tools/)"
else
    echo "  ⚠ ha_client.py not found at $HA_CLIENT_SRC"
fi

# Copy directories
for d in "${DIRS[@]}"; do
    if [ -d "$SCRIPT_DIR/$d" ]; then
        cp -r "$SCRIPT_DIR/$d" "$MOUNT_POINT/$ADDON_DIR/"
        echo "  ✓ $d/"
    else
        echo "  ⚠ $d/ not found, skipping"
    fi
done

VERSION=$(python3 -c "import json; print(json.load(open('$SCRIPT_DIR/config.json'))['version'])")
echo ""
echo "Deployed v${VERSION}. Now in HA:"
echo "  1. Add-on Store → ⋮ → Check for updates"
echo "  2. Wall Display Dashboard → Update → Restart"
echo ""
echo "Note: Do NOT uninstall — that regenerates the ingress token"
echo "and breaks the iframe URL in the Wall Display dashboard."
