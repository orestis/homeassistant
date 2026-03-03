#!/usr/bin/env python3
"""
smart-toggle-setup.py — Configure Shelly + HA for smart toggle (edge mode)

A single script to set up Shelly relays in detached mode and pair them
with IKEA Zigbee bulbs via Home Assistant automations.

Usage:
  ./smart-toggle-setup.py setup   <shelly> <light> [output-id]   Full setup: detach + add pair
  ./smart-toggle-setup.py status  <shelly> [output-id]           Show Shelly config/status
  ./smart-toggle-setup.py detach  <shelly> [output-id]           Set Shelly to detached mode
  ./smart-toggle-setup.py revert  <shelly> [output-id]           Revert to follow (dumb) mode
  ./smart-toggle-setup.py add-pair <shelly> <light> [output-id]  Add pair to HA automation
  ./smart-toggle-setup.py show-pairs                             Show all configured pairs
  ./smart-toggle-setup.py sync-pairs                             Download pairs from HA to local file
  ./smart-toggle-setup.py test    <light>                        Toggle a light via HA

<shelly> can be an IP address or a name (matched against HA device names).
<light>  can be an entity_id (light.xxx) or a friendly name.
"""

import json
import sys
import os
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

# --- Config ---

HA_URL = "http://homeassistant.local:8123"
AUTOMATION_ID = "smart_toggle_shelly_zigbee"
PAIRINGS_FILE = Path(__file__).parent / "smart-toggle-pairs.json"


def load_token():
    """Load HA token from env var or .ha-token file."""
    token = os.environ.get("HA_TOKEN")
    if token:
        return token.strip()

    token_file = Path(__file__).parent / ".ha-token"
    if token_file.exists():
        return token_file.read_text().strip()

    print("ERROR: No HA_TOKEN env var and no .ha-token file found")
    sys.exit(1)


HA_TOKEN = load_token()


# --- HTTP helpers ---

def shelly_rpc(ip, method, params=None):
    """Call a Shelly RPC method and return the result."""
    payload = {"id": 1, "method": method}
    if params:
        payload["params"] = params

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://{ip}/rpc",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"  ERROR: Cannot reach Shelly at {ip}: {e}")
        sys.exit(1)

    if "error" in result:
        print(f"  ERROR: Shelly RPC {method} failed: {result['error']}")
        sys.exit(1)

    return result.get("result", result)


def ha_api(method, endpoint, data=None):
    """Call the HA REST API."""
    url = f"{HA_URL}{endpoint}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {HA_TOKEN}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  ERROR: HA API {method} {endpoint} → {e.code}: {body}")
        sys.exit(1)
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"  ERROR: Cannot reach HA at {HA_URL}: {e}")
        sys.exit(1)


