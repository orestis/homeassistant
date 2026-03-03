#!/usr/bin/env python3
"""Create HA entities for the water heater auto-off feature.

Creates (via WebSocket API for helpers, REST for automation):
  - timer.hot_water_auto_off        (30 min timer)
  - input_boolean.hot_water_bypass   (bypass flag)
  - automation: when timer finishes + bypass is off → turn off switch

Usage:
  # From wall-display/ directory, with venv active:
  python setup-water-heater.py

  # Or specify HA connection:
  HA_URL=http://192.168.1.48:8123 HA_TOKEN=... python setup-water-heater.py

Requires: pip install websocket-client
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

import websocket  # websocket-client package


# ---------------------------------------------------------------------------
# REST helpers (for automation + state checks)
# ---------------------------------------------------------------------------

def ha_request(base_url: str, token: str, method: str, path: str,
               data: dict | None = None) -> dict | list | None:
    url = f"{base_url}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")[:300]
        print(f"  ✗ HTTP {e.code}: {body_text}")
        return None
    except Exception as e:
        print(f"  ✗ {e}")
        return None


# ---------------------------------------------------------------------------
# WebSocket helpers (for creating timer / input_boolean)
# ---------------------------------------------------------------------------

def ws_create_helper(base_url: str, token: str,
                     domain: str, data: dict) -> dict | None:
    """Create a helper entity via the HA WebSocket API.

    The HA frontend uses WS commands like ``input_boolean/create`` and
    ``timer/create`` — these are *not* exposed on the REST API.
    """
    ws_url = (
        base_url
        .replace("http://", "ws://")
        .replace("https://", "wss://")
        + "/api/websocket"
    )
    ws = websocket.create_connection(ws_url, timeout=15)
    try:
        # --- auth handshake ---
        msg = json.loads(ws.recv())
        if msg["type"] != "auth_required":
            print(f"  ✗ Unexpected WS message: {msg}")
            return None

        ws.send(json.dumps({"type": "auth", "access_token": token}))

        msg = json.loads(ws.recv())
        if msg["type"] != "auth_ok":
            print(f"  ✗ WS auth failed: {msg}")
            return None

        # --- send create command ---
        cmd = {"id": 1, "type": f"{domain}/create", **data}
        ws.send(json.dumps(cmd))

        msg = json.loads(ws.recv())
        if msg.get("success"):
            return msg.get("result", {})
        else:
            err = msg.get("error", {})
            print(f"  ✗ {err.get('code', '?')}: {err.get('message', 'Unknown error')}")
            return None
    finally:
        ws.close()


# ---------------------------------------------------------------------------
# Entity creation
# ---------------------------------------------------------------------------

def create_timer(base_url: str, token: str) -> bool:
    """Create timer.hot_water_auto_off via WebSocket."""
    print("Creating timer.hot_water_auto_off ...")
    result = ws_create_helper(base_url, token, "timer", {
        "name": "Hot Water Auto Off",
        "duration": "00:30:00",
        "icon": "mdi:timer",
        "restore": True,
    })
    if result is not None:
        print(f"  ✓ timer entity created (id: {result.get('id', '?')})")
        return True
    return False


def create_input_boolean(base_url: str, token: str) -> bool:
    """Create input_boolean.hot_water_bypass via WebSocket."""
    print("Creating input_boolean.hot_water_bypass ...")
    result = ws_create_helper(base_url, token, "input_boolean", {
        "name": "Hot Water Bypass",
        "icon": "mdi:infinity",
    })
    if result is not None:
        print(f"  ✓ input_boolean entity created (id: {result.get('id', '?')})")
        return True
    return False


def create_automation(base_url: str, token: str) -> bool:
    """Create automation: timer finished → turn off switch (unless bypassed).

    Uses REST API (automations have a dedicated config endpoint).
    """
    print("Creating automation (hot_water_auto_off) ...")
    automation_config = {
        "alias": "Hot Water Auto Off",
        "description": "Turn off water heater when timer finishes (unless bypassed)",
        "mode": "single",
        "trigger": [
            {
                "platform": "event",
                "event_type": "timer.finished",
                "event_data": {
                    "entity_id": "timer.hot_water_auto_off"
                }
            }
        ],
        "condition": [
            {
                "condition": "state",
                "entity_id": "input_boolean.hot_water_bypass",
                "state": "off"
            }
        ],
        "action": [
            {
                "action": "switch.turn_off",
                "target": {
                    "entity_id": "switch.hot_water"
                }
            },
            {
                "action": "logbook.log",
                "data": {
                    "name": "Θερμοσίφων",
                    "message": "Auto-off after 30 minutes"
                }
            }
        ],
    }
    result = ha_request(base_url, token, "POST",
                        "/api/config/automation/config/hot_water_auto_off",
                        automation_config)
    if result is not None:
        print("  ✓ automation.hot_water_auto_off created")
        return True
    return False


def verify_entities(base_url: str, token: str) -> None:
    """Check that all entities exist."""
    print("\nVerifying entities ...")
    entities = [
        "switch.hot_water",
        "timer.hot_water_auto_off",
        "input_boolean.hot_water_bypass",
    ]
    for eid in entities:
        state = ha_request(base_url, token, "GET", f"/api/states/{eid}")
        if state:
            print(f"  ✓ {eid} = {state.get('state', '?')}")
        else:
            print(f"  ✗ {eid} not found!")


def main():
    # Resolve HA connection
    base_url = os.environ.get("HA_URL", "http://192.168.1.48:8123")
    token = os.environ.get("HA_TOKEN", "")

    if not token:
        token_file = Path(__file__).parent.parent / ".ha-token"
        if token_file.exists():
            token = token_file.read_text().strip()

    if not token:
        print("Error: set HA_TOKEN env var or create ../.ha-token file")
        sys.exit(1)

    print(f"HA: {base_url}")
    print()

    # Check that switch.hot_water exists first
    sw = ha_request(base_url, token, "GET", "/api/states/switch.hot_water")
    if not sw:
        print("✗ switch.hot_water not found in HA! Aborting.")
        sys.exit(1)
    print(f"✓ switch.hot_water exists (state: {sw.get('state')})")
    print()

    # Create helpers via WebSocket, automation via REST
    ok = True
    ok = create_timer(base_url, token) and ok
    ok = create_input_boolean(base_url, token) and ok
    ok = create_automation(base_url, token) and ok

    if ok:
        print("\n✓ All entities created successfully!")
    else:
        print("\n⚠ Some entities failed — check errors above")

    verify_entities(base_url, token)


if __name__ == "__main__":
    main()
