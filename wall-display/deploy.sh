#!/usr/bin/env bash
# Deploy wall-display add-on to HA via Samba share.
# Usage: ./deploy.sh
#
# Mounts the Samba share if needed, copies all add-on files, then unmounts.
# Credentials: set HA_SAMBA_USER and HA_SAMBA_PASS env vars,
#   or reads from .samba-pass file. HA_SAMBA_USER defaults to homeassistant.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HA_HOST="${HA_HOST:-192.168.1.48}"
HA_SAMBA_USER="${HA_SAMBA_USER:-homeassistant}"
if [ -z "${HA_SAMBA_PASS:-}" ] && [ -f "$SCRIPT_DIR/.samba-pass" ]; then
    HA_SAMBA_PASS="$(tr -d '\n' < "$SCRIPT_DIR/.samba-pass")"
fi
: "${HA_SAMBA_PASS:?Set HA_SAMBA_PASS env var or create wall-display/.samba-pass}"
MOUNT_POINT="$HOME/mnt/ha_addons"
ADDON_DIR="wall_display"

FILES=(app.py ha_client.py dashboard_config.json Dockerfile build.json config.json requirements.txt run.sh)
DIRS=(templates static)

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
echo "  1. Uninstall the add-on (if installed)"
echo "  2. Add-on Store → ⋮ → Check for updates"
echo "  3. Install Wall Display Dashboard → Start"
