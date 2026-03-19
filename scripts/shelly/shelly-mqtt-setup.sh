#!/usr/bin/env bash
# Configure MQTT on a Shelly Gen2 device and verify the connection.
# Usage: ./shelly-mqtt-setup.sh <shelly-ip>

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

MQTT_SERVER="homeassistant.local:1883"
MQTT_USER="shelly"
if [ -z "${MQTT_PASS:-}" ] && [ -f "$SCRIPT_DIR/.mqtt-pass" ]; then
    MQTT_PASS="$(tr -d '\n' < "$SCRIPT_DIR/.mqtt-pass")"
fi
: "${MQTT_PASS:?Set MQTT_PASS env var or create shelly/.mqtt-pass}"

SHELLY="${1:?Usage: $0 <shelly-ip>}"

rpc() {
  curl -sf -X POST -d "$1" "http://${SHELLY}/rpc" | python3 -m json.tool
}

# Step 1: check current status
echo "==> Step 1: Checking current MQTT status on ${SHELLY} ..."
CONNECTED=$(rpc '{"id":1,"method":"Mqtt.GetStatus"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['result']['connected'])")
if [ "${CONNECTED}" = "True" ]; then
  echo "Already connected over MQTT. Nothing to do."
  exit 0
fi

# Step 2: show current config
echo ""
echo "==> Step 2: Current MQTT config:"
rpc '{"id":2,"method":"Mqtt.GetConfig"}'

# Step 3: set new config
echo ""
echo "==> Step 3: Configuring MQTT (server=${MQTT_SERVER}, user=${MQTT_USER}) ..."
rpc "{\"id\":3,\"method\":\"Mqtt.SetConfig\",\"params\":{\"config\":{\"enable\":true,\"server\":\"${MQTT_SERVER}\",\"user\":\"${MQTT_USER}\",\"pass\":\"${MQTT_PASS}\"}}}"

# Step 4: reboot
echo ""
echo "==> Step 4: Rebooting device ..."
rpc '{"id":4,"method":"Shelly.Reboot"}' || true  # device may close connection before responding
echo "Waiting 15s for device to come back up ..."
sleep 15

# Step 5: verify
echo ""
echo "==> Step 5: Verifying MQTT connection ..."
rpc '{"id":5,"method":"Mqtt.GetStatus"}'
