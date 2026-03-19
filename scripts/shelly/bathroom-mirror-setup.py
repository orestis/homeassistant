#!/usr/bin/env python3
"""
bathroom-mirror-setup.py — Set up bathroom mirror-mode + split automations.

This script:
  1. Detaches ventilation outputs (physical switch goes through HA, not direct relay)
  2. Splits the single smart_toggle_shelly_zigbee automation into TWO:
     a. Mirror automation (parallel) — bathroom lights + ventilation.
        Input ON → on, Input OFF → off. Self-correcting, no race conditions.
     b. Toggle automation (queued) — all other rooms.
        Any light on → off, all off → on. Queued to prevent rapid-tap races.
  3. Flips master bathroom input mapping (input_0 ↔ input_1) so left=vent,
     right=lights matches pink bathroom layout.
  4. Updates the local smart-toggle-pairs.json file.

Light settings (brightness, color_temp) are keyed by INPUT entity, not individual
lights. One switch → one set of settings for all lights on that switch.

Usage:
  .venv/bin/python shelly/bathroom-mirror-setup.py          # apply all changes
  .venv/bin/python shelly/bathroom-mirror-setup.py --dry-run # show what would change

Prerequisites:
  - Pink bathroom Shelly 2PM Gen4 at .181 (output 0 = vent, output 1 = lights)
  - Master bathroom Shelly 2PM Gen3 at .194 (output 0 = lights, output 1 = vent)
  - Existing smart_toggle_shelly_zigbee automation
  - wall-display/ha_client.py available
"""

import json
import os
import sys
from pathlib import Path

from ha_tools.ha_client import HAClient
from ha_tools import shelly_client

# --- Config ---

HA_URL = "http://homeassistant.local:8123"
MIRROR_LIGHT_ID = "smart_mirror_shelly_zigbee"
TOGGLE_LIGHT_ID = "smart_toggle_shelly_zigbee"
VENT_MIRROR_ID = "ventilation_mirror_switches"

DEFAULT_BRIGHTNESS_PCT = 75
DEFAULT_COLOR_TEMP_KELVIN = 2700


def load_token():
    token = os.environ.get("HA_TOKEN")
    if token:
        return token.strip()
    for p in [Path(__file__).parent / ".ha-token", Path(__file__).parent.parent / ".ha-token"]:
        if p.exists():
            return p.read_text().strip()
    print("ERROR: No HA_TOKEN env var and no .ha-token file found")
    sys.exit(1)


# --- Bathroom definitions ---

PINK = {
    "name": "pink",
    "shelly_ip": "192.168.1.181",
    "vent_output": 0,
    "vent_input": "binary_sensor.shelly2pmg4_7c2c677b223c_input_0",
    "vent_switch": "switch.ventilation_pink",
    "light_input": "binary_sensor.shelly2pmg4_7c2c677b223c_input_1",
    "light_entities": [
        "light.mpanio_roz_spot_eisodos",
        "light.mpanio_roz_spot_ntouz",
    ],
}

# Master: inputs flipped so left=vent, right=lights matches pink
MASTER = {
    "name": "master",
    "shelly_ip": "192.168.1.194",
    "vent_output": 1,
    "vent_input": "binary_sensor.shelly2pmg3_8cbfea9e6e60_input_0",
    "vent_switch": "switch.shelly2pmg3_8cbfea9e6e60_output_1",
    "light_input": "binary_sensor.shelly2pmg3_8cbfea9e6e60_input_1",
    "light_entities": [
        "light.master_mpanio_eisodos",
        "light.master_mpanio_ntouz",
    ],
}

BATHROOMS = [PINK, MASTER]

# Per-input light settings (overrides defaults). Keyed by input entity.
INPUT_SETTINGS = {
    PINK["light_input"]: {
        "brightness_pct": 100,
        "color_temp_kelvin": 2700,
    },
    # Master bathroom uses defaults (75%, 2700K)
}


# --- Step 1: Detach ventilation outputs ---

