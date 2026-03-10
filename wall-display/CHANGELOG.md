# Changelog

## 0.27.0

- **HA Ingress support**: All URLs are now dynamically prefixed with the
  ingress base path (from `X-Ingress-Path` header), so the dashboard
  works over HTTPS when accessed via the companion app or HA sidebar.
  Direct HTTP access (e.g. from the wall display itself) continues to
  work unchanged.

## 0.26.0

- **Ventilation fan indicators**: Two tiny fan icons in the status bar show
  bathroom ventilation status (pink bathroom / master bathroom).
  Dim grey when off, steady color when manually on, pulsing when
  automation-triggered.
- **WebSocket support in ha_client**: Added `ws_command_sync()` for
  WebSocket API operations (helper creation, config commands, etc.).
- **websockets** added to requirements.txt

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
