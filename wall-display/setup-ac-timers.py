#!/usr/bin/env python3
"""Create HA entities for the split-AC auto-off timer feature.

For each cooling-fleet unit defined in dashboard_config.json, creates:
  - timer.ac_<id>_auto_off            (countdown timer, restore=True)
  - automation: when that timer finishes → climate.set_hvac_mode off

Mirrors setup-water-heater.py: helpers are created over the WebSocket API
(``timer/create`` is not on the REST API), automations over REST.

Usage:
  # From wall-display/ directory, with venv active:
  python setup-ac-timers.py

  # Or specify HA connection:
  HA_URL=http://192.168.1.48:8123 HA_TOKEN=... python setup-ac-timers.py

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
# WebSocket helpers (for creating timer helpers)
# ---------------------------------------------------------------------------

def ws_create_helper(base_url: str, token: str,
                     domain: str, data: dict) -> dict | None:
    """Create a helper entity via the HA WebSocket API.

    The HA frontend uses WS commands like ``timer/create`` — these are
    *not* exposed on the REST API.
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

def create_timer(base_url: str, token: str, unit: dict, default_duration: str) -> bool:
    """Create timer.ac_<id>_auto_off via WebSocket (idempotent).

    HA derives the entity_id by slugifying the timer *name*, so the name must
    slugify to exactly ``unit['timer']`` (e.g. id 'kids' → 'AC Kids Auto Off'
    → timer.ac_kids_auto_off). Using the Greek display name would transliterate
    to a different slug, so we name from the ASCII id.
    """
    timer_eid = unit["timer"]
    # Idempotent: skip if it already exists.
    if ha_request(base_url, token, "GET", f"/api/states/{timer_eid}"):
        print(f"  • {timer_eid} already exists — skipping")
        return True
    name = f"AC {unit['id'].capitalize()} Auto Off"
    print(f"Creating timer for {unit['name']} ({timer_eid}) ...")
    result = ws_create_helper(base_url, token, "timer", {
        "name": name,
        "duration": default_duration,
        "icon": "mdi:timer",
        "restore": True,
    })
    if result is not None:
        print(f"  ✓ timer created (id: {result.get('id', '?')})")
        return True
    return False


def create_automation(base_url: str, token: str, unit: dict) -> bool:
    """Create automation: timer finished → turn the unit's climate off.

    Uses REST API (automations have a dedicated config endpoint).
    """
    auto_id = f"ac_{unit['id']}_auto_off"
    print(f"Creating automation ({auto_id}) ...")
    automation_config = {
        "alias": f"AC {unit['name']} Auto Off",
        "description": "Turn off the split AC when its countdown timer finishes",
        "mode": "single",
        "trigger": [
            {
                "platform": "event",
                "event_type": "timer.finished",
                "event_data": {
                    "entity_id": unit["timer"]
                }
            }
        ],
        "condition": [],
        "action": [
            {
                "action": "climate.set_hvac_mode",
                "target": {
                    "entity_id": unit["climate"]
                },
                "data": {
                    "hvac_mode": "off"
                }
            },
            {
                "action": "logbook.log",
                "data": {
                    "name": f"AC {unit['name']}",
                    "message": "Auto-off (timer finished)"
                }
            }
        ],
    }
    result = ha_request(base_url, token, "POST",
                        f"/api/config/automation/config/{auto_id}",
                        automation_config)
    if result is not None:
        print(f"  ✓ automation.{auto_id} created")
        return True
    return False


def verify_entities(base_url: str, token: str, units: list[dict]) -> None:
    """Check that all timers + the units' climate entities exist."""
    print("\nVerifying entities ...")
    for unit in units:
        for eid in (unit["climate"], unit["timer"]):
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

    # Load cooling-fleet config
    config_path = Path(__file__).parent / "dashboard_config.json"
    with open(config_path) as f:
        config = json.load(f)
    cooling = config.get("cooling_fleet", {})
    units = [u for u in cooling.get("units", []) if u.get("id") and u.get("timer")]
    default_duration = cooling.get("timer_default", "02:00:00")

    if not units:
        print("✗ No cooling_fleet units with id+timer in dashboard_config.json")
        sys.exit(1)

    print(f"HA: {base_url}")
    print(f"Units: {', '.join(u['name'] for u in units)}")
    print()

    ok = True
    for unit in units:
        # Verify the climate entity exists before wiring a timer to it
        cs = ha_request(base_url, token, "GET", f"/api/states/{unit['climate']}")
        if not cs:
            print(f"⚠ {unit['climate']} not found — creating timer anyway")
        ok = create_timer(base_url, token, unit, default_duration) and ok
        ok = create_automation(base_url, token, unit) and ok
        print()

    if ok:
        print("✓ All entities created successfully!")
    else:
        print("⚠ Some entities failed — check errors above")

    verify_entities(base_url, token, units)


if __name__ == "__main__":
    main()
