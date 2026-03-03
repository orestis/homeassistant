#!/usr/bin/env python3
"""
ble-proxy-setup.py — Enable Bluetooth proxy on all compatible Shelly devices.

Reads the inventory, checks each device's BLE config, and enables BLE
so Home Assistant can use them as Bluetooth proxies.

Only Gen2+ devices (Gen2/Plus, Gen3, Gen4, Pro, Wall Display) support BLE.
Gen1 devices (e.g. Shelly Shutter) are automatically skipped.

Once BLE is enabled, the HA Shelly integration automatically uses the
device as a Bluetooth proxy — no additional HA configuration needed.

Usage:
  python3 ble-proxy-setup.py              Show current BLE status (dry run)
  python3 ble-proxy-setup.py apply        Enable BLE on all compatible devices
  python3 ble-proxy-setup.py apply <name> Enable BLE on one device (by friendly_name)
"""

import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

INVENTORY_FILE = Path(__file__).parent / "shelly-inventory.json"

# Gen1 device prefixes — these do NOT support BLE
GEN1_PREFIXES = ("shellyshutter-", "shelly1-", "shelly1pm-", "shellyswitch",
                 "shellyplug-", "shelly2-", "shellyrgbw2-", "shellydimmer-",
                 "shellyht-", "shellydw-", "shellyflood-", "shellygas-",
                 "shellysmoke-", "shellyi3-", "shelly25-", "shellyem-",
                 "shelly3em-", "shellybutton1-")


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


def is_gen1(device_name):
    """Check if a device is Gen1 (no BLE support)."""
    name_lower = device_name.lower()
    return any(name_lower.startswith(prefix) for prefix in GEN1_PREFIXES)


def get_ble_config(ip):
    """Get current BLE config. Returns (reachable, supports_ble, ble_enabled, observer_enabled)."""
    result = shelly_rpc(ip, "BLE.GetConfig")
    if isinstance(result, dict) and "_error" in result:
        # Could be unreachable or BLE not supported
        if "RPC error" in result["_error"]:
            return True, False, False, False
        return False, False, False, False

    ble_enabled = result.get("enable", False)

    # Check observer config if available
    obs_result = shelly_rpc(ip, "BLE.Observer.GetConfig")
    observer_enabled = False
    if isinstance(obs_result, dict) and "_error" not in obs_result:
        observer_enabled = obs_result.get("enable", False)

    return True, True, ble_enabled, observer_enabled


def enable_ble(ip):
    """Enable BLE on a Shelly device. Returns (success, message)."""
    result = shelly_rpc(ip, "BLE.SetConfig", {"config": {"enable": True}})
    if isinstance(result, dict) and "_error" in result:
        return False, result["_error"]
    return True, "BLE enabled"


def enable_observer(ip):
    """Enable BLE observer on a Shelly device. Returns (success, message)."""
    result = shelly_rpc(ip, "BLE.Observer.SetConfig", {"config": {"enable": True}})
    if isinstance(result, dict) and "_error" in result:
        return False, result["_error"]
    return True, "Observer enabled"


def load_inventory():
    with open(INVENTORY_FILE) as f:
        return json.load(f)


