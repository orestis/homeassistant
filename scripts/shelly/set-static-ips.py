#!/usr/bin/env python3
"""
set-static-ips.py — Set static IPs on Shelly devices from shelly-inventory.json.

Reads the inventory, checks each device's current WiFi config, and applies
the static_ip if it differs from what's already configured.

Usage:
  python3 set-static-ips.py              Show plan (dry run)
  python3 set-static-ips.py apply        Apply static IPs to all devices
  python3 set-static-ips.py apply <name> Apply static IP to one device (by friendly_name)
"""

import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

INVENTORY_FILE = Path(__file__).parent / "shelly-inventory.json"
GATEWAY = "192.168.1.1"
NETMASK = "255.255.255.0"
NAMESERVER = "192.168.1.1"


def shelly_rpc(ip, method, params=None, timeout=10):
    """Call a Shelly Gen2+ RPC method. Returns result dict or None on error."""
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
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {"_error": str(e)}

    if "error" in result:
        return {"_error": f"RPC error: {result['error']}"}

    return result.get("result", result)


def check_device_ip(ip, target_ip):
    """Check if a device at `ip` already has `target_ip` configured as static.

    Returns: (reachable: bool, already_static: bool, current_static_ip: str|None)
    """
    result = shelly_rpc(ip, "Wifi.GetConfig")
    if "_error" in result:
        return False, False, None

    sta = result.get("sta", {})
    if sta.get("ipv4mode") == "static" and sta.get("ip") == target_ip:
        return True, True, sta.get("ip")
    return True, False, sta.get("ip")


def set_static_ip(ip, target_ip):
    """Set static IP on a Shelly device. Returns (success, message)."""
    result = shelly_rpc(ip, "Wifi.SetConfig", {
        "config": {
            "sta": {
                "ipv4mode": "static",
                "ip": target_ip,
                "netmask": NETMASK,
                "gw": GATEWAY,
                "nameserver": NAMESERVER,
            }
        }
    })

    if "_error" in result:
        return False, result["_error"]

    return True, "OK"


def load_inventory():
    with open(INVENTORY_FILE) as f:
        return json.load(f)


def cmd_plan(devices):
    """Show what would be done."""
    print(f"\n  Static IP Assignment Plan ({len(devices)} devices)")
    print(f"  {'#':<4} {'Name':<30} {'Current IP':<18} {'Static IP':<18} {'Status'}")
    print(f"  {'─' * 95}")

    for i, d in enumerate(devices, 1):
        current_ip = d["ip"]
        target_ip = d.get("static_ip", "")
        name = d["friendly_name"]

        if not target_ip:
            print(f"  {i:<4} {name:<30} {current_ip:<18} {'—':<18} NO static_ip set")
            continue

        # Try current IP first, then target IP
        reachable, already, configured_ip = check_device_ip(current_ip, target_ip)
        if not reachable:
            # Maybe it's already at the target IP
            reachable, already, configured_ip = check_device_ip(target_ip, target_ip)

        if not reachable:
            status = "UNREACHABLE"
        elif already:
            status = "✓ already static"
        else:
            status = "→ needs update"

        print(f"  {i:<4} {name:<30} {current_ip:<18} {target_ip:<18} {status}")


def cmd_apply(devices, filter_name=None):
    """Apply static IPs to devices."""
    if filter_name:
        devices = [d for d in devices if d["friendly_name"] == filter_name]
        if not devices:
            print(f"  ERROR: No device named '{filter_name}' in inventory")
            sys.exit(1)

    print(f"\n  Applying static IPs to {len(devices)} device(s)...\n")

    success = 0
    skipped = 0
    failed = 0

    for i, d in enumerate(devices, 1):
        current_ip = d["ip"]
        target_ip = d.get("static_ip", "")
        name = d["friendly_name"]

        if not target_ip:
            print(f"  [{i}/{len(devices)}] {name:<30} — no static_ip, skipping")
            skipped += 1
            continue

        # Check if already configured — try current IP, then target IP
        reach_ip = current_ip
        reachable, already, _ = check_device_ip(current_ip, target_ip)
        if not reachable:
            reachable, already, _ = check_device_ip(target_ip, target_ip)
            reach_ip = target_ip

        if not reachable:
            print(f"  [{i}/{len(devices)}] {name:<30} ✗ unreachable at {current_ip} and {target_ip}")
            failed += 1
            continue

        if already:
            print(f"  [{i}/{len(devices)}] {name:<30} ✓ already at {target_ip}")
            skipped += 1
            continue

        print(f"  [{i}/{len(devices)}] {name:<30} {reach_ip} → {target_ip} ... ", end="", flush=True)

        ok, msg = set_static_ip(reach_ip, target_ip)
        if ok:
            print(f"✓ {msg}")
            success += 1

            # Verify at new IP after a short delay
            time.sleep(2)
            verify = shelly_rpc(target_ip, "Shelly.GetDeviceInfo", timeout=5)
            if "_error" not in verify:
                print(f"           {'':30} verified at {target_ip}")
            else:
                print(f"           {'':30} ⚠ not yet reachable at {target_ip} (may need more time)")
        else:
            print(f"✗ {msg}")
            failed += 1

        # Small delay between devices
        if i < len(devices):
            time.sleep(1)

    print(f"\n  Done: {success} updated, {skipped} skipped, {failed} failed")


def main():
    devices = load_inventory()

    if len(sys.argv) < 2 or sys.argv[1] == "plan":
        cmd_plan(devices)
    elif sys.argv[1] == "apply":
        filter_name = sys.argv[2] if len(sys.argv) > 2 else None
        cmd_apply(devices, filter_name)
    else:
        print("Usage: python3 set-static-ips.py [plan|apply [device-name]]")
        sys.exit(1)


if __name__ == "__main__":
    main()
