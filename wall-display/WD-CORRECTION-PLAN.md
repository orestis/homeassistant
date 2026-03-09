# WD Curve Solar Correction Plan

## Problem

The Daikin Antlia heat pump uses a Weather Dependent (WD) curve to compute
the target Leaving Water Temperature (LWT) based on its own outdoor sensor
(`sensor.antlia_climatecontrol_outdoor_temperature`). This sensor is exposed
to direct sunlight and reads too high on sunny-but-cold days, causing the
heat pump to under-heat.

A second outdoor sensor from a sheltered Daikin AP unit
(`sensor.daikinap68496_climatecontrol_outdoor_temperature`) gives a more
accurate reading.

## Approach

Keep the heat pump's native WD curve active (safe fallback if HA goes down).
Use an HA automation to compute the LWT error between what the heat pump
_thinks_ (using its sensor) vs what it _should be_ (using the accurate
sensor) and apply the difference as a leaving water offset correction.

The user retains manual control via a "base offset" preference. The
automation adds its solar correction on top:

```
final_offset = clamp(base_offset + solar_correction, -10, +10)
```

## WD Curve

- 50°C LWT @ 2°C outdoor
- 25°C LWT @ 18°C outdoor
- Slope: -25/16 = -1.5625
- Formula: LWT(T) = 53.125 - 1.5625 × T

### Sensor Characteristics

Analysis of ~1,870 aligned hourly readings (Dec 2025 – Mar 2026):

- **Antlia**: integer-only resolution (1.0°C steps), updates ~every 30min
- **Daikin AP**: 0.5°C resolution, updates ~every 30min
- Delta resolution: 0.5°C increments (from combining 1.0 + 0.5° sensors)
- High correlation (0.95), no significant temporal lag between sensors

**Nighttime baseline (22:00–05:00, no solar gain):**

- Mean delta: -0.15°C (Antlia reads slightly low — radiative cooling)
- Std dev: 0.74°C, range: -2.1 to +2.7°C
- This is sensor noise + integer rounding + microclimate differences

**Solar gain profile (hourly means):**

- Starts building ~10:00, peaks 14:00–16:00 (mean +1.6 to +1.8°C)
- Extremes: up to +9.5°C (afternoon, sunny winter day)
- Drops to zero by 19:00
- Large deviations (>2°C) occur almost exclusively between 10:00–18:00

**Monthly trend:** Solar gain increases as sun angle rises:

- Dec: mean +0.1°C, max +2.7°C
- Jan: mean +0.3°C, max +4.9°C
- Feb: mean +0.3°C, max +9.5°C
- Mar (partial): mean +1.1°C, max +8.4°C

### Correction Formula

Deadband threshold: **2.0°C, positive-only corrections.**

Rationale:

- 2.0°C is safely above the nighttime noise floor (std dev 0.74°C)
- Clean multiple of Antlia's 1°C integer resolution
- At 2.0°C threshold: 115/1870 hours triggered (6.1%), with only 7
  false positives (negative deltas) — virtually all are genuine solar events
- Negative deltas (Antlia < Daikin) are NOT corrected: the heat pump
  producing slightly more heat than needed is the safe direction
- Minimum meaningful correction at threshold: round(1.5625 × 2.0) = 3°C
  on LWT — below this, the error is within system noise

```
if (T_antlia - T_accurate) > 2.0:
    solar_correction = round(1.5625 × (T_antlia - T_accurate))
else:
    solar_correction = 0
```

Example: Antlia reads 10°C (solar gain), sheltered reads 7°C →
delta = 3.0 > 2.0 → correction = round(1.5625 × 3) = +5°C offset.

Example: Antlia reads 14°C, sheltered reads 13.5°C →
delta = 0.5 < 2.0 → correction = 0 (within noise).

## Rate Limit Constraints

Daikin Onecta cloud API: **200 calls/24h** shared between reads and writes.

| Source             | Max calls/day | Notes                          |
| ------------------ | ------------- | ------------------------------ |
| Onecta polling     | ~96           | Integration reads every ~15min |
| Auto correction    | ~48           | 30-min cooldown between writes |
| Manual button taps | ~10           | Realistic human usage          |
| **Total**          | **~154**      | Comfortably under 200          |

### Write Safeguards

1. **30-minute cooldown** between automation writes (tracked via
   `input_datetime.wd_last_write`)