def cmd_status(devices):
    """Show BLE status for all devices."""
    print(f"\n  BLE Proxy Status ({len(devices)} devices)")
    print(f"  {'#':<4} {'Name':<30} {'IP':<18} {'Model':<22} {'BLE':<10} {'Observer'}")
    print(f"  {'─' * 110}")

    gen1_skipped = 0
    unreachable = 0
    no_ble = 0
    ble_on = 0
    ble_off = 0
    obs_on = 0

    for i, d in enumerate(devices, 1):
        name = d.get("friendly_name", d["device_name"])
        ip = d["ip"]
        model = d.get("model", "?")

        if is_gen1(d["device_name"]):
            status = "Gen1 — skip"
            obs_status = "—"
            gen1_skipped += 1
        else:
            reachable, supports_ble, ble_enabled, observer_enabled = get_ble_config(ip)
            if not reachable:
                status = "UNREACHABLE"
                obs_status = "—"
                unreachable += 1
            elif not supports_ble:
                status = "Not supported"
                obs_status = "—"
                no_ble += 1
            elif ble_enabled:
                status = "✓ ON"
                ble_on += 1
                obs_status = "✓ ON" if observer_enabled else "OFF"
                if observer_enabled:
                    obs_on += 1
            else:
                status = "OFF"
                obs_status = "—"
                ble_off += 1

        print(f"  {i:<4} {name:<30} {ip:<18} {model:<22} {status:<10} {obs_status}")

    print(f"\n  Summary:")
    print(f"    BLE enabled:     {ble_on}")
    print(f"    BLE disabled:    {ble_off}")
    print(f"    Observer on:     {obs_on}")
    print(f"    Gen1 (skipped):  {gen1_skipped}")
    print(f"    No BLE support:  {no_ble}")
    print(f"    Unreachable:     {unreachable}")

    if ble_off > 0:
        print(f"\n  Run 'python3 ble-proxy-setup.py apply' to enable BLE on {ble_off} device(s).")


def cmd_apply(devices, filter_name=None):
    """Enable BLE + observer on devices."""
    if filter_name:
        devices = [d for d in devices if filter_name.lower() in d.get("friendly_name", "").lower()
                   or filter_name.lower() in d["device_name"].lower()]
        if not devices:
            print(f"  No device matching '{filter_name}' found in inventory.")
            return

    print(f"\n  Enabling BLE proxy on compatible devices...\n")

    success_count = 0
    skip_count = 0
    fail_count = 0
    needs_reboot = []

    for d in devices:
        name = d.get("friendly_name", d["device_name"])
        ip = d["ip"]

        if is_gen1(d["device_name"]):
            print(f"  SKIP  {name:<30} Gen1 — no BLE support")
            skip_count += 1
            continue

        reachable, supports_ble, ble_enabled, observer_enabled = get_ble_config(ip)

        if not reachable:
            print(f"  FAIL  {name:<30} Unreachable at {ip}")
            fail_count += 1
            continue

        if not supports_ble:
            print(f"  SKIP  {name:<30} BLE not supported")
            skip_count += 1
            continue

        changed = False

        # Enable BLE if not already on
        if not ble_enabled:
            ok, msg = enable_ble(ip)
            if ok:
                print(f"  OK    {name:<30} BLE enabled")
                changed = True
            else:
                print(f"  FAIL  {name:<30} BLE enable failed: {msg}")
                fail_count += 1
                continue
        else:
            print(f"  OK    {name:<30} BLE already enabled")

        # Enable observer if not already on
        if not observer_enabled:
            ok, msg = enable_observer(ip)
            if ok:
                print(f"  OK    {name:<30} Observer enabled")
                changed = True
            else:
                # Observer might not be available on all devices — not fatal
                print(f"  WARN  {name:<30} Observer not available: {msg}")

        if changed:
            needs_reboot.append((name, ip))

        success_count += 1

    print(f"\n  Done: {success_count} configured, {skip_count} skipped, {fail_count} failed")

    if needs_reboot:
        print(f"\n  {len(needs_reboot)} device(s) were changed. Rebooting to apply...")
        for name, ip in needs_reboot:
            result = shelly_rpc(ip, "Shelly.Reboot")
            if isinstance(result, dict) and "_error" in result:
                print(f"    WARN  {name} — reboot failed: {result['_error']}")
            else:
                print(f"    OK    {name} — rebooting")

    print(f"\n  Next steps:")
    print(f"    1. The HA Shelly integration will automatically detect BLE proxy capability")
    print(f"    2. Check Settings → Devices → (any Shelly) → Diagnostic info for BLE proxy status")
    print(f"    3. Nearby Bluetooth devices should start appearing in HA automatically")


def main():
    devices = load_inventory()

    if len(sys.argv) < 2 or sys.argv[1] == "status":
        cmd_status(devices)
    elif sys.argv[1] == "apply":
        filter_name = sys.argv[2] if len(sys.argv) > 2 else None
        cmd_apply(devices, filter_name)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