def step_detach_ventilation(dry_run=False):
    """Detach ventilation outputs so physical switch goes through HA."""
    print("\n=== Step 1: Detach ventilation outputs ===\n")
    for bath in BATHROOMS:
        ip = bath["shelly_ip"]
        oid = bath["vent_output"]
        print(f"  {bath['name']}: {ip} output {oid} ({bath['vent_switch']})")

        if dry_run:
            print(f"    [DRY RUN] Would detach and ensure relay ON")
            continue

        result = shelly_client.detach(ip, oid)
        for change in result["changes"]:
            print(f"    ✓ {change}")


# --- Step 2: Split into mirror + toggle automations ---

def step_split_automations(ha, dry_run=False):
    """Read the existing automation, split into mirror (parallel) + toggle (queued)."""
    print("\n=== Step 2: Split into mirror + toggle automations ===\n")

    # Fetch existing mapping
    config = ha._request("GET", f"/api/config/automation/config/{TOGGLE_LIGHT_ID}")
    if not config or "message" in config:
        print("  ERROR: Smart toggle automation not found!")
        sys.exit(1)

    old_mapping = {}
    for action in config.get("actions", config.get("action", [])):
        if "variables" in action and "shelly_to_lights" in action["variables"]:
            old_mapping = action["variables"]["shelly_to_lights"]
            break

    if not old_mapping:
        print("  ERROR: Cannot find shelly_to_lights in automation variables!")
        sys.exit(1)

    # Flip master bathroom: input_0 → input_1 for lights
    old_master_input = "binary_sensor.shelly2pmg3_8cbfea9e6e60_input_0"
    new_master_input = MASTER["light_input"]
    if old_master_input in old_mapping:
        old_mapping[new_master_input] = old_mapping.pop(old_master_input)
        print(f"  Flipped master lights: {old_master_input} → {new_master_input}")

    # Split: mirror inputs go to mirror automation, rest stay in toggle
    mirror_inputs = {bath["light_input"] for bath in BATHROOMS}
    mirror_mapping = {}
    toggle_mapping = {}
    for inp, lights in old_mapping.items():
        if inp in mirror_inputs:
            mirror_mapping[inp] = lights
        else:
            toggle_mapping[inp] = lights

    print(f"  Mirror inputs ({len(mirror_mapping)}): {list(mirror_mapping.keys())}")
    print(f"  Toggle inputs ({len(toggle_mapping)}): {list(toggle_mapping.keys())}")

    # --- Build mirror automation (parallel, safe for rapid presses) ---
    mirror_config = {
        "id": MIRROR_LIGHT_ID,
        "alias": "Smart Mirror - Shelly to Zigbee (Bathrooms)",
        "description": (
            "Mirror mode for bathroom lights. Switch ON → lights ON, "
            "switch OFF → lights OFF. Self-correcting: re-syncs on every flip. "
            "Parallel mode is safe because we only read trigger.to_state, "
            "not current light state."
        ),
        "mode": "parallel",
        "max": 10,
        "triggers": [
            {
                "platform": "state",
                "entity_id": list(mirror_mapping.keys()),
            }
        ],
        "actions": [
            {
                "variables": {
                    "shelly_to_lights": mirror_mapping,
                    "input_settings": {k: v for k, v in INPUT_SETTINGS.items() if k in mirror_mapping},
                    "default_brightness_pct": DEFAULT_BRIGHTNESS_PCT,
                    "default_color_temp_kelvin": DEFAULT_COLOR_TEMP_KELVIN,
                }
            },
            {
                "condition": "template",
                "value_template": "{{ shelly_to_lights.get(trigger.entity_id, []) | length > 0 }}",
            },
            {
                "choose": [
                    {
                        "conditions": [{
                            "condition": "template",
                            "value_template": "{{ trigger.to_state.state == 'on' }}",
                        }],
                        "sequence": [{
                            "action": "light.turn_on",
                            "target": {
                                "entity_id": "{{ shelly_to_lights.get(trigger.entity_id, []) }}",
                            },
                            "data": {
                                "transition": 0,
                                "brightness_pct": "{{ input_settings.get(trigger.entity_id, {}).get('brightness_pct', default_brightness_pct) }}",
                                "color_temp_kelvin": "{{ input_settings.get(trigger.entity_id, {}).get('color_temp_kelvin', default_color_temp_kelvin) }}",
                            },
                        }],
                    },
                ],
                "default": [{
                    "action": "light.turn_off",
                    "target": {
                        "entity_id": "{{ shelly_to_lights.get(trigger.entity_id, []) }}",
                    },
                    "data": {"transition": 0},
                }],
            },
        ],
    }

    # --- Build toggle automation (queued, prevents rapid-tap races) ---
    toggle_config = {
        "id": TOGGLE_LIGHT_ID,
        "alias": "Smart Toggle - Shelly to Zigbee",
        "description": (
            "Toggle mode for non-bathroom lights. Any light on → all off, "
            "all off → turn on with defaults. Queued mode ensures sequential "
            "execution so rapid presses don't race."
        ),
        "mode": "queued",
        "max": 10,
        "triggers": [
            {
                "platform": "state",
                "entity_id": list(toggle_mapping.keys()),
            }
        ],
        "actions": [
            {
                "variables": {
                    "shelly_to_lights": toggle_mapping,
                    "input_settings": {k: v for k, v in INPUT_SETTINGS.items() if k in toggle_mapping},
                    "default_brightness_pct": DEFAULT_BRIGHTNESS_PCT,
                    "default_color_temp_kelvin": DEFAULT_COLOR_TEMP_KELVIN,
                }
            },
            {
                "condition": "template",
                "value_template": "{{ shelly_to_lights.get(trigger.entity_id, []) | length > 0 }}",
            },
            {
                "choose": [
                    {
                        "conditions": [{
                            "condition": "template",
                            "value_template": '{{ shelly_to_lights.get(trigger.entity_id, []) | select("is_state", "on") | list | count > 0 }}',
                        }],
                        "sequence": [{
                            "action": "light.turn_off",
                            "target": {
                                "entity_id": "{{ shelly_to_lights.get(trigger.entity_id, []) }}",
                            },
                            "data": {"transition": 0},
                        }],
                    },
                ],
                "default": [{
                    "action": "light.turn_on",
                    "target": {
                        "entity_id": "{{ shelly_to_lights.get(trigger.entity_id, []) }}",
                    },
                    "data": {
                        "transition": 0,
                        "brightness_pct": "{{ input_settings.get(trigger.entity_id, {}).get('brightness_pct', default_brightness_pct) }}",
                        "color_temp_kelvin": "{{ input_settings.get(trigger.entity_id, {}).get('color_temp_kelvin', default_color_temp_kelvin) }}",
                    },
                }],
            },
        ],
    }

    if dry_run:
        print("\n  [DRY RUN] Mirror automation:")
        print(json.dumps(mirror_config, indent=2, ensure_ascii=False))
        print("\n  [DRY RUN] Toggle automation:")
        print(json.dumps(toggle_config, indent=2, ensure_ascii=False))
        return

    ha._request("POST", f"/api/config/automation/config/{MIRROR_LIGHT_ID}", mirror_config)
    print(f"  ✓ Mirror automation created ({MIRROR_LIGHT_ID})")

    ha._request("POST", f"/api/config/automation/config/{TOGGLE_LIGHT_ID}", toggle_config)
    print(f"  ✓ Toggle automation updated ({TOGGLE_LIGHT_ID})")


