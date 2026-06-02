# Changelog

## 0.39.2

- **AC button loading state**: AC control-page buttons now show the shared
  spinner while their request is in flight (the `htmx-request` spinner CSS was
  only in the dashboard template, not the AC page).

## 0.39.1

- **Split AC control pages**: Each cooling-fleet unit on the dashboard is now a
  tappable card opening a per-unit control page (`/ac/<id>`) with power (off ↔
  cool), a 0.5° temperature stepper, and an auto-off timer (0.5–8h, 0.5h steps).
  60s inactivity auto-returns to the dashboard. The back button uses
  `history.back()` for an instant return (no full dashboard re-render).
- **Seasonal visibility**: The cooling row now shows whenever the heat pump is
  **not** heating (instead of only when an AC is already running), so units can
  be turned on from the UI. Heating and cooling rows are mutually exclusive.
- **Sleep mode (bedrooms)**: Master & Kids get a Sleep button — a macro that sets
  cool + 26° + quiet fan + Comfort Airflow (`windnice`) + a 2h off-timer.
  (Onecta exposes no native sleep/econo/comfort preset on these units.)
- Timers are backed by per-unit `timer.ac_<id>_auto_off` helpers + auto-off
  automations (see `setup-ac-timers.py`).

## 0.33.0

- **Notify button**: New red phone-icon button for sending iOS push
  notifications via HA Companion app. Includes confirmation overlay
  with 3-second countdown and 60-second server-side cooldown to prevent
  accidental or repeated presses.
- **Layout reorder**: Water heater row moved above scenes (below status bar).
- Auto-discovers `notify.mobile_app_*` service from HA.

## 0.29.0

- **Fix background refresh spinner**: The loading spinner no longer
  replaces the entire dashboard during the 30-second auto-refresh.
  Spinners are now scoped to buttons only.

## 0.28.0

- **Button loading spinners**: All interactive buttons now show a visible
  spinning ring indicator during HA API calls, replacing content with a
  spinner. Buttons are disabled during requests to prevent double-presses.
- **iPhone scroll fix**: Dashboard uses `dvh` viewport units and allows
  vertical scrolling, so the climate section is reachable on mobile.

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
