#!/usr/bin/env python3
"""View and edit the 4 light scenes (morning, afternoon, relax, lights_off).

Each scene targets the individual lights in the "Scene lights" group,
so you can set per-light brightness and color temperature.

Usage:
    python scripts/scenes/manage-light-scenes.py show
    python scripts/scenes/manage-light-scenes.py edit
    python scripts/scenes/manage-light-scenes.py apply

'show'  — display current scene settings per light.
'edit'  — dump a simplified JSON with per-light controls, ready to tweak.
'apply' — push edited JSON back to HA.
"""

import json
import sys
from pathlib import Path

from ha_tools.ha_client import HAClient

SCENES = ["scene.morning", "scene.afternoon", "scene.relax", "scene.lights_off"]
LIGHT_GROUP = "light.main_lights"
EDIT_FILE = Path(__file__).parent / "light-scenes.json"

TOKEN = Path(Path(__file__).resolve().parents[2] / ".ha-token").read_text().strip()
ha = HAClient("http://homeassistant.local:8123", TOKEN)


def _get_group_members() -> list[str]:
    """Get ordered list of entity_ids in the Scene lights group."""
    state = ha.get_state(LIGHT_GROUP)
    return state["attributes"].get("entity_id", []) if state else []


def _get_light_info() -> dict[str, dict]:
    """Get friendly names and supported color modes for each light."""
    members = _get_group_members()
    info = {}
    for eid in members:
        s = ha.get_state(eid)
        if s:
            a = s["attributes"]
            info[eid] = {
                "friendly_name": a.get("friendly_name", eid),
                "has_color_temp": "color_temp" in a.get("supported_color_modes", []),
            }
    return info


def _simplify(config: dict, light_info: dict) -> dict:
    """Convert scene config to per-light simplified format."""
    simple = {"name": config["name"].strip()}

    # Build a comment showing light names
    simple["_lights"] = {eid: info["friendly_name"] for eid, info in light_info.items()}

    simple["lights"] = {}
    entities = config.get("entities", {})

    for eid, info in light_info.items():
        if eid in entities:
            attrs = entities[eid]
            entry = {"state": attrs.get("state", "on")}
            if attrs.get("brightness") is not None:
                entry["brightness"] = attrs["brightness"]
            if info["has_color_temp"] and attrs.get("color_temp_kelvin") is not None:
                entry["color_temp_kelvin"] = attrs["color_temp_kelvin"]
            simple["lights"][eid] = entry
        else:
            # Light not in scene yet — check if group entity has it
            group_attrs = entities.get(LIGHT_GROUP, {})
            group_members = group_attrs.get("entity_id", [])
            if eid in group_members:
                # Inherit from group settings
                entry = {"state": group_attrs.get("state", "on")}
                if group_attrs.get("brightness") is not None:
                    entry["brightness"] = group_attrs["brightness"]
                if info["has_color_temp"] and group_attrs.get("color_temp_kelvin") is not None:
                    entry["color_temp_kelvin"] = group_attrs["color_temp_kelvin"]
                simple["lights"][eid] = entry
            else:
                # Not referenced at all — default off
                simple["lights"][eid] = {"state": "off"}

    return simple


