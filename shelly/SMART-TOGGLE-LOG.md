# Smart Toggle: Shelly + IKEA Zigbee Bulbs — Project Log

## Original Goal

Many IKEA Zigbee bulbs are wired through Shelly relays controlled by physical wall
toggle switches. By default ("dumb" mode), the wall switch cuts power to the bulb via
the relay. We wanted the physical switch to trigger a **smart toggle** — toggling the
bulb via Zigbee through Home Assistant — while keeping the relay always on so the bulb
stays powered and controllable from HA, automations, voice, etc.

## Architecture

- **Shelly Gen2/3/4 relays** in detached mode (relay ignores wall switch)
- **IKEA Dirigera + Zigbee bulbs** controlled through HA
- **Home Assistant** orchestrates: watches Shelly input state changes → toggles paired lights
- **Single HA automation** (`smart_toggle_shelly_zigbee`) handles all pairs via a mapping dict

### How It Works

1. Shelly is set to **detached mode** (`in_mode: detached`) — relay stays on, wall switch
   only changes the `binary_sensor` input state
2. HA's native Shelly integration maintains a persistent **WebSocket** to each Shelly —
   input state changes arrive in milliseconds
3. The HA automation triggers on any input state change (edge mode — both on→off and
   off→on count as a toggle)
4. The automation looks up the paired light(s) and toggles them

## Three-Tier Fallback Design (Original Plan)

We designed a three-tier system, though only Tier 1 has been implemented so far:

| Tier  | Scenario                        | Mechanism                                                           | Status         |
| ----- | ------------------------------- | ------------------------------------------------------------------- | -------------- |
| **1** | HA + Zigbee working             | Detached mode + HA automation toggles bulb via Zigbee               | ✅ Done        |
| **2** | HA up, Zigbee/bulb unresponsive | Shelly mJS script with ack timeout → falls back to relay toggle     | ⬜ Not started |
| **3** | HA completely down              | Shelly detects HA disconnect → reverts to follow mode (dumb switch) | ⬜ Not started |

## Phase 1: Implementation Log

### Step 1: First Test Pair (lights-office)

- **Shelly**: shelly1g4-7c2c677f6410 at 192.168.1.24 (later static .180)
- **Light**: light.lampteras_14 (Γραφείο τοίχος)
- Set Shelly output 0 to detached mode via `Switch.SetConfig`
- Created HA automation `smart_toggle_shelly_zigbee` with a `shelly_to_light` mapping
- **Result**: Wall switch toggles bulb instantly via Zigbee. Success.

### Step 2: Scaling to More Pairs

Added pairs one at a time using `smart-toggle-setup.py`:

| #   | Shelly IP  | Output | Input Entity                                     | Light Entity                 | Name              |
| --- | ---------- | ------ | ------------------------------------------------ | ---------------------------- | ----------------- |
| 1   | .180       | 0      | `binary_sensor.lights_office_input_0`            | `light.lampteras_14`         | Γραφείο τοίχος    |
| 2   | .5 → .183  | 0      | `binary_sensor.shelly2pmg4_7c2c677b4524_input_0` | `light.upnodomatio`          | Υπνοδωμάτιο       |
| 3   | .25 → .185 | 0      | `binary_sensor.shelly2pmg4_7c2c6779fe50_input_0` | `light.poluelaios_2`         | Αριστερά          |
| 4   | .181       | 1      | `binary_sensor.shelly2pmg4_7c2c677b223c_input_1` | Μπάνιο ροζ σποτ (2 bulbs)    | Μπάνιο ροζ        |
| 5   | .194       | 0      | `binary_sensor.shelly2pmg3_8cbfea9e6e60_input_0` | Μπάνιο μάστερ σποτ (2 bulbs) | Μπάνιο μάστερ     |
| 6   | .196       | 0      | `binary_sensor.lights_dining_input_0`            | `light.trapezaria_toixos`    | Τραπεζαρία τοίχος |

### Step 3: Grouped Lights (Bathroom Spots)

Two pairs have **two physical bulbs per switch**:

- **Μπάνιο ροζ**: `light.mpanio_roz_spot_eisodos` + `light.mpanio_roz_spot_ntouz`
- **Μπάνιο μάστερ**: `light.master_mpanio_eisodos` + `light.master_mpanio_ntouz`

Initially created HA **light groups** (`light.mpanio_roz_spot`, `light.mpanio_master_spot`)
and pointed the automation at the groups.

**Gotcha: Group latency** — Toggling a light group is slower than toggling individual
bulbs because HA sends Zigbee commands sequentially through the group entity. We expanded
the groups to individual bulbs directly in the automation mapping for better responsiveness.

### Step 4: Transition Time

IKEA bulbs have a default fade-in/fade-out when toggled, which feels laggy for a wall switch.

**Fix**: Added `transition: 0` to both `light.turn_on` and `light.turn_off` in the
automation for instant switching.

### Step 5: Turn-On Brightness & Color Temperature

Changed the automation from `light.toggle` to explicit `light.turn_on` / `light.turn_off`
with a choose block:

- If any target light is on → turn all off
- If all off → turn on at **default 75% brightness, 2700K** (warm white)

Variables in the automation:

- `default_brightness_pct: 75`
- `default_color_temp_kelvin: 2700`

**Planned but not yet implemented**: `light_overrides` — a per-light dict to customize
brightness/color temp for individual lights. This would require looping over lights
individually when overrides differ from defaults.

## Gotchas & Lessons Learned

### Shelly Detached Mode

- `in_mode: detached` on a Shelly 2PM means each output has its own mode — you must
  set it per output ID (0 or 1)
- Some Shellys were in `flip` mode (toggles relay on each press) — had to explicitly
  set to `detached`
- **Always ensure relay is ON** after detaching — otherwise the bulb has no power
  and Zigbee can't reach it. The script checks this automatically.

### HA Automation: Variable Chaining Bug

Early version used chained template variables:

```yaml
variables:
  target_lights: "{{ shelly_to_lights.get(trigger.entity_id, []) }}"
  any_on: "{{ target_lights | select('is_state', 'on') | list | count > 0 }}"
```

**Problem**: `any_on` references `target_lights`, but by HA's evaluation time, `target_lights`
renders as a string (not a list), causing `is_state` filter to fail silently. Some lights
got stuck on.

**Fix**: Compute everything inline from the original dict, no variable chaining:

```yaml
value_template: >
  {{ shelly_to_lights.get(trigger.entity_id, []) | select('is_state', 'on') | list | count > 0 }}
```

### HA Automation: triggers vs trigger, actions vs action

HA returns automation config with `triggers`/`actions` (new format) but some API versions
accept `trigger`/`action` (old format). The `smart-toggle-setup.py` script has a
`normalize_automation_config()` function to handle both.

### Static IPs

All 25 Shellys were assigned static IPs in the .180-.204 range directly via the Shelly
`Wifi.SetConfig` RPC (not router DHCP reservations). See `set-static-ips.py`.

**Exception: Shelly Wall Display** — firmware bug on platform vXD10000M (Gen2, fw 2.5.6)
causes it to ignore static IP settings. Workaround: DHCP reservation on the ZTE router.
The Wall Display ended up at .179 (adjacent to the .180 block). The router rejects DHCP
bindings for IPs inside its DHCP pool range (above .179), which is why .191 failed but
.179 worked.

### ZTE F670L Router DHCP Limit

The router has a hard limit of **10 static DHCP bindings**. This prompted the pivot to
setting static IPs on the Shellys themselves rather than using router reservations.

### .ha-token Location

The `smart-toggle-setup.py` script loads the HA token from `Path(__file__).parent / ".ha-token"`.
After reorganizing files into `shelly/`, the token file at the project root wasn't found.
Fix: pass `HA_TOKEN` env var or symlink/copy the token file.

## Tooling

| Script                            | Purpose                                                                    |
| --------------------------------- | -------------------------------------------------------------------------- |
| `shelly/shelly_client.py`         | Shelly Gen2+ RPC client — reusable functions (rpc, detach, get/set config) |
| `shelly/smart-toggle-setup.py`    | All-in-one: detach, add-pair, show-pairs, revert, status                   |
| `shelly/bathroom-mirror-setup.py` | Bathroom-specific: detach vent, mirror mode, input flip, overrides         |
| `shelly/ventilation-setup.py`     | Hourly ventilation fan automation setup                                    |
| `shelly/smart-toggle-pairs.json`  | Local record of all configured pairs                                       |
| `shelly/discover-shellys.py`      | Discover Shelly devices on the network                                     |
| `shelly/set-static-ips.py`        | Bulk assign static IPs to all Shellys                                      |
| `shelly/shelly-inventory.json`    | 25 Shelly devices with IPs, MACs, static_ip assignments                    |
| `wall-display/ha_client.py`       | Generic HA REST + WebSocket client                                         |
| `zte/zte_router.py`               | ZTE F670L router login, DHCP binding CRUD                                  |
| `wall-display/ha_client.py`       | Generic HA REST + WebSocket client                                         |

## Current Automation State

The automation `smart_toggle_shelly_zigbee` uses:

- **Mode**: parallel (max 20) — handles simultaneous button presses across rooms
- **Mapping**: `shelly_to_lights` dict, values are lists (supports 1 or many lights per switch)
- **Turn-on defaults**: 75% brightness, 2700K color temp, transition 0
- **Turn-off**: transition 0
- **Logic**: choose block — if any target light is on, turn all off; otherwise turn all on

## 2026-03-15: Mirror Mode + Ventilation Detach

### Problem

Both bathrooms have a **double wall switch**: one half controls lights (IKEA Zigbee via
detached Shelly), the other half controls ventilation (Shelly relay in follow mode).

Two issues emerged:

1. **Light switch desync**: The smart toggle automation treats every input state change as
   a toggle. With a physical toggle switch (not momentary), if one event is missed or
   fires twice, the switch position permanently desynchronises from the light state. This
   was observed intermittently in both bathrooms.