2. **Hysteresis bypass**: if |desired - current| >= 2, skip cooldown
   (weather changed significantly)
3. **Manual bypass**: user button presses write immediately (but reset
   the cooldown timer for the next automated write)
4. **No-op guard**: never write if the computed final offset equals the
   current climate target

## Entities to Create

| Entity                             | Type           | Created via | Purpose                              |
| ---------------------------------- | -------------- | ----------- | ------------------------------------ |
| `input_number.heating_base_offset` | input_number   | WebSocket   | User's manual base offset (-10..+10) |
| `input_number.wd_solar_correction` | input_number   | WebSocket   | Automation's computed correction     |
| `input_datetime.wd_last_write`     | input_datetime | WebSocket   | Tracks last write time for cooldown  |
| `automation.wd_solar_correction`   | automation     | REST API    | Runs the correction logic            |

## Implementation Steps

### Step 1: Create `setup-wd-correction.py`

New setup script following the `setup-water-heater.py` pattern. Run once
from dev machine against HA.

Creates:

- `input_number.heating_base_offset` — range -10 to 10, step 1, initial 0,
  icon `mdi:tune`
- `input_number.wd_solar_correction` — range -25 to 25, step 1, initial 0,
  icon `mdi:white-balance-sunny` (written only by automation)
- `input_datetime.wd_last_write` — has_date + has_time, icon `mdi:clock`
- `automation.wd_solar_correction`:
  - **Triggers:**
    - `time_pattern` every `/10` minutes
    - State change on antlia outdoor temp (with `for: 00:01:00` debounce)
    - State change on sheltered outdoor temp (with `for: 00:01:00` debounce)
    - State change on `input_number.heating_base_offset` (immediate)
  - **Conditions:**
    - Climate entity state is not `off`
  - **Actions (Jinja templates):**
    1. Compute `delta = antlia - accurate`
    2. If `delta > 2.0`: `solar_correction = round(1.5625 × delta)`, else `0`
    3. Set `input_number.wd_solar_correction` to the computed value
    4. Compute `final = clamp(base + correction, -10, 10)`
    5. **Write guard:** only proceed if `final ≠ current climate target`
    6. **Cooldown guard:** only proceed if trigger was manual base change,
       OR `|final - current| >= 2`, OR `now() - last_write > 30 min`
    7. Call `climate.set_temperature` on `climate.antlia_leaving_water_offset`
    8. Set `input_datetime.wd_last_write` to `now()`
    9. Log to logbook for debugging
- Verification: check all entities exist and print states

### Step 2: Update `dashboard_config.json`

Add a `wd_correction` block:

```json
"wd_correction": {
  "base_offset_entity": "input_number.heating_base_offset",
  "solar_correction_entity": "input_number.wd_solar_correction"
}
```

Existing `climate` section stays unchanged.

### Step 3: Update `ha_client.py`

Add `set_input_number(entity_id, value)` method calling
`input_number/set_value` service.

### Step 4: Update `app.py`

- **`_get_dashboard_data()`**: fetch states for the two new input_number
  entities. Pass `base_offset` (int) and `solar_correction` (int) to the
  template context.
- **`/action/climate` handler**: when user taps an offset button, write to
  `input_number.heating_base_offset` instead of the climate entity directly.
  The automation fires on the state change and updates the climate entity.

### Step 5: Update `dashboard_content.html`

- Offset buttons: highlight based on `base_offset` value (not
  `climate.target`), target the base offset input_number
- Add solar correction indicator: e.g., `☀️ +3` shown small/muted between
  the info line and buttons
- Show effective offset in the climate info area:
  `Offset: -2 (base 0, ☀️ -2)`

### Step 6: Update `deploy.sh`

Add `setup-wd-correction.py` to the file copy list.

## Verification

1. Run `python setup-wd-correction.py` — creates entities + automation
2. In HA Developer Tools → States: confirm all input_number/input_datetime
   entities exist with initial values
3. Trigger automation manually — verify `wd_solar_correction` matches the
   formula and climate entity gets updated
4. On wall display: tap base offset button → confirm
   `input_number.heating_base_offset` changes → automation fires → climate
   target updates to `base + correction`
5. Monitor HA history on sunny day: correction should be positive when
   Antlia reads higher than sheltered sensor
6. Verify rate limits: check Onecta integration logs for API call counts
