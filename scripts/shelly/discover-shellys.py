#!/usr/bin/env python3
"""Discover all Shelly devices registered in Home Assistant.

Outputs a table and saves shelly-inventory.json with:
  - Device name (Shelly ID)
  - Current IP
  - Model
  - MAC address
  - HA entity friendly names (for human reference)
"""

import json
import re
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

token = Path(__file__).parent.joinpath(".ha-token").read_text().strip()
HA = "http://homeassistant.local:8123"


def ha_tpl(template):
    url = f"{HA}/api/template"
    data = json.dumps({"template": template}).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode().strip()
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        print(f"  Template error: {body}")
        return None


def ha_api_get(endpoint):
    url = f"{HA}{endpoint}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


print("Discovering Shelly devices in Home Assistant...\n")

# Step 1: Get all shelly integration entities
all_ents_str = ha_tpl('{{ integration_entities("shelly") | join("\\n") }}')
if not all_ents_str:
    print("ERROR: Could not get Shelly entities from HA")
    exit(1)

entities = [l.strip() for l in all_ents_str.split("\n") if l.strip()]
print(f"  {len(entities)} Shelly integration entities")

# Step 2: Get device_id for each entity, build device→entities mapping
device_entities = {}  # device_id → list of entity_ids
for ent in entities:
    did = ha_tpl(f'{{{{ device_id("{ent}") }}}}')
    if did and did != "None":
        device_entities.setdefault(did, []).append(ent)

print(f"  {len(device_entities)} unique HA device entries")

# Step 3: Get details for each device
devices = []
for did, ents in device_entities.items():
    name = ha_tpl(f'{{% set d = "{did}" %}}{{{{ device_attr(d, "name") }}}}')
    curl = ha_tpl(f'{{% set d = "{did}" %}}{{{{ device_attr(d, "configuration_url") }}}}')
    model = ha_tpl(f'{{% set d = "{did}" %}}{{{{ device_attr(d, "model") }}}}')
    conns = ha_tpl(f'{{% set d = "{did}" %}}{{{{ device_attr(d, "connections") }}}}')

    ip = urllib.parse.urlparse(curl).hostname if curl and curl != "None" else None
    mac_match = re.search(
        r"([0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2})",
        conns or "",
    )
    mac = mac_match.group(1).upper() if mac_match else None

    # Get friendly names from entities
    friendly_names = []
    for ent in ents:
        try:
            state = ha_api_get(f"/api/states/{ent}")
            fname = state.get("attributes", {}).get("friendly_name", "")
            if fname:
                friendly_names.append({"entity_id": ent, "friendly_name": fname})
        except Exception:
            friendly_names.append({"entity_id": ent, "friendly_name": ""})

    devices.append({
        "device_name": name,
        "ip": ip,
        "model": model,
        "mac": mac,
        "device_id": did,
        "entities": friendly_names,
    })


# Filter: only physical devices (those with a MAC address) — skip sub-devices like "Output 0"
physical = [d for d in devices if d["mac"]]
sub_devices = [d for d in devices if not d["mac"]]

# Merge sub-device entities into their parent (matched by IP)
for sub in sub_devices:
    for parent in physical:
        if parent["ip"] and sub["ip"] and parent["ip"] == sub["ip"]:
            parent["entities"].extend(sub["entities"])
            break


def ip_sort_key(d):
    try:
        return tuple(int(p) for p in d["ip"].split("."))
    except (ValueError, AttributeError, TypeError):
        return (999, 999, 999, 999)


physical.sort(key=ip_sort_key)

# Print table
print(f"\n{'#':<4} {'Device Name':<40} {'IP':<18} {'Model':<25} {'MAC':<20} {'Friendly Names'}")
print("=" * 140)
for i, d in enumerate(physical, 1):
    # Pick the most useful friendly name (prefer switch/light entities)
    fnames = []
    for e in d["entities"]:
        fn = e["friendly_name"]
        eid = e["entity_id"]
        # Prefer switch, light, and cover entities for display
        if any(eid.startswith(p) for p in ("switch.", "light.", "cover.")):
            fnames.insert(0, fn)
        elif fn and fn not in fnames:
            fnames.append(fn)
    # Deduplicate: strip common prefixes from the device name
    display_names = []
    seen = set()
    for fn in fnames:
        base = fn.split(" ")[0] if fn else ""
        if base not in seen:
            seen.add(base)
            display_names.append(fn)
        if len(display_names) >= 3:
            break

    names_str = ", ".join(display_names[:3])
    print(f"{i:<4} {d['device_name']:<40} {d['ip'] or '?':<18} {d['model']:<25} {d['mac']:<20} {names_str}")

print(f"\nTotal: {len(physical)} physical Shelly devices")

# Save JSON (clean up for the inventory file)
inventory = []
for d in physical:
    # Pick primary friendly name
    primary_name = ""
    for e in d["entities"]:
        eid = e["entity_id"]
        if any(eid.startswith(p) for p in ("switch.", "light.", "cover.")):
            primary_name = e["friendly_name"]
            break
    if not primary_name:
        for e in d["entities"]:
            if e["friendly_name"]:
                primary_name = e["friendly_name"]
                break

    inventory.append({
        "device_name": d["device_name"],
        "friendly_name": primary_name,
        "ip": d["ip"],
        "mac": d["mac"],
        "model": d["model"],
    })

out = Path(__file__).parent / "shelly-inventory.json"
with open(out, "w") as f:
    json.dump(inventory, f, indent=2, ensure_ascii=False)
print(f"\nSaved to {out.name}")
