#!/usr/bin/env python3
"""
ventilation-setup.py — Set up periodic ventilation automations via HA REST API.

Creates/updates two automations that run bathroom ventilation fans hourly
to prevent back-draught smells. Each automation:
  - Triggers every hour at a fixed minute offset
  - Only runs during daytime (configurable)
  - Skips if the fan is already on (manual use)
  - Sets an input_boolean flag so the wall display can show auto vs manual
  - Guards against turning off a fan that was manually switched during the delay

Prerequisites:
  - input_boolean.ventilation_pink_auto must exist
  - input_boolean.ventilation_master_auto must exist
  (Create via HA UI or ha_client.ws_command_sync('input_boolean/create', ...))

Usage:
  .venv/bin/python shelly/ventilation-setup.py          # apply both automations
  .venv/bin/python shelly/ventilation-setup.py --dry-run # show what would be sent
"""

import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

# --- Config ---

HA_URL = "http://homeassistant.local:8123"

FANS = [
    {
        "id": "1771878202066",  # existing automation ID
        "alias": "Ροζ εξαερισμός",
        "switch": "switch.ventilation_pink",
        "auto_flag": "input_boolean.ventilation_pink_auto",
        "trigger_minute": "35",
        "start_time": "07:00:00",
        "end_time": "00:00:00",
    },
    {
        "id": "ventilation_master_hourly",
        "alias": "Master εξαερισμός",
        "switch": "switch.shelly2pmg3_8cbfea9e6e60_output_1",
        "auto_flag": "input_boolean.ventilation_master_auto",
        "trigger_minute": "55",
        "start_time": "08:55:00",
        "end_time": "00:00:00",
    },
]

DELAY_SECONDS = 60
# Guard threshold: only turn off if the switch hasn't been re-toggled
# during our delay (i.e. last_changed > 50s ago means we turned it on,
# not a human in the last few seconds).
GUARD_THRESHOLD = 50


def load_token():
    token = os.environ.get("HA_TOKEN")
    if token:
        return token.strip()
    for p in [Path(__file__).parent / ".ha-token", Path(__file__).parent.parent / ".ha-token"]:
        if p.exists():
            return p.read_text().strip()
    print("ERROR: No HA_TOKEN env var and no .ha-token file found")
    sys.exit(1)


HA_TOKEN = load_token()


def ha_api(method, endpoint, data=None):
    url = f"{HA_URL}{endpoint}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(
        url, data=body,
        headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:200]
        if e.code == 404:
            return None
        print(f"  ERROR: HA API {method} {endpoint} → {e.code}: {err_body}")
        sys.exit(1)


def build_automation(fan):
    return {
        "id": fan["id"],
        "alias": fan["alias"],
        "description": f"Run {fan['switch']} for {DELAY_SECONDS}s every hour to prevent back-draught",
        "triggers": [
            {"trigger": "time_pattern", "minutes": fan["trigger_minute"]},
        ],
        "conditions": [
            {
                "condition": "time",
                "after": fan["start_time"],
                "before": fan["end_time"],
            },
            {
                "condition": "state",
                "entity_id": fan["switch"],
                "state": "off",
            },
        ],
        "actions": [
            # Mark as automation-driven
            {
                "action": "input_boolean.turn_on",
                "target": {"entity_id": fan["auto_flag"]},
            },
            # Turn on the fan
            {
                "action": "switch.turn_on",
                "target": {"entity_id": fan["switch"]},
            },
            # Wait
            {
                "delay": {"seconds": DELAY_SECONDS},
            },
            # Clear the auto flag (always, even if guard stops turn-off)
            {
                "action": "input_boolean.turn_off",
                "target": {"entity_id": fan["auto_flag"]},
            },
            # Guard: only turn off if nobody manually toggled during the delay
            {
                "condition": "template",
                "value_template": (
                    "{{ (now() - states." + fan["switch"] + ".last_changed)"
                    ".total_seconds() > " + str(GUARD_THRESHOLD) + " }}"
                ),
            },
            # Turn off the fan
            {
                "action": "switch.turn_off",
                "target": {"entity_id": fan["switch"]},
            },
        ],
        "mode": "single",
    }


def main():
    dry_run = "--dry-run" in sys.argv

    for fan in FANS:
        config = build_automation(fan)
        print(f"\n{'[DRY RUN] ' if dry_run else ''}Automation: {fan['alias']}")
        print(f"  Switch:  {fan['switch']}")
        print(f"  Trigger: every hour at :{fan['trigger_minute']}")
        print(f"  Hours:   {fan['start_time']} – {fan['end_time']}")

        if dry_run:
            print(json.dumps(config, indent=2, ensure_ascii=False))
            continue

        # Check if automation already exists
        existing = ha_api("GET", f"/api/config/automation/config/{fan['id']}")
        verb = "Updating" if existing else "Creating"
        print(f"  {verb}...")

        result = ha_api("POST", f"/api/config/automation/config/{fan['id']}", config)
        if result is not None:
            print(f"  ✓ Done (result: {result})")
        else:
            print(f"  ✗ Failed")
            sys.exit(1)

    print("\nAll automations configured.")


if __name__ == "__main__":
    main()
