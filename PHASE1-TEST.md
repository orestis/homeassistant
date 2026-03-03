# Phase 1: Smart Toggle Test Plan

## Test Setup

| Component | Details |
|---|---|
| **Shelly device** | Shelly 1 Gen4 (`shelly1g4-7c2c677f6410`), IP: `192.168.1.24` |
| **Shelly HA device ID** | `86002141fdaf78ae7e63c6cf0f4756eb` |
| **Shelly HA entities** | `switch.lights_office` (relay), `binary_sensor.lights_office_input_0` (wall switch input) |
| **IKEA bulb** | `light.lampteras_14` (friendly name: Γραφείο τοίχος, color_temp bulb) |
| **HA URL** | `http://homeassistant.local:8123` |
| **Shelly integration** | Native Shelly integration (WebSocket-based) |
| **Wall switch type** | Toggle switch (not momentary button) — position doesn't matter, only state changes |

## Current Configuration

- Shelly switch mode: `in_mode: follow` (relay follows switch — default dumb behavior)
- Shelly input type: `switch` (correct for a wall toggle switch)
- Relay state: `on`
- Input state: `on`
- IKEA bulb state: `on`

## What We're Testing

Press the wall toggle switch → Shelly stays powered (relay doesn't change) → HA detects the input state change → HA toggles the IKEA bulb via Zigbee.

The switch acts in **edge mode**: every state transition (on→off OR off→on) triggers a toggle. The physical position of the switch is irrelevant.

## Step-by-Step Plan

### Step 1: Record Current State (Before Any Changes)

Before changing anything, note the current state so we can revert if needed:

```bash
# Shelly switch config (currently in_mode: follow)
curl -sf -X POST -d '{"id":1,"method":"Switch.GetConfig","params":{"id":0}}' \
  "http://192.168.1.24/rpc" | python3 -m json.tool

# Shelly input config (currently type: switch)
curl -sf -X POST -d '{"id":1,"method":"Input.GetConfig","params":{"id":0}}' \
  "http://192.168.1.24/rpc" | python3 -m json.tool
```

### Step 2: Set Shelly to Detached Mode

Change the Shelly's switch input mode from "follow" to "detached". This disconnects the relay from the physical switch — pressing the switch will no longer cut/restore power.

```bash
curl -sf -X POST \
  -d '{"id":1,"method":"Switch.SetConfig","params":{"config":{"in_mode":"detached"}}}' \
  "http://192.168.1.24/rpc" | python3 -m json.tool
```

**Verify:** Press the wall switch. The relay should NOT change. But `binary_sensor.lights_office_input_0` in HA should still toggle between on/off.

### Step 3: Verify Input Events Reach HA in Detached Mode

After setting detached mode, flip the wall switch a couple of times and confirm that `binary_sensor.lights_office_input_0` in HA still changes state. It should — the Shelly integration reports input state independently of the relay.

```bash
# Check input state
curl -sf -H "Authorization: Bearer $HA_TOKEN" \
  "http://homeassistant.local:8123/api/states/binary_sensor.lights_office_input_0" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'state={d[\"state\"]} last_changed={d[\"last_changed\"]}')"
```

Flip the switch, then run the command again — `last_changed` should update and state should flip.

### Step 4: Create the HA Automation

A single automation handles ALL Shelly→light pairs using a mapping dictionary. For now we start with one pair, but it's designed to scale.

The automation YAML:

```yaml
alias: "Smart Toggle - Shelly to Zigbee"
description: >
  Universal edge-mode smart toggle. Maps Shelly input sensors to IKEA Zigbee
  lights. On any state change of a Shelly input (toggle switch), the paired
  light is toggled via Zigbee. Add new pairs by extending the trigger list
  and the map dictionary.
mode: parallel
max: 20
trigger:
  - platform: state
    entity_id:
      - binary_sensor.lights_office_input_0
      # Add more Shelly inputs here as needed:
      # - binary_sensor.lights_bedroom_input_0
      # - binary_sensor.lights_kitchen_input_0
action:
  - variables:
      shelly_to_light:
        binary_sensor.lights_office_input_0: light.lampteras_14
        # Add more pairs here:
        # binary_sensor.lights_bedroom_input_0: light.bedroom_bulb
        # binary_sensor.lights_kitchen_input_0: light.kitchen_bulb
      target_light: "{{ shelly_to_light.get(trigger.entity_id, '') }}"
  - condition: template
    value_template: "{{ target_light != '' }}"
  - service: light.toggle
    target:
      entity_id: "{{ target_light }}"
```

Key points:
- **No `from`/`to` filter** on the trigger — fires on ANY state change (on→off or off→on) — this gives edge-mode behavior with toggle switches
- **`mode: parallel`** — multiple switches can fire simultaneously without blocking each other
- **Mapping dictionary** — one place to manage all Shelly→light pairs
- **Guard condition** — skips if a trigger entity isn't in the map (safety against misconfiguration)
- To add a new pair: add the input entity to the trigger list AND to the `shelly_to_light` dict

**How to create:** Either via HA UI (Settings → Automations → Create Automation → switch to YAML mode) or via the REST API.

### Step 5: Test Basic Function

1. Note current state of `light.lampteras_14` (on or off)
2. Flip the wall switch once
3. Verify: the IKEA bulb toggles (on→off or off→on)
4. Flip the wall switch again
5. Verify: the IKEA bulb toggles back
6. Repeat 2-3 more times to confirm consistency

### Step 6: Test Edge Cases

#### 6a: Rapid Toggle
Flip the switch quickly 3-4 times in a row. Expected: the bulb should end up in the correct final state (same number of toggles). With `mode: single`, some middle toggles may be dropped — which is actually fine for user experience (-if you flip a switch rapidly, you probably just want the final state).

#### 6b: Switch Position Independence
Confirm that the bulb state is NOT tied to the switch position:
1. Switch in position A → bulb on
2. Toggle via HA UI → bulb off
3. Switch still in position A, but bulb is off → this is correct
4. Flip switch to position B → bulb should turn on (toggled)

#### 6c: HA UI Still Works
Toggle the bulb from the HA dashboard. The switch position doesn't change (it's detached), and the bulb should respond normally.

