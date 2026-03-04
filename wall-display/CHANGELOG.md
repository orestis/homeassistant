# Changelog

## 0.23.0

- **WD curve solar correction**: Automatically compensates for solar gain on
  the Antlia outdoor sensor by comparing it with the sheltered Daikin AP
  sensor. When Antlia reads >2°C higher, the leaving water offset is
  increased to maintain correct heating output.
  - New entities: `input_number.heating_base_offset`,
    `input_number.wd_solar_correction`, `input_datetime.wd_last_write`
  - New automation: `automation.wd_solar_correction` (runs every 10 min +
    on sensor changes)
  - Dashboard: offset buttons now control base offset; solar correction
    shown as ☀️ indicator
  - Rate-limited writes: 30-min cooldown with hysteresis bypass for large
    changes
  - Setup: `python setup-wd-correction.py` to create entities + automation

## 0.22.0

- Water heater auto-off with timer and bypass mode
- Night tariff schedule display (winter/summer windows)

## 0.21.0

- Scene activation with active scene highlighting
- Weather forecast with severity-based icon selection

## 0.20.0

- Initial wall display dashboard
- Indoor/outdoor temperature and humidity
- Climate offset control (leaving water temperature)
- htmx partial updates every 30s with version-based full reload