2. **Ventilation switch desync**: The ventilation relay is in follow mode, but the hourly
   ventilation automation (`ventilation-setup.py`) calls `switch.turn_on`/`switch.turn_off`
   via HA. If the automation turns the fan off while the physical switch is in the "on"
   position, the relay turns off but the switch remains up — now they're out of sync until
   the next manual toggle cycle.

### Solution

**Lights — mirror mode**: For bathroom inputs, instead of "state changed → toggle", use
"state is ON → lights ON, state is OFF → lights OFF". This is self-correcting: even if
an event is lost, the next flip re-syncs. Added a `mirror_inputs` list to the smart toggle
automation variables.

Understood limitation: if someone toggles the light from HA UI or voice, the physical
switch position won't match — but this is the same as a dumb switch. Next flip re-syncs.

**Ventilation — detach + mirror**: Put both ventilation outputs in detached mode and add a
separate mirror automation for ventilation switches. This way:

- Physical switch → HA → relay (small network round-trip, acceptable for a fan)
- Hourly automation can turn fan on/off independently
- Physical switch always re-syncs on next flip

Understood limitation: ventilation may be on when switch is off (due to automation), but
an on/off cycle of the physical switch will override it.

**Light overrides**: Added `light_overrides` dict. Pink bathroom spots set to 100%
brightness (both bulbs same).

### Physical Layout

**Pink Bathroom — Shelly 2PM Gen4 at .181**

| Output | Input   | Function                                | Mode                   |
| ------ | ------- | --------------------------------------- | ---------------------- |
| 0      | input_0 | Ventilation (`switch.ventilation_pink`) | follow → **detached**  |
| 1      | input_1 | Lights (2 IKEA spots)                   | detached (mirror mode) |

**Master Bathroom — Shelly 2PM Gen3 at .194 (inputs flipped so left=vent, right=lights matches pink)**

| Output | Input                 | Function                                                 | Mode                   |
| ------ | --------------------- | -------------------------------------------------------- | ---------------------- |
| 0      | **input_1** (flipped) | Lights (2 IKEA spots)                                    | detached (mirror mode) |
| 1      | **input_0** (flipped) | Ventilation (`switch.shelly2pmg3_8cbfea9e6e60_output_1`) | follow → **detached**  |

### Design Decision: Two Automations, Not One

After analysis of automation modes (`parallel`, `queued`, `restart`, `single`), we decided
to split into **two separate automations**:

**1. Mirror automation** (`mode: parallel`)

- For: bathroom lights + ventilation switches
- Logic: input ON → turn on, input OFF → turn off. Uses `trigger.to_state.state` only —
  no dependency on current light state, so parallel runs can't race.
- `parallel` is safe here because each run acts on the trigger value, not shared state.

**2. Toggle automation** (`mode: queued`)

- For: all other rooms (office, kid room, dining, etc.)
- Logic: any target light on → turn all off; all off → turn on with defaults.
- `queued` ensures sequential execution so each toggle sees the result of the previous
  one. This prevents a race where rapid presses (e.g. a kid triple-tapping) both read
  "lights are OFF" simultaneously and both try to turn on.
- Cross-switch queuing delay is negligible (~100ms) since Zigbee commands are fast.

### Design Decision: Light Settings Attached to Input, Not Light

Light settings (brightness, color temp) are keyed by **input entity** (the switch), not
by individual light entity. This is simpler and matches reality: one switch controls one
or more lights that always share the same settings.

```
input_settings = {
    "binary_sensor.shelly2pmg4_7c2c677b223c_input_1": {
        "brightness_pct": 100,
        "color_temp_kelvin": 2700,
    },
    # other inputs fall back to defaults
}
```

This eliminates the need for `repeat: for_each` with per-light overrides. A single
`light.turn_on` call targets all lights for a switch with the same brightness/color:

```yaml
- action: light.turn_on
  target:
    entity_id: "{{ shelly_to_lights.get(trigger.entity_id, []) }}"
  data:
    brightness_pct: "{{ input_settings.get(trigger.entity_id, {}).get('brightness_pct', default_brightness_pct) }}"
    color_temp_kelvin: "{{ input_settings.get(trigger.entity_id, {}).get('color_temp_kelvin', default_color_temp_kelvin) }}"
```

### Other Options Considered but Not Pursued

- **`light_profiles.csv`**: HA feature for per-light defaults, but uses CIE XY coords
  (not color_temp_kelvin), lives in HA config dir (not this repo), and only applies when
  calling `light.turn_on` without explicit params. Awkward to maintain.
- **IKEA bulb firmware defaults**: `on_level` (startup brightness) works reliably via ZHA,
  but `StartUpColorTemperatureMireds` is buggy on IKEA bulbs (only works for physical
  power cycling, not Zigbee on/off). Could be used as belt-and-suspenders later.
- **`parallel` inside `repeat: for_each`**: Not supported by HA. Loop iterations always
  run sequentially. Moot since we no longer need per-light loops.

## Next Steps

- [ ] Phase 2: Shelly mJS ack script for Zigbee failure fallback
- [ ] Phase 3: HA-down detection + automatic revert to dumb switch mode
- [ ] Scale to more rooms as new bulbs are installed