#### 6d: HA Restart
1. Restart HA (Settings → System → Restart)
2. While HA is restarting, flip the switch
3. Expected: nothing happens (bulb stays in current state, relay is detached)
4. After HA comes back up, flip the switch again
5. Expected: bulb toggles normally

#### 6e: Latency Check
Time the delay from switch flip to bulb response. Should be under 1 second (WebSocket event + Zigbee command). If noticeably slow, note the approximate delay.

### Step 7: Evaluate Results

| Test | Pass/Fail | Notes |
|---|---|---|
| Basic toggle (on→off) | PASS | |
| Basic toggle (off→on) | PASS | |
| Edge mode (both directions trigger) | PASS | |
| Rapid toggling | PASS | Keeps up fine |
| Switch position independence | PASS | |
| HA UI control still works | PASS | |
| HA restart recovery | — | Not tested yet |
| Latency acceptable | PASS | Near-instant, faster than cold power-on |

## Rollback

To revert the Shelly to normal dumb switch behavior:

```bash
curl -sf -X POST \
  -d '{"id":1,"method":"Switch.SetConfig","params":{"config":{"in_mode":"follow"}}}' \
  "http://192.168.1.24/rpc" | python3 -m json.tool
```

And disable/delete the HA automation.

## What This Proves

If Phase 1 passes, we've validated that:
1. Detached mode works on this Shelly Gen4
2. HA's Shelly integration delivers input state changes in detached mode (over WebSocket)
3. The end-to-end latency is acceptable
4. Edge-mode triggering works with a toggle switch

This gives us confidence to proceed to Phase 2 (ack + relay fallback) and Phase 3 (connectivity fallback).
