#!/usr/bin/env python3
"""Create HA entities for the WD curve solar correction feature.

Creates (via WebSocket API for helpers, REST for automation):
  - input_number.heating_base_offset   (user's manual base offset -10..+10)
  - input_number.wd_solar_correction   (automation's computed correction)
  - input_datetime.wd_last_write       (cooldown tracker)
  - automation.wd_solar_correction     (computes & applies correction)

The automation compares the Antlia outdoor sensor (solar-affected) with the
sheltered Daikin AP sensor and adjusts the leaving water offset to compensate
for solar gain on the Antlia sensor.

Usage:
  # From wall-display/ directory, with venv active:
  python setup-wd-correction.py

  # Or specify HA connection:
  HA_URL=http://192.168.1.48:8123 HA_TOKEN=... python setup-wd-correction.py

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
# Entities & constants
# ---------------------------------------------------------------------------

ANTLIA_OUTDOOR = "sensor.antlia_climatecontrol_outdoor_temperature"
DAIKIN_AP_OUTDOOR = "sensor.daikinap68496_climatecontrol_outdoor_temperature"
CLIMATE_ENTITY = "climate.antlia_leaving_water_offset"

BASE_OFFSET_ENTITY = "input_number.heating_base_offset"
SOLAR_CORRECTION_ENTITY = "input_number.wd_solar_correction"
LAST_WRITE_ENTITY = "input_datetime.wd_last_write"
AUTOMATION_ID = "wd_solar_correction"

# WD curve: LWT = 53.125 - 1.5625 * T_outdoor
WD_SLOPE = -1.5625
DEADBAND = 2.0
COOLDOWN_MINUTES = 30
HYSTERESIS_BYPASS = 2


# ---------------------------------------------------------------------------
# REST helpers
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
# WebSocket helpers
# ---------------------------------------------------------------------------

def ws_create_helper(base_url: str, token: str,
                     domain: str, data: dict) -> dict | None:
    """Create a helper entity via the HA WebSocket API."""
    ws_url = (
        base_url
        .replace("http://", "ws://")
        .replace("https://", "wss://")
        + "/api/websocket"
    )
    ws = websocket.create_connection(ws_url, timeout=15)
    try:
        msg = json.loads(ws.recv())
        if msg["type"] != "auth_required":
            print(f"  ✗ Unexpected WS message: {msg}")
            return None

        ws.send(json.dumps({"type": "auth", "access_token": token}))

        msg = json.loads(ws.recv())
        if msg["type"] != "auth_ok":
            print(f"  ✗ WS auth failed: {msg}")
            return None

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

def create_base_offset(base_url: str, token: str) -> bool:
    """Create input_number.heating_base_offset via WebSocket."""
    print(f"Creating {BASE_OFFSET_ENTITY} ...")
    result = ws_create_helper(base_url, token, "input_number", {
        "name": "Heating Base Offset",
        "min": -10,
        "max": 10,
        "step": 1,
        "initial": 0,
        "mode": "slider",
        "icon": "mdi:tune",
        "unit_of_measurement": "°C",
    })
    if result is not None:
        print(f"  ✓ input_number entity created (id: {result.get('id', '?')})")
        return True
    return False


def create_solar_correction(base_url: str, token: str) -> bool:
    """Create input_number.wd_solar_correction via WebSocket."""
    print(f"Creating {SOLAR_CORRECTION_ENTITY} ...")
    result = ws_create_helper(base_url, token, "input_number", {
        "name": "WD Solar Correction",
        "min": -25,
        "max": 25,
        "step": 1,
        "initial": 0,
        "mode": "box",
        "icon": "mdi:white-balance-sunny",
        "unit_of_measurement": "°C",
    })
    if result is not None:
        print(f"  ✓ input_number entity created (id: {result.get('id', '?')})")
        return True
    return False


def create_last_write(base_url: str, token: str) -> bool:
    """Create input_datetime.wd_last_write via WebSocket."""
    print(f"Creating {LAST_WRITE_ENTITY} ...")
    result = ws_create_helper(base_url, token, "input_datetime", {
        "name": "WD Last Write",
        "has_date": True,
        "has_time": True,
        "icon": "mdi:clock-outline",
    })
    if result is not None:
        print(f"  ✓ input_datetime entity created (id: {result.get('id', '?')})")
        return True
    return False


def create_automation(base_url: str, token: str) -> bool:
    """Create the WD solar correction automation via REST API."""
    print(f"Creating automation.{AUTOMATION_ID} ...")

    automation_config = {
        "alias": "WD Solar Correction",
        "description": (
            "Compares Antlia (solar-affected) vs sheltered Daikin AP outdoor sensor. "
            "When Antlia reads >2°C higher, applies a leaving water offset correction "
            "to compensate for solar gain on the WD curve."
        ),
        "mode": "single",
        "trigger": [
            {
                "platform": "time_pattern",
                "minutes": "/10",
            },
            {
                "platform": "state",
                "entity_id": ANTLIA_OUTDOOR,
                "for": "00:01:00",
            },
            {
                "platform": "state",
                "entity_id": DAIKIN_AP_OUTDOOR,
                "for": "00:01:00",
            },
            {
                "platform": "state",
                "entity_id": BASE_OFFSET_ENTITY,
                "id": "base_offset_changed",
            },
        ],
        "condition": [
            {
                "condition": "not",
                "conditions": [
                    {
                        "condition": "state",
                        "entity_id": CLIMATE_ENTITY,
                        "state": "off",
                    }
                ],
            }
        ],
        "action": [
            # --- Step 1: Compute variables ---
            {
                "variables": {
                    "t_antlia": (
                        "{{ states('" + ANTLIA_OUTDOOR + "') | float(0) }}"
                    ),
                    "t_accurate": (
                        "{{ states('" + DAIKIN_AP_OUTDOOR + "') | float(0) }}"
                    ),
                    "delta": (
                        "{{ (states('" + ANTLIA_OUTDOOR + "') | float(0))"
                        " - (states('" + DAIKIN_AP_OUTDOOR + "') | float(0)) }}"
                    ),
                    "base_offset": (
                        "{{ states('" + BASE_OFFSET_ENTITY + "') | int(0) }}"
                    ),
                    "current_target": (
                        "{{ state_attr('" + CLIMATE_ENTITY + "', 'temperature') | int(0) }}"
                    ),
                    "solar_correction": (
                        "{% set d = (states('" + ANTLIA_OUTDOOR + "') | float(0))"
                        " - (states('" + DAIKIN_AP_OUTDOOR + "') | float(0)) %}"
                        "{% if d > " + str(DEADBAND) + " %}"
                        "{{ ((" + str(abs(WD_SLOPE)) + " * d) | round(0)) | int }}"
                        "{% else %}0{% endif %}"
                    ),
                    "final_offset": (
                        "{% set d = (states('" + ANTLIA_OUTDOOR + "') | float(0))"
                        " - (states('" + DAIKIN_AP_OUTDOOR + "') | float(0)) %}"
                        "{% set corr = ((" + str(abs(WD_SLOPE)) + " * d) | round(0)) | int"
                        " if d > " + str(DEADBAND) + " else 0 %}"
                        "{% set base = states('" + BASE_OFFSET_ENTITY + "') | int(0) %}"
                        "{{ [[-10, base + corr] | max, 10] | min }}"
                    ),
                }
            },
            # --- Step 2: Update solar correction display value ---
            {
                "action": "input_number.set_value",
                "target": {"entity_id": SOLAR_CORRECTION_ENTITY},
                "data": {"value": "{{ solar_correction }}"},
            },
            # --- Step 3: No-op guard — skip write if nothing changed ---
            {
                "condition": "template",
                "value_template": "{{ final_offset | int != current_target | int }}",
            },
            # --- Step 4: Cooldown guard ---
            #   Skip cooldown if: trigger is base_offset_changed,
            #   OR large change (|final - current| >= 2),
            #   OR enough time has passed
            {
                "condition": "or",
                "conditions": [
                    {
                        "condition": "trigger",
                        "id": "base_offset_changed",
                    },
                    {
                        "condition": "template",
                        "value_template": (
                            "{{ (final_offset | int - current_target | int) | abs >= "
                            + str(HYSTERESIS_BYPASS) + " }}"
                        ),
                    },
                    {
                        "condition": "template",
                        "value_template": (
                            "{% set last = states('" + LAST_WRITE_ENTITY + "') %}"
                            "{% if last in ['unknown', 'unavailable', ''] %}"
                            "true"
                            "{% else %}"
                            "{{ (now() - last | as_datetime).total_seconds() > "
                            + str(COOLDOWN_MINUTES * 60) + " }}"
                            "{% endif %}"
                        ),
                    },
                ],
            },
            # --- Step 5: Apply the offset ---
            {
                "action": "climate.set_temperature",
                "target": {"entity_id": CLIMATE_ENTITY},
                "data": {"temperature": "{{ final_offset }}"},
            },
            # --- Step 6: Update last-write timestamp ---
            {
                "action": "input_datetime.set_datetime",
                "target": {"entity_id": LAST_WRITE_ENTITY},
                "data": {"datetime": "{{ now().strftime('%Y-%m-%d %H:%M:%S') }}"},
            },
            # --- Step 7: Logbook entry ---
            {
                "action": "logbook.log",
                "data": {
                    "name": "WD Correction",
                    "message": (
                        "Offset → {{ final_offset }} "
                        "(base={{ base_offset }}, solar={{ solar_correction }}, "
                        "Δ={{ delta | round(1) }}°C: "
                        "Antlia={{ t_antlia }}° / AP={{ t_accurate }}°)"
                    ),
                },
            },
        ],
    }

    result = ha_request(base_url, token, "POST",
                        f"/api/config/automation/config/{AUTOMATION_ID}",
                        automation_config)
    if result is not None:
        print(f"  ✓ automation.{AUTOMATION_ID} created")
        return True
    return False


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify_entities(base_url: str, token: str) -> None:
    """Check that all entities exist and print their states."""
    print("\nVerifying entities ...")
    entities = [
        ANTLIA_OUTDOOR,
        DAIKIN_AP_OUTDOOR,
        CLIMATE_ENTITY,
        BASE_OFFSET_ENTITY,
        SOLAR_CORRECTION_ENTITY,
        LAST_WRITE_ENTITY,
    ]
    for eid in entities:
        state = ha_request(base_url, token, "GET", f"/api/states/{eid}")
        if state:
            print(f"  ✓ {eid} = {state.get('state', '?')}")
        else:
            print(f"  ✗ {eid} not found!")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
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

    # Check prerequisite sensors exist
    for eid in (ANTLIA_OUTDOOR, DAIKIN_AP_OUTDOOR, CLIMATE_ENTITY):
        state = ha_request(base_url, token, "GET", f"/api/states/{eid}")
        if not state:
            print(f"✗ {eid} not found in HA! Aborting.")
            sys.exit(1)
        print(f"✓ {eid} = {state.get('state', '?')}")
    print()

    # Create helpers via WebSocket, automation via REST
    ok = True
    ok = create_base_offset(base_url, token) and ok
    ok = create_solar_correction(base_url, token) and ok
    ok = create_last_write(base_url, token) and ok
    ok = create_automation(base_url, token) and ok

    if ok:
        print("\n✓ All entities created successfully!")
    else:
        print("\n⚠ Some entities failed — check errors above")

    verify_entities(base_url, token)


if __name__ == "__main__":
    main()