def _build_scene_config(original: dict, simplified: dict, light_info: dict) -> dict:
    """Build a full scene config from simplified per-light format."""
    config = {
        "id": original["id"],
        "name": simplified["name"],
        "entities": {},
        "metadata": {},
    }

    for eid, attrs in simplified["lights"].items():
        entity_config = {"state": attrs["state"]}

        if attrs["state"] == "on":
            # Fetch current state to fill in required attributes
            current = ha.get_state(eid)
            if current:
                ca = current["attributes"]
                entity_config["supported_color_modes"] = ca.get("supported_color_modes", [])
                entity_config["friendly_name"] = ca.get("friendly_name", eid)
                entity_config["supported_features"] = ca.get("supported_features", 0)

                if "brightness" in attrs:
                    entity_config["brightness"] = attrs["brightness"]
                    entity_config["color_mode"] = ca.get("supported_color_modes", ["brightness"])[0]

                if "color_temp_kelvin" in attrs:
                    entity_config["color_temp_kelvin"] = attrs["color_temp_kelvin"]
                    entity_config["color_mode"] = "color_temp"
        else:
            current = ha.get_state(eid)
            if current:
                ca = current["attributes"]
                entity_config["supported_color_modes"] = ca.get("supported_color_modes", [])
                entity_config["friendly_name"] = ca.get("friendly_name", eid)
                entity_config["supported_features"] = ca.get("supported_features", 0)
                entity_config["color_mode"] = None
                entity_config["brightness"] = None

        config["entities"][eid] = entity_config
        config["metadata"][eid] = {"entity_only": True}

    return config


def cmd_show():
    light_info = _get_light_info()

    print("Light Scenes — Per-Light Summary")
    print("=" * 70)
    # Header
    names = {eid: info["friendly_name"] for eid, info in light_info.items()}
    for sid in SCENES:
        config = ha.get_scene_config(sid)
        if not config:
            print(f"\n{sid}: FAILED TO LOAD")
            continue
        simple = _simplify(config, light_info)
        print(f"\n{sid} — \"{simple['name']}\"")
        for eid, attrs in simple["lights"].items():
            name = names.get(eid, eid)
            state = attrs.get("state", "?")
            if state == "off":
                print(f"  {name:30s}  OFF")
            else:
                brightness = attrs.get("brightness")
                kelvin = attrs.get("color_temp_kelvin")
                parts = []
                if brightness is not None:
                    pct = round(brightness / 255 * 100)
                    parts.append(f"{pct:3d}%")
                if kelvin is not None:
                    parts.append(f"{kelvin}K")
                print(f"  {name:30s}  {', '.join(parts)}")


def cmd_edit():
    light_info = _get_light_info()

    simplified = {}
    for sid in SCENES:
        config = ha.get_scene_config(sid)
        if not config:
            print(f"ERROR: Could not load {sid}")
            return 1
        simplified[sid] = _simplify(config, light_info)

    EDIT_FILE.write_text(json.dumps(simplified, indent=2) + "\n")
    print(f"Wrote editable config to: {EDIT_FILE}")
    print()
    print("Each scene has per-light entries. Edit brightness (0-255) and color_temp_kelvin.")
    print("Set state to 'off' to turn a light off in that scene.")
    print()
    print(f"Then run:  python {sys.argv[0]} apply")
    return 0


def cmd_apply():
    if not EDIT_FILE.exists():
        print(f"No edit file found at {EDIT_FILE}")
        print(f"Run 'python {sys.argv[0]} edit' first.")
        return 1

    edited = json.loads(EDIT_FILE.read_text())
    light_info = _get_light_info()

    for sid in SCENES:
        if sid not in edited:
            print(f"WARNING: {sid} not in edited file, skipping")
            continue

        original = ha.get_scene_config(sid)
        if not original:
            print(f"ERROR: Could not load {sid} from HA")
            return 1

        updated = _build_scene_config(original, edited[sid], light_info)
        ok = ha.update_scene_config(sid, updated)
        if ok:
            print(f"  Updated {sid} ✓")
        else:
            print(f"  FAILED to update {sid}")
            return 1

    print("\nAll scenes updated. You can delete the edit file:")
    print(f"  rm {EDIT_FILE}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("show", "edit", "apply"):
        print("Usage: python manage-light-scenes.py {show|edit|apply}")
        print("  show  — display current scene settings")
        print("  edit  — dump editable JSON file")
        print("  apply — push edited JSON back to HA")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "show":
        cmd_show()
    elif cmd == "edit":
        sys.exit(cmd_edit())
    elif cmd == "apply":
        sys.exit(cmd_apply())
