#!/usr/bin/env python3
"""Build a second-pass Shelly->light mapping after HA entity re-pairing.

Reads the existing local mapping file and live Home Assistant entities, then writes
an updated mapping file with replaced light entity IDs.

Usage:
  .venv/bin/python scripts/shelly/build-second-pass-mapping.py
  .venv/bin/python scripts/shelly/build-second-pass-mapping.py --write-ha

By default this only writes:
  scripts/shelly/smart-toggle-pairs-pass2.json

With --write-ha it also updates these automations in HA:
  - smart_toggle_shelly_zigbee
  - smart_mirror_shelly_zigbee
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from pathlib import Path

from ha_tools.ha_client import HAClient


HA_URL = "http://homeassistant.local:8123"
INPUT_FILE = Path(__file__).parent / "smart-toggle-pairs.json"
OUTPUT_FILE = Path(__file__).parent / "smart-toggle-pairs-pass2.json"
AUTOMATION_IDS = [
    "smart_toggle_shelly_zigbee",
    "smart_mirror_shelly_zigbee",
]


def load_token() -> str:
    token = os.environ.get("HA_TOKEN")
    if token:
        return token.strip()

    candidates = [
        Path(__file__).parent / ".ha-token",
        Path(__file__).parent.parent / ".ha-token",
        Path.cwd() / ".ha-token",
    ]
    for path in candidates:
        if path.exists():
            return path.read_text().strip()

    print("ERROR: No HA_TOKEN env var and no .ha-token file found")
    sys.exit(1)


def norm(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[_\-.]+", " ", value)
    return re.sub(r"\s+", " ", value)


def find_mapping_variable(action_list: list[dict]) -> tuple[int, str] | tuple[None, None]:
    for i, action in enumerate(action_list):
        variables = action.get("variables", {})
        if "shelly_to_lights" in variables:
            return i, "shelly_to_lights"
        if "shelly_to_light" in variables:
            return i, "shelly_to_light"
    return None, None


def choose_new_lights(pair: dict, existing_lights: set[str], lights_by_name: dict[str, list[str]]) -> list[str]:
    old_lights = pair.get("light_entities", [])
    input_entity = pair["input_entity"]
    input_name = norm(pair.get("input_friendly_name", ""))
    old_primary = old_lights[0] if old_lights else ""
    old_slug = norm(old_primary.split(".", 1)[1] if "." in old_primary else old_primary)
    friendly_name = norm(pair.get("light_friendly_name", ""))

    # Keep entities that still exist.
    kept = [entity_id for entity_id in old_lights if entity_id in existing_lights]
    if kept:
        return kept

    # Exact friendly name match from previous mapping metadata.
    if friendly_name and friendly_name in lights_by_name:
        return lights_by_name[friendly_name]

    # Room-specific deterministic rules for this installation.
    if "kid" in input_name or "paid" in input_name or "upn" in old_slug:
        if "light.paidiko_1" in existing_lights:
            return ["light.paidiko_1"]

    if "grapheio" in old_slug or "office" in input_name:
        if "light.grapheio_toikhos" in existing_lights:
            return ["light.grapheio_toikhos"]

    if "poluel" in old_slug or "arister" in old_slug:
        if "light.saloni_aristera" in existing_lights:
            return ["light.saloni_aristera"]

    if "trapezaria" in old_slug:
        if "light.kajplats_e27_ws_globe_1055lm" in existing_lights:
            return ["light.kajplats_e27_ws_globe_1055lm"]

    if "shelly2pmg4_7c2c677b223c_input_1" in input_entity:
        pink = [
            eid for eid in ["light.mpanio_roz_1", "light.pink_2"]
            if eid in existing_lights
        ]
        if pink:
            return pink

    if "shelly2pmg3_8cbfea9e6e60_input_1" in input_entity:
        master = [
            entity_id
            for entity_id in ["light.mpanio_master_1", "light.mpanio_master_2"]
            if entity_id in existing_lights
        ]
        if master:
            return master

    # Last-resort: return unchanged old IDs so nothing is dropped silently.
    return old_lights


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-ha", action="store_true", help="Also update HA automation mappings")
    args = parser.parse_args()

    token = load_token()
    ha = HAClient(HA_URL, token)

    if not INPUT_FILE.exists():
        print(f"ERROR: Missing input file: {INPUT_FILE}")
        sys.exit(1)

    pairs = json.loads(INPUT_FILE.read_text())
    all_states = ha.get_all_states() or []

    light_states = [s for s in all_states if s.get("entity_id", "").startswith("light.")]
    existing_lights = {s["entity_id"] for s in light_states}

    lights_by_name: dict[str, list[str]] = {}
    for state in light_states:
        friendly = norm(state.get("attributes", {}).get("friendly_name", ""))
        if not friendly:
            continue
        lights_by_name.setdefault(friendly, []).append(state["entity_id"])

    # Build a quick lookup: entity_id -> friendly_name from live state.
    light_friendly: dict[str, str] = {}
    for state in light_states:
        eid = state["entity_id"]
        fn = state.get("attributes", {}).get("friendly_name", "")
        if fn:
            light_friendly[eid] = fn

    updated_pairs = copy.deepcopy(pairs)
    print("Second-pass remap:")
    for pair in updated_pairs:
        old_lights = pair.get("light_entities", [])
        new_lights = choose_new_lights(pair, existing_lights, lights_by_name)
        pair["light_entities"] = new_lights

        # Refresh friendly name from the first new light entity.
        if new_lights and new_lights[0] in light_friendly:
            pair["light_friendly_name"] = light_friendly[new_lights[0]]

        old_str = ", ".join(old_lights) if old_lights else "<none>"
        new_str = ", ".join(new_lights) if new_lights else "<none>"
        print(f"- {pair['input_entity']}")
        print(f"  old: {old_str}")
        print(f"  new: {new_str}")

    OUTPUT_FILE.write_text(json.dumps(updated_pairs, indent=2, ensure_ascii=False) + "\n")
    print(f"\nWrote: {OUTPUT_FILE}")

    if not args.write_ha:
        return

    # Build input -> list[lights] mapping from pass2 file.
    pass2_mapping = {
        pair["input_entity"]: pair.get("light_entities", [])
        for pair in updated_pairs
    }

    for automation_id in AUTOMATION_IDS:
        cfg = ha._request("GET", f"/api/config/automation/config/{automation_id}")
        if not cfg or (isinstance(cfg, dict) and "message" in cfg):
            print(f"SKIP: automation not found: {automation_id}")
            continue

        actions = cfg.get("actions", cfg.get("action", []))
        idx, key = find_mapping_variable(actions)
        if idx is None:
            print(f"SKIP: no shelly mapping variable in {automation_id}")
            continue

        old_mapping = actions[idx]["variables"][key]
        new_mapping = {}
        for input_entity, old_value in old_mapping.items():
            replacement = pass2_mapping.get(input_entity)
            if replacement:
                new_mapping[input_entity] = replacement
            else:
                if isinstance(old_value, list):
                    new_mapping[input_entity] = old_value
                else:
                    new_mapping[input_entity] = [old_value]

        actions[idx]["variables"][key] = new_mapping
        if "actions" in cfg:
            cfg["actions"] = actions
        else:
            cfg["action"] = actions

        ha._request("POST", f"/api/config/automation/config/{automation_id}", cfg)
        print(f"Updated HA automation: {automation_id}")


if __name__ == "__main__":
    main()