def ha_template(template):
    """Evaluate a Jinja2 template via HA API and return the string result."""
    url = f"{HA_URL}/api/template"
    body = json.dumps({"template": template}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {HA_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode().strip()


# --- Local pairings file ---

def load_pairings():
    """Load the local pairings file. Returns a list of pair dicts."""
    if not PAIRINGS_FILE.exists():
        return []
    with open(PAIRINGS_FILE) as f:
        return json.load(f)


def save_pairings(pairs):
    """Write the pairings list to the local file."""
    with open(PAIRINGS_FILE, "w") as f:
        json.dump(pairs, f, indent=2, ensure_ascii=False)
    print(f"  ✓ Saved {len(pairs)} pair(s) to {PAIRINGS_FILE.name}")


def enrich_pair(input_entity, light_entity):
    """Build a rich pair dict with IDs and friendly names."""
    pair = {
        "input_entity": input_entity,
        "light_entity": light_entity,
    }

    # Resolve input entity friendly name + Shelly device info
    try:
        input_state = ha_api("GET", f"/api/states/{input_entity}")
        pair["input_friendly_name"] = input_state.get("attributes", {}).get("friendly_name", "")
    except SystemExit:
        pair["input_friendly_name"] = ""

    # Resolve Shelly device name and IP via device registry
    tpl_shelly = (
        "{% set did = device_id('" + input_entity + "') %}"
        "{% if did %}"
        "{{ device_attr(did, 'name') }}|||{{ device_attr(did, 'configuration_url') }}"
        "{% endif %}"
    )
    try:
        shelly_info = ha_template(tpl_shelly)
        dname, _, curl = shelly_info.partition("|||")
        pair["shelly_device_name"] = dname.strip()
        if curl:
            parsed = urllib.parse.urlparse(curl.strip())
            pair["shelly_ip"] = parsed.hostname or ""
        else:
            pair["shelly_ip"] = ""
    except Exception:
        pair["shelly_device_name"] = ""
        pair["shelly_ip"] = ""

    # Resolve light friendly name
    try:
        light_state = ha_api("GET", f"/api/states/{light_entity}")
        pair["light_friendly_name"] = light_state.get("attributes", {}).get("friendly_name", "")
    except SystemExit:
        pair["light_friendly_name"] = ""

    return pair


def upsert_pair_in_file(input_entity, light_entity):
    """Add or update a pair in the local pairings file."""
    pairs = load_pairings()
    new_pair = enrich_pair(input_entity, light_entity)

    # Replace existing pair with same input_entity, or append
    replaced = False
    for i, p in enumerate(pairs):
        if p["input_entity"] == input_entity:
            pairs[i] = new_pair
            replaced = True
            break
    if not replaced:
        pairs.append(new_pair)

    save_pairings(pairs)


# --- Resolvers ---

def resolve_shelly(identifier):
    """Resolve a Shelly IP or HA device name to an IP address.

    Returns (ip, device_name) tuple. device_name may be None if given a raw IP.
    """
    # If it looks like an IP, return as-is
    parts = identifier.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        return identifier, None

    # Search HA for a Shelly device matching this name.
    # HA device names are raw Shelly IDs (e.g. "shelly2pmg4-7c2c677b4524"),
    # but users refer to them by their entity friendly names (e.g. "lights-kid-room").
    # So we search: device name, entity_id (slugified), and entity friendly_name.
    search_slug = identifier.lower().replace("-", "_")  # for entity_id matching
    search_lower = identifier.lower()

    template = (
        "{% set ns = namespace(found='', name='') %}"
        "{% for state in states %}"
        "{% set did = device_id(state.entity_id) %}"
        "{% if did %}"
        "{% set mfr = device_attr(did, 'manufacturer') | default('') | lower %}"
        "{% if 'shelly' in mfr %}"
        "{% set dname = device_attr(did, 'name') | default('') %}"
        "{% set fname = state.attributes.get('friendly_name', '') %}"
        "{% set eid = state.entity_id | lower %}"
        "{% set fname_norm = fname | lower | replace('-', '_') %}"
        "{% if ('" + search_lower + "' in dname | lower"
        " or '" + search_slug + "' in eid"
        " or '" + search_slug + "' in fname_norm"
        " or '" + search_lower + "' in fname | lower"
        ") and ns.found == '' %}"
        "{% set config_url = device_attr(did, 'configuration_url') %}"
        "{% if config_url %}"
        "{% set ns.found = config_url %}"
        "{% set ns.name = dname if dname else fname %}"
        "{% endif %}"
        "{% endif %}"
        "{% endif %}"
        "{% endif %}"
        "{% endfor %}"
        "{{ ns.found }}|||{{ ns.name }}"
    )
    result = ha_template(template)
    url_str, _, name = result.partition("|||")

    if not url_str or url_str == "None":
        print(f"  ERROR: No Shelly device matching '{identifier}' found in HA")
        print(f"         Searched device names, entity IDs, and friendly names.")
        sys.exit(1)

    parsed = urllib.parse.urlparse(url_str)
    ip = parsed.hostname
    print(f"  Resolved '{identifier}' → {name} ({ip})")
    return ip, name


def resolve_light(identifier):
    """Resolve a light entity ID or friendly name to an entity_id.

    Returns (entity_id, friendly_name) tuple.
    """
    # If it already looks like an entity ID
    if identifier.startswith("light."):
        state = ha_api("GET", f"/api/states/{identifier}")
        name = state.get("attributes", {}).get("friendly_name", identifier)
        return identifier, name

    # Search by friendly name
    template = (
        "{% set ns = namespace(found='', name='') %}"
        "{% for state in states %}"
        "{% if state.entity_id.startswith('light.') %}"
        "{% set fname = state.attributes.get('friendly_name', '') %}"
        "{% if '" + identifier.lower().replace("'", "") + "' in fname | lower and ns.found == '' %}"
        "{% set ns.found = state.entity_id %}"
        "{% set ns.name = fname %}"
        "{% endif %}"
        "{% endif %}"
        "{% endfor %}"
        "{{ ns.found }}|||{{ ns.name }}"
    )
    result = ha_template(template)
    entity_id, _, name = result.partition("|||")

    if not entity_id or entity_id == "None":
        print(f"  ERROR: No light entity matching '{identifier}' found in HA")
        sys.exit(1)

    print(f"  Resolved '{identifier}' → {name} ({entity_id})")
    return entity_id, name


def resolve_shelly_input_entity(ip, output_id):
    """Given a Shelly IP and output ID, find the corresponding HA binary_sensor input entity.

    The entity is typically named like binary_sensor.<device_name>_input_<output_id>.
    """
    # Get device info to find the Shelly device ID
    info = shelly_rpc(ip, "Shelly.GetDeviceInfo")
    shelly_id = info["id"]  # e.g., "shelly1g4-7c2c677f6410"
    mac = info["mac"]       # e.g., "7C2C677F6410"

    # Search HA for a binary_sensor input entity matching this device
    template = (
        "{% set ns = namespace(found='') %}"
        "{% for state in states %}"
        "{% if state.entity_id.startswith('binary_sensor.') "
        "and 'input_" + str(output_id) + "' in state.entity_id "
        "and ('" + mac.lower() + "' in state.entity_id or '" + shelly_id.replace('-', '_') + "' in state.entity_id) %}"
        "{% set ns.found = state.entity_id %}"
        "{% endif %}"
        "{% endfor %}"
        "{{ ns.found }}"
    )
    result = ha_template(template)

    if not result or result == "None":
        # Broader search: look for any binary_sensor with the MAC in it
        template2 = (
            "{% set ns = namespace(found='') %}"
            "{% for state in states %}"
            "{% if state.entity_id.startswith('binary_sensor.') "
            "and 'input_" + str(output_id) + "' in state.entity_id %}"
            "{% set did = device_id(state.entity_id) %}"
            "{% if did %}"
            "{% set ids = device_attr(did, 'identifiers') | string | lower %}"
            "{% if '" + mac.lower() + "' in ids and ns.found == '' %}"
            "{% set ns.found = state.entity_id %}"
            "{% endif %}"
            "{% endif %}"
            "{% endif %}"
            "{% endfor %}"
            "{{ ns.found }}"
        )
        result = ha_template(template2)

    if not result or result == "None":
        print(f"  ERROR: Cannot find HA input entity for Shelly {shelly_id} input {output_id}")
        print(f"         Is this device added to HA via the Shelly integration (not just MQTT)?")
        sys.exit(1)

    print(f"  Found input entity: {result}")
    return result


# --- Commands ---

def cmd_status(args):
    """Show Shelly config and status."""
    if not args:
        print("Usage: status <shelly-ip-or-name> [output-id]")
        sys.exit(1)

    ip, name = resolve_shelly(args[0])
    oid = int(args[1]) if len(args) > 1 else 0

    print(f"\n=== Shelly at {ip}, Output {oid} ===\n")

    print("--- Device Info ---")
    info = shelly_rpc(ip, "Shelly.GetDeviceInfo")
    print(f"  ID:       {info['id']}")
    print(f"  Model:    {info.get('model', '?')}")
    print(f"  App:      {info.get('app', '?')}")
    print(f"  Firmware: {info.get('ver', '?')}")
    print(f"  Gen:      {info.get('gen', '?')}")

    print("\n--- Switch Config ---")
    cfg = shelly_rpc(ip, "Switch.GetConfig", {"id": oid})
    print(f"  in_mode:       {cfg['in_mode']}")
    print(f"  initial_state: {cfg['initial_state']}")
    print(f"  auto_on:       {cfg['auto_on']}")
    print(f"  auto_off:      {cfg['auto_off']}")

    print("\n--- Switch Status ---")
    status = shelly_rpc(ip, "Switch.GetStatus", {"id": oid})
    print(f"  output: {'ON' if status['output'] else 'OFF'}")
    print(f"  source: {status.get('source', '?')}")
    temp = status.get("temperature", {})
    if temp:
        print(f"  temp:   {temp.get('tC', '?')}°C")

    print("\n--- Input Config ---")
    icfg = shelly_rpc(ip, "Input.GetConfig", {"id": oid})
    print(f"  type:          {icfg['type']}")
    print(f"  enable:        {icfg['enable']}")
    print(f"  invert:        {icfg['invert']}")
    print(f"  factory_reset: {icfg['factory_reset']}")

    print("\n--- Input Status ---")
    ist = shelly_rpc(ip, "Input.GetStatus", {"id": oid})
    print(f"  state: {'ON' if ist.get('state') else 'OFF'}")


def cmd_detach(args):
    """Set Shelly output to detached mode and ensure relay is ON."""
    if not args:
        print("Usage: detach <shelly-ip-or-name> [output-id]")
        sys.exit(1)

    ip, _ = resolve_shelly(args[0])
    oid = int(args[1]) if len(args) > 1 else 0

    # Check current mode
    cfg = shelly_rpc(ip, "Switch.GetConfig", {"id": oid})
    if cfg["in_mode"] == "detached":
        print(f"  Already in detached mode (output {oid}). Nothing to do.")
    else:
        print(f"  Current mode: {cfg['in_mode']}")
        print(f"  Setting output {oid} to detached mode...")
        shelly_rpc(ip, "Switch.SetConfig", {
            "id": oid,
            "config": {"in_mode": "detached", "initial_state": "restore_last"},
        })
        print(f"  ✓ Output {oid} is now detached.")

    # Ensure relay is ON
    status = shelly_rpc(ip, "Switch.GetStatus", {"id": oid})
    if not status["output"]:
        print(f"  Relay is OFF — turning ON to keep bulb powered...")
        shelly_rpc(ip, "Switch.Set", {"id": oid, "on": True})
        print(f"  ✓ Relay is now ON.")
    else:
        print(f"  ✓ Relay is already ON.")


def cmd_revert(args):
    """Revert Shelly output to follow (dumb switch) mode."""
    if not args:
        print("Usage: revert <shelly-ip-or-name> [output-id]")
        sys.exit(1)

    ip, _ = resolve_shelly(args[0])
    oid = int(args[1]) if len(args) > 1 else 0

    print(f"  Reverting output {oid} to follow mode...")
    shelly_rpc(ip, "Switch.SetConfig", {
        "id": oid,
        "config": {"in_mode": "follow", "initial_state": "match_input"},
    })
    print(f"  ✓ Wall switch now controls the relay directly.")


def normalize_automation_config(config):
    """Normalize HA automation config keys.

    HA returns 'triggers'/'actions' (new format) but accepts 'trigger'/'action' (old format).
    Also 'action' (service call key) vs 'service'. Normalize to old format for consistency.
    """
    if config is None:
        return None

    # triggers → trigger
    if "triggers" in config and "trigger" not in config:
        config["trigger"] = config.pop("triggers")
    elif "triggers" in config:
        config.pop("triggers")

    # actions → action
    if "actions" in config and "action" not in config:
        config["action"] = config.pop("actions")
    elif "actions" in config:
        config.pop("actions")

    # Inside action items: 'action' key (new) → 'service' key (old)
    for item in config.get("action", []):
        if "action" in item and "service" not in item:
            item["service"] = item.pop("action")

    return config


def get_automation_config():
    """Get the current automation config, or None if it doesn't exist."""
    try:
        config = ha_api("GET", f"/api/config/automation/config/{AUTOMATION_ID}")
        return normalize_automation_config(config)
    except SystemExit:
        return None


def save_automation_config(config):
    """Save the automation config."""
    result = ha_api("POST", f"/api/config/automation/config/{AUTOMATION_ID}", config)
    return result


def cmd_add_pair(args):
    """Add a Shelly input → light pair to the HA automation."""
    if len(args) < 2:
        print("Usage: add-pair <shelly-ip-or-name> <light-entity-or-name> [output-id]")
        sys.exit(1)

    ip, shelly_name = resolve_shelly(args[0])
    light_entity, light_name = resolve_light(args[1])
    oid = int(args[2]) if len(args) > 2 else 0

    input_entity = resolve_shelly_input_entity(ip, oid)

    print(f"\n  Pair: {input_entity} → {light_entity} ({light_name})")

    config = get_automation_config()

    if config is None or "message" in config:
        # Create new automation
        print("  Creating new automation...")
        config = {
            "alias": "Smart Toggle - Shelly to Zigbee",
            "description": "Universal edge-mode smart toggle. Maps Shelly input sensors to IKEA Zigbee lights.",
            "mode": "parallel",
            "max": 20,
            "trigger": [
                {
                    "platform": "state",
                    "entity_id": [input_entity],
                }
            ],
            "action": [
                {
                    "variables": {
                        "shelly_to_light": {
                            input_entity: light_entity,
                        },
                        "target_light": "{{ shelly_to_light.get(trigger.entity_id, '') }}",
                    }
                },
                {
                    "condition": "template",
                    "value_template": "{{ target_light != '' }}",
                },
                {
                    "service": "light.toggle",
                    "target": {
                        "entity_id": "{{ target_light }}",
                    },
                },
            ],
        }
    else:
        # Update existing automation
        print("  Updating existing automation...")

        # Add to trigger list
        trigger_entities = config["trigger"][0].get("entity_id", [])
        if isinstance(trigger_entities, str):
            trigger_entities = [trigger_entities]
        if input_entity not in trigger_entities:
            trigger_entities.append(input_entity)
        config["trigger"][0]["entity_id"] = trigger_entities

        # Add to mapping
        variables = config["action"][0].get("variables", {})
        mapping = variables.get("shelly_to_light", {})
        mapping[input_entity] = light_entity
        variables["shelly_to_light"] = mapping
        config["action"][0]["variables"] = variables

    save_automation_config(config)
    print(f"  ✓ Pair added: {input_entity} → {light_entity}")

    # Update local pairings file
    upsert_pair_in_file(input_entity, light_entity)

    # Verify
    state = ha_api("GET", "/api/states/automation.smart_toggle_shelly_to_zigbee")
    print(f"  Automation state: {state['state']}")


def cmd_show_pairs(args):
    """Show all configured Shelly→light pairs."""
    config = get_automation_config()

    if config is None or "message" in config:
        print("  No automation found. Run 'setup' or 'add-pair' first.")
        return

    # Extract triggers
    trigger_entities = []
    for t in config.get("trigger", []):
        eid = t.get("entity_id", [])
        if isinstance(eid, str):
            trigger_entities.append(eid)
        else:
            trigger_entities.extend(eid)

    # Extract mapping
    mapping = {}
    for a in config.get("action", []):
        variables = a.get("variables", {})
        if "shelly_to_light" in variables:
            mapping = variables["shelly_to_light"]

    if not mapping:
        print("  No pairs configured.")
        return

    print("\n  Configured pairs:")
    print(f"  {'─' * 70}")
    for input_ent, light_ent in mapping.items():
        in_triggers = "✓" if input_ent in trigger_entities else "✗ MISSING FROM TRIGGERS"
        # Try to get friendly names
        try:
            light_state = ha_api("GET", f"/api/states/{light_ent}")
            light_name = light_state.get("attributes", {}).get("friendly_name", "")
        except SystemExit:
            light_name = "?"
        print(f"  {input_ent}")
        print(f"    → {light_ent} ({light_name}) [{in_triggers}]")
    print(f"  {'─' * 70}")
    print(f"  Total: {len(mapping)} pair(s)")

    # Show local file status
    local_pairs = load_pairings()
    if local_pairs:
        local_inputs = {p["input_entity"] for p in local_pairs}
        ha_inputs = set(mapping.keys())
        if local_inputs == ha_inputs:
            print(f"  Local file: ✓ in sync ({PAIRINGS_FILE.name})")
        else:
            only_local = local_inputs - ha_inputs
            only_ha = ha_inputs - local_inputs
            print(f"  Local file: ✗ out of sync")
            if only_ha:
                print(f"    In HA but not local: {', '.join(only_ha)}")
            if only_local:
                print(f"    In local but not HA: {', '.join(only_local)}")
            print(f"    Run 'sync-pairs' to update.")
    else:
        print(f"  Local file: not found — run 'sync-pairs' to create.")


def cmd_sync_pairs(args):
    """Download all pairs from HA automation and save to local pairings file."""
    config = get_automation_config()

    if config is None or "message" in config:
        print("  No automation found in HA. Nothing to sync.")
        return

    # Extract mapping
    mapping = {}
    for a in config.get("action", []):
        variables = a.get("variables", {})
        if "shelly_to_light" in variables:
            mapping = variables["shelly_to_light"]

    if not mapping:
        print("  No pairs in HA automation. Nothing to sync.")
        return

    print(f"  Found {len(mapping)} pair(s) in HA automation. Enriching...")

    pairs = []
    for input_ent, light_ent in mapping.items():
        print(f"    {input_ent} → {light_ent}")
        pair = enrich_pair(input_ent, light_ent)
        pairs.append(pair)

    save_pairings(pairs)
    print()
    print(f"  Pairings file: {PAIRINGS_FILE}")
    for p in pairs:
        shelly = p.get('shelly_device_name') or p.get('shelly_ip') or '?'
        light = p.get('light_friendly_name') or p['light_entity']
        print(f"    {shelly} / {p.get('input_friendly_name', '?')}")
        print(f"      → {light} ({p['light_entity']})")


def cmd_test(args):
    """Toggle a light to verify it works."""
    if not args:
        print("Usage: test <light-entity-or-name>")
        sys.exit(1)

    light_entity, light_name = resolve_light(args[0])

    state_before = ha_api("GET", f"/api/states/{light_entity}")
    print(f"  {light_name} ({light_entity}): {state_before['state']}")

    print(f"  Toggling...")
    ha_api("POST", "/api/services/light/toggle", {"entity_id": light_entity})

    import time
    time.sleep(1)

    state_after = ha_api("GET", f"/api/states/{light_entity}")
    print(f"  {light_name} ({light_entity}): {state_after['state']}")

    if state_before["state"] != state_after["state"]:
        print(f"  ✓ Toggle worked.")
    else:
        print(f"  ✗ State didn't change — is the bulb reachable?")


def cmd_setup(args):
    """Full setup: detach Shelly + add pair to HA automation.

    Usage: setup <shelly-ip-or-name> <light-entity-or-name> [output-id]
    """
    if len(args) < 2:
        print("Usage: setup <shelly-ip-or-name> <light-entity-or-name> [output-id]")
        print()
        print("Examples:")
        print("  ./smart-toggle-setup.py setup lights-kid-room Υπνοδωματιο 0")
        print("  ./smart-toggle-setup.py setup 192.168.1.24 light.lampteras_14")
        sys.exit(1)

    shelly_arg = args[0]
    light_arg = args[1]
    oid = int(args[2]) if len(args) > 2 else 0

    print(f"═══ Smart Toggle Setup ═══")
    print(f"  Shelly: {shelly_arg} (output {oid})")
    print(f"  Light:  {light_arg}")
    print()

    # Step 1: Resolve both
    print("Step 1: Resolving devices...")
    ip, shelly_name = resolve_shelly(shelly_arg)
    light_entity, light_name = resolve_light(light_arg)
    input_entity = resolve_shelly_input_entity(ip, oid)
    print()

    # Step 2: Set Shelly to detached mode
    print("Step 2: Setting Shelly to detached mode...")
    cmd_detach([ip, str(oid)])
    print()

    # Step 3: Add pair to HA automation
    print("Step 3: Adding pair to HA automation...")
    print(f"  Pair: {input_entity} → {light_entity} ({light_name})")

    config = get_automation_config()

    if config is None or "message" in config:
        config = {
            "alias": "Smart Toggle - Shelly to Zigbee",
            "description": "Universal edge-mode smart toggle. Maps Shelly input sensors to IKEA Zigbee lights.",
            "mode": "parallel",
            "max": 20,
            "trigger": [{"platform": "state", "entity_id": [input_entity]}],
            "action": [
                {
                    "variables": {
                        "shelly_to_light": {input_entity: light_entity},
                        "target_light": "{{ shelly_to_light.get(trigger.entity_id, '') }}",
                    }
                },
                {"condition": "template", "value_template": "{{ target_light != '' }}"},
                {"service": "light.toggle", "target": {"entity_id": "{{ target_light }}"}},
            ],
        }
    else:
        trigger_entities = config["trigger"][0].get("entity_id", [])
        if isinstance(trigger_entities, str):
            trigger_entities = [trigger_entities]
        if input_entity not in trigger_entities:
            trigger_entities.append(input_entity)
        config["trigger"][0]["entity_id"] = trigger_entities

        variables = config["action"][0].get("variables", {})
        mapping = variables.get("shelly_to_light", {})
        mapping[input_entity] = light_entity
        variables["shelly_to_light"] = mapping
        config["action"][0]["variables"] = variables

    save_automation_config(config)
    print(f"  ✓ Automation updated.")

    # Update local pairings file
    upsert_pair_in_file(input_entity, light_entity)
    print()

    # Summary
    print(f"═══ Setup Complete ═══")
    print(f"  Shelly {shelly_name or ip} output {oid} → detached mode")
    print(f"  {input_entity} → {light_entity} ({light_name})")
    print(f"  Flip the wall switch to test!")


def cmd_help():
    print("Smart Toggle Setup — Configure Shelly + HA for edge-mode smart toggle")
    print()
    print("Commands:")
    print("  setup     <shelly> <light> [output-id]   Full setup: detach + add pair")
    print("  status    <shelly> [output-id]            Show Shelly config/status")
    print("  detach    <shelly> [output-id]            Set Shelly to detached mode")
    print("  revert    <shelly> [output-id]            Revert to follow (dumb) mode")
    print("  add-pair  <shelly> <light> [output-id]    Add pair to HA automation")
    print("  show-pairs                                Show all configured pairs")
    print("  sync-pairs                                Download pairs from HA to local file")
    print("  test      <light>                         Toggle a light via HA")
    print()
    print("<shelly> can be an IP (192.168.1.24) or HA device name (lights-kid-room)")
    print("<light>  can be an entity_id (light.xxx) or friendly name (Υπνοδωματιο)")
    print("Output ID defaults to 0.")
    print()
    print("Examples:")
    print("  ./smart-toggle-setup.py setup lights-kid-room Υπνοδωματιο 0")
    print("  ./smart-toggle-setup.py setup 192.168.1.24 light.lampteras_14")
    print("  ./smart-toggle-setup.py show-pairs")
    print("  ./smart-toggle-setup.py revert lights-office")


# --- Main ---

def main():
    if len(sys.argv) < 2:
        cmd_help()
        sys.exit(0)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    commands = {
        "setup":      cmd_setup,
        "status":     cmd_status,
        "detach":     cmd_detach,
        "revert":     cmd_revert,
        "add-pair":   cmd_add_pair,
        "show-pairs": cmd_show_pairs,
        "sync-pairs": cmd_sync_pairs,
        "test":       cmd_test,
        "help":       lambda _: cmd_help(),
    }

    if cmd not in commands:
        print(f"Unknown command: {cmd}")
        cmd_help()
        sys.exit(1)

    commands[cmd](args)


if __name__ == "__main__":
    main()
