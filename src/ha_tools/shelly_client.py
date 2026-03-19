"""Shelly Gen2+ RPC client — top-level helper functions."""

import json
import urllib.request
import urllib.error


def rpc(ip, method, params=None):
    """Call a Shelly Gen2+ RPC method and return the result dict.

    Raises RuntimeError on network or RPC errors.
    """
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
        raise RuntimeError(f"Cannot reach Shelly at {ip}: {e}")

    if "error" in result:
        raise RuntimeError(f"Shelly RPC {method} failed: {result['error']}")

    return result.get("result", result)


def get_device_info(ip):
    """Get Shelly device info (id, model, mac, firmware, etc.)."""
    return rpc(ip, "Shelly.GetDeviceInfo")


def get_switch_config(ip, output_id=0):
    """Get Switch config for a given output."""
    return rpc(ip, "Switch.GetConfig", {"id": output_id})


def set_switch_config(ip, output_id=0, **config):
    """Set Switch config (e.g. in_mode, initial_state)."""
    return rpc(ip, "Switch.SetConfig", {"id": output_id, "config": config})


def get_switch_status(ip, output_id=0):
    """Get Switch status (output on/off, source, temperature)."""
    return rpc(ip, "Switch.GetStatus", {"id": output_id})


def switch_set(ip, output_id=0, on=True):
    """Turn a switch output on or off."""
    return rpc(ip, "Switch.Set", {"id": output_id, "on": on})


def detach(ip, output_id=0):
    """Set a switch output to detached mode and ensure relay is ON.

    Returns a summary dict with what was changed.
    """
    result = {"ip": ip, "output_id": output_id, "changes": []}

    cfg = get_switch_config(ip, output_id)
    if cfg["in_mode"] == "detached":
        result["changes"].append("already detached")
    else:
        set_switch_config(ip, output_id, in_mode="detached", initial_state="restore_last")
        result["changes"].append(f"detached (was {cfg['in_mode']})")

    status = get_switch_status(ip, output_id)
    if not status["output"]:
        switch_set(ip, output_id, on=True)
        result["changes"].append("relay turned ON")
    else:
        result["changes"].append("relay already ON")

    return result