# --- Step 3: Create ventilation mirror automation ---

def step_create_vent_mirror(ha, dry_run=False):
    """Create a mirror automation for ventilation switches."""
    print("\n=== Step 3: Create ventilation mirror automation ===\n")

    vent_mapping = {}
    for bath in BATHROOMS:
        vent_mapping[bath["vent_input"]] = bath["vent_switch"]

    config = {
        "id": VENT_MIRROR_ID,
        "alias": "Ventilation Mirror - Switch to Fan",
        "description": (
            "Mirror bathroom ventilation switches: input ON → fan ON, "
            "input OFF → fan OFF. Fans are in detached mode so the hourly "
            "automation can control them independently."
        ),
        "mode": "parallel",
        "max": 10,
        "triggers": [
            {
                "platform": "state",
                "entity_id": list(vent_mapping.keys()),
            }
        ],
        "actions": [
            {
                "variables": {
                    "input_to_switch": vent_mapping,
                }
            },
            {
                "condition": "template",
                "value_template": "{{ input_to_switch.get(trigger.entity_id, '') != '' }}",
            },
            {
                "choose": [
                    {
                        "conditions": [{
                            "condition": "template",
                            "value_template": "{{ trigger.to_state.state == 'on' }}",
                        }],
                        "sequence": [{
                            "action": "switch.turn_on",
                            "target": {"entity_id": "{{ input_to_switch[trigger.entity_id] }}"},
                        }],
                    },
                ],
                "default": [{
                    "action": "switch.turn_off",
                    "target": {"entity_id": "{{ input_to_switch[trigger.entity_id] }}"},
                }],
            },
        ],
    }

    if dry_run:
        print("  [DRY RUN] Would create ventilation mirror automation:")
        print(json.dumps(config, indent=2, ensure_ascii=False))
        return

    ha._request("POST", f"/api/config/automation/config/{VENT_MIRROR_ID}", config)
    print(f"  ✓ Ventilation mirror automation created/updated")
    for inp, sw in vent_mapping.items():
        print(f"    {inp} → {sw}")


