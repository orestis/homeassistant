# Smart Switch Plan: Shelly + IKEA Bulbs + Home Assistant

## Goal

Many IKEA Zigbee bulbs are wired through Shelly relays controlled by physical wall switches. We want the physical switch to trigger a "smart" toggle (via Zigbee through HA) instead of cutting power. This keeps the bulb always powered and controllable from HA UI, automations, etc.

## Architecture

- **Shelly Gen3/Gen4** relays (Gen2+ API is shared across Gen2/Gen3/Gen4 — all support mJS scripting, detached mode, and the same RPC interface)
- **IKEA bulbs**: controlled via Zigbee through HA
- **Home Assistant**: orchestrates everything
- **MQTT broker**: Mosquitto add-on running on HA (same host) — already configured on Shellys, but **not needed for Phases 1-2**

## Transport: How HA ↔ Shelly Communication Works

HA's native Shelly integration uses a **persistent WebSocket** connection:

```
HA ──── WebSocket (persistent, bidirectional) ────► Shelly (port 80, /rpc)
```

- **HA initiates** the WebSocket to `ws://<shelly-ip>/rpc`
- Connection stays open permanently
- Shelly pushes **real-time event notifications** (input changes, switch state, etc.) over this WebSocket — near-instant latency
- HA sends **RPC commands** to Shelly over the same WebSocket
- No polling, no extra connections

This means:
- Button press in detached mode → Shelly pushes `NotifyEvent` over existing WebSocket → HA sees it in milliseconds
- HA can call custom RPC handlers on the Shelly script over the same WebSocket (useful for ack in Phase 2)
- **No MQTT needed for event delivery or ack** — the WebSocket handles both directions

## Full Design (Three-Tier Fallback)

### Tier 1: Smart Toggle (HA + Zigbee working)

1. Shelly is in **detached** mode (relay does not follow switch input)
2. Button press → Shelly native integration fires a device trigger in HA (over existing WebSocket)
3. HA automation receives trigger → calls `light.toggle` on the IKEA bulb
4. Done (Phase 1: no ack, no fallback)

With ack (Phase 2):
5. HA confirms bulb state changed → calls custom RPC `Custom.Ack` on Shelly (over same WebSocket)
6. Shelly script receives ack → cancels fallback timer

### Tier 2: Ack Timeout (HA up, but Zigbee/bulb not responding)

1. Shelly script starts a ~2 second timer on button press
2. If no ack received → Shelly script toggles relay via `Shelly.call("Switch.Toggle")`
3. Power is cut/restored → light toggles the "dumb" way
4. User still gets their light toggle, just not via Zigbee

### Tier 3: Dumb Switch (HA down entirely)

The Shelly needs to detect that HA is unavailable and revert to dumb switch mode.

**Detection options (to be investigated in Phase 3):**

| Approach | How | Pros | Cons |
|---|---|---|---|
| WebSocket client disconnect | Shelly script detects that HA's WebSocket has dropped | Zero extra dependencies, uses existing connection | **Unclear if Shelly mJS scripting API exposes this** — needs research/testing |
| MQTT connection monitoring | Monitor MQTT broker connection on the Shelly | Built-in MQTT status events in scripts | Adds MQTT as a dependency; MQTT broker (Mosquitto add-on) runs on same host as HA, so it covers most failures |
| MQTT heartbeat watchdog | HA publishes heartbeat every 15s; Shelly watches for timeout | Catches HA Core crash even when Mosquitto stays up | Most complex, needs HA automation + Shelly script |
| HTTP health check | Shelly script polls HA REST API periodically | No MQTT needed | Heavy, needs auth token, fragile to transient hiccups |

**Key research question for Phase 3:** Can the Shelly mJS scripting API detect when the HA WebSocket client disconnects? If yes, MQTT is not needed for fallback detection either. If no, MQTT connection monitoring + heartbeat is the best fallback signal.

When fallback triggers:
- Shelly switches from "detached" to "follow" mode
- Relay follows switch input directly — classic dumb switch behavior
- When HA reconnects → switch back to detached/smart mode

## Implementation Phases

### Phase 1: Basic Smart Toggle (No Fallback) ✅ COMPLETE

Goal: Validate the core concept works end-to-end using **only native HA integrations** (no MQTT, no Shelly scripts).

**Steps:**

1. Pick one test Shelly + IKEA bulb pair
2. Configure the Shelly's switch to "detached" mode via API:
   ```
   Switch.SetConfig → {"config": {"in_mode": "detached"}}
   ```
3. Verify: pressing the physical switch no longer toggles the relay
4. Confirm that the Shelly HA integration still fires a device trigger / event for button presses in detached mode (check HA Developer Tools → Events for `shelly.click` or device trigger events)
5. Create an HA automation:
   - **Trigger**: Shelly device trigger (button press)
   - **Action**: `light.toggle` on the paired IKEA bulb
6. Test: press physical switch → bulb toggles via Zigbee
7. Test edge cases:
   - Rapid presses
   - Long press vs short press
   - HA restart — does the WebSocket reconnect automatically?
   - What happens when you press the switch while HA is restarting?

**What we learn from Phase 1:**
- Does the native integration reliably deliver button events in detached mode?
- What's the latency from button press to bulb toggle?
- Any issues with rapid presses or debouncing?

### Phase 2: Add Ack + Relay Fallback

Add a Shelly mJS script that provides a safety net when Zigbee fails.

- Write Shelly mJS script with:
  - `Custom.Ack` RPC handler (receives ack from HA over WebSocket)
  - Button event handler: starts ~2s fallback timer
  - On ack → cancel timer
  - On timeout → `Shelly.call("Switch.Toggle")` to toggle relay as fallback
- Update HA automation to:
  - Toggle bulb
  - Confirm state change
  - Call `Custom.Ack` on Shelly via RPC (over existing WebSocket)

**Note:** Button events should still flow through the native integration (WebSocket), not MQTT. The Shelly script only adds the ack handler and fallback timer.

### Phase 3: Add Connectivity Fallback

Research and implement HA-down detection:

1. **First**: test whether Shelly mJS can detect WebSocket client disconnection
   - If yes → implement mode switching based on WebSocket status (simplest, no MQTT)
   - If no → use MQTT connection monitoring + heartbeat watchdog:
     - MQTT disconnect → immediate switch to "follow" mode
     - Heartbeat timeout (30s) → switch to "follow" mode (catches HA Core crash while Mosquitto stays up)
     - Reconnect + heartbeat resumes → switch back to "detached" mode
     - HA heartbeat automation: publish to `ha/heartbeat` every 15s

### Phase 4: Scale to All Devices

- Parameterize Shelly script (device ID)
- Write deployment script to push script + config to all Shelly devices
- Create HA automation blueprints for all Shelly + bulb pairs