# --- Step 4: Update local pairs file ---

def step_update_pairs_file(dry_run=False):
    """Update smart-toggle-pairs.json with the flipped master input."""
    print("\n=== Step 4: Update local pairs file ===\n")

    pairs_file = Path(__file__).parent / "smart-toggle-pairs.json"
    if not pairs_file.exists():
        print("  Pairs file not found, skipping.")
        return

    pairs = json.loads(pairs_file.read_text())
    changed = False

    for pair in pairs:
        if pair["input_entity"] == "binary_sensor.shelly2pmg3_8cbfea9e6e60_input_0":
            light_entities = pair.get("light_entities", [])
            if "light.master_mpanio_eisodos" in light_entities:
                if dry_run:
                    print(f"  [DRY RUN] Would flip master input: input_0 → input_1")
                else:
                    pair["input_entity"] = "binary_sensor.shelly2pmg3_8cbfea9e6e60_input_1"
                    changed = True
                    print(f"  Flipped master bathroom input: input_0 → input_1")

    if changed:
        pairs_file.write_text(json.dumps(pairs, indent=2, ensure_ascii=False) + "\n")
        print(f"  ✓ Saved {pairs_file.name}")
    elif not dry_run:
        print(f"  No changes needed.")


# --- Main ---

def main():
    dry_run = "--dry-run" in sys.argv

    token = load_token()
    ha = HAClient(HA_URL, token)

    if dry_run:
        print("=== DRY RUN — no changes will be made ===")

    step_detach_ventilation(dry_run)
    step_split_automations(ha, dry_run)
    step_create_vent_mirror(ha, dry_run)
    step_update_pairs_file(dry_run)

    print("\n=== Done ===")
    if not dry_run:
        print("\nSummary:")
        print("  - Ventilation outputs detached on both bathroom Shellys")
        print("  - Mirror automation (parallel): bathroom lights — ON=on, OFF=off")
        print("  - Toggle automation (queued): other rooms — toggle with race protection")
        print("  - Ventilation mirror automation: physical switch mirrors fan state")
        print("  - Master bathroom inputs flipped (left=vent, right=lights)")
        print(f"  - Pink bathroom lights: 100% brightness, {DEFAULT_COLOR_TEMP_KELVIN}K")
        print(f"  - All other lights: {DEFAULT_BRIGHTNESS_PCT}% brightness, {DEFAULT_COLOR_TEMP_KELVIN}K")


if __name__ == "__main__":
    main()
