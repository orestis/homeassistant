"""Wall Display Dashboard — Flask application."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, make_response, redirect, render_template, request, url_for

from ha_client import HAClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# --- Load config ---

CONFIG_PATH = Path(__file__).parent / "dashboard_config.json"
with open(CONFIG_PATH) as f:
    config = json.load(f)

# HA connection: inside an add-on, use Supervisor API + SUPERVISOR_TOKEN.
# For local dev, set HA_URL and HA_TOKEN env vars.
ha_url = os.environ.get("HA_URL", config.get("ha_url", "http://supervisor/core"))
ha_token = os.environ.get("HA_TOKEN") or os.environ.get("SUPERVISOR_TOKEN", "")

# Debug: log available env vars (filtered for security)
log.info("Available env vars: %s", [k for k in os.environ if k.startswith(("SUPERVISOR", "HA_", "HASSIO"))])
log.info("SUPERVISOR_TOKEN present: %s (len=%d)", bool(ha_token), len(ha_token))

if not ha_token:
    log.warning("No HA token found — set HA_TOKEN or SUPERVISOR_TOKEN env var")

ha = HAClient(ha_url, ha_token)

# --- App version (hash of templates + static files for cache-busting) ---

def _compute_app_version() -> str:
    """Hash all template and static files to detect deploys."""
    h = hashlib.sha256()
    root = Path(__file__).parent
    for directory in (root / "templates", root / "static"):
        if directory.is_dir():
            for f in sorted(directory.rglob("*")):
                if f.is_file():
                    h.update(f.read_bytes())
    # Also include app.py itself
    h.update((root / "app.py").read_bytes())
    return h.hexdigest()[:12]

APP_VERSION = _compute_app_version()
log.info("App version: %s", APP_VERSION)

# --- Flask app ---

app = Flask(__name__)


def _get_dashboard_state() -> dict:
    """Fetch all entity states needed for the dashboard."""
    sensors = config.get("sensors", {})
    climate_cfg = config.get("climate", {})

    # Collect all entity IDs we need
    entity_ids = list(sensors.values())
    if climate_cfg.get("entity_id"):
        entity_ids.append(climate_cfg["entity_id"])
    if climate_cfg.get("outdoor_unit_temp"):
        entity_ids.append(climate_cfg["outdoor_unit_temp"])
    scene_list = config.get("scenes", [])
    scene_entity_ids = [s["entity_id"] for s in scene_list]
    entity_ids.extend(scene_entity_ids)

    # Water heater entities
    wh_cfg = config.get("water_heater", {})
    for key in ("switch_entity", "timer_entity", "bypass_entity"):
        eid = wh_cfg.get(key, "")
        if eid:
            entity_ids.append(eid)

    # WD correction entities
    wd_cfg = config.get("wd_correction", {})
    for key in ("base_offset_entity", "solar_correction_entity"):
        eid = wd_cfg.get(key, "")
        if eid:
            entity_ids.append(eid)

    # Night tariff sensor
    tariff_cfg = config.get("night_tariff", {})
    if tariff_cfg.get("entity_id"):
        entity_ids.append(tariff_cfg["entity_id"])

    states = ha.get_states(entity_ids)

    # Parse sensor values
    indoor_temp = _parse_float(states.get(sensors.get("indoor_temp", ""), {}))
    indoor_humidity = _parse_float(states.get(sensors.get("indoor_humidity", ""), {}))
    outdoor_temp = _parse_float(states.get(sensors.get("outdoor_temp", ""), {}))

    # Parse climate state
    climate_state = states.get(climate_cfg.get("entity_id", ""), {})
    climate_target = None
    climate_current = None
    climate_action = None
    climate_outdoor_unit = None
    if climate_state:
        attrs = climate_state.get("attributes", {})
        climate_target = attrs.get("temperature")
        climate_current = attrs.get("current_temperature")
        climate_action = climate_state.get("state", "unknown")
    if climate_cfg.get("outdoor_unit_temp"):
        climate_outdoor_unit = _parse_float(
            states.get(climate_cfg["outdoor_unit_temp"], {}))

    # Generate offset button list with colors
    min_val = climate_cfg.get("min", -10)
    max_val = climate_cfg.get("max", 10)
    button_values = climate_cfg.get("button_values", list(range(min_val, max_val + 1)))
    offset_buttons = []
    for v in button_values:
        # Blue (cold) → grey (neutral) → orange (hot)
        t = (v - min_val) / (max_val - min_val) if max_val != min_val else 0.5
        if t < 0.5:
            # Blue to grey: #2196F3 → #555
            f = t * 2
            r = int(33 + f * (85 - 33))
            g = int(150 + f * (85 - 150))
            b = int(243 + f * (85 - 243))
        else:
            # Grey to orange: #555 → #E65100
            f = (t - 0.5) * 2
            r = int(85 + f * (230 - 85))
            g = int(85 + f * (81 - 85))
            b = int(85 + f * (0 - 85))
        offset_buttons.append({"value": v, "color": f"#{r:02x}{g:02x}{b:02x}"})

    # Determine active scene (most recently changed)
    active_scene = None
    latest_time = None
    for eid in scene_entity_ids:
        scene_state = states.get(eid, {})
        last_changed = scene_state.get("last_changed", "")
        if last_changed:
            try:
                dt = datetime.fromisoformat(last_changed)
                if latest_time is None or dt > latest_time:
                    latest_time = dt
                    active_scene = eid
            except (ValueError, TypeError):
                pass

    # Weather forecast — summary for next 12 hours
    weather_entity = config.get("weather_entity", "")
    forecast_summary = None
    if weather_entity:
        try:
            forecasts = ha.get_weather_forecast(weather_entity, "hourly")
            now = datetime.now(timezone.utc)
            temps: list[float] = []
            conditions: list[str] = []

            for fc in forecasts:
                try:
                    fc_dt = datetime.fromisoformat(fc.get("datetime", ""))
                    hours_ahead = (fc_dt - now).total_seconds() / 3600
                    if 0 <= hours_ahead <= 12:
                        t = fc.get("temperature")
                        if t is not None:
                            temps.append(float(t))
                        cond = fc.get("condition", "")
                        if cond:
                            conditions.append(cond)
                except (ValueError, TypeError):
                    continue

            if temps:
                # Pick the most "important" condition (worst weather wins)
                condition_icon = _pick_forecast_icon(conditions)
                forecast_summary = {
                    "low": int(min(temps)),
                    "high": int(max(temps)),
                    "icon": condition_icon,
                }
        except Exception as e:
            log.warning("Failed to fetch weather forecast: %s", e)

    # Water heater state
    wh_cfg = config.get("water_heater", {})
    water_heater = None
    if wh_cfg.get("switch_entity"):
        wh_switch = states.get(wh_cfg["switch_entity"], {})
        wh_timer = states.get(wh_cfg.get("timer_entity", ""), {})
        wh_bypass = states.get(wh_cfg.get("bypass_entity", ""), {})

        switch_on = wh_switch.get("state") == "on"
        timer_active = wh_timer.get("state") == "active"
        bypass_on = wh_bypass.get("state") == "on"

        # Compute remaining minutes from timer's finishes_at attribute
        remaining_min = None
        if timer_active:
            finishes_at = wh_timer.get("attributes", {}).get("finishes_at", "")
            if finishes_at:
                try:
                    finish_dt = datetime.fromisoformat(finishes_at)
                    now_utc = datetime.now(timezone.utc)
                    remaining_sec = (finish_dt - now_utc).total_seconds()
                    remaining_min = max(0, int(remaining_sec / 60))
                except (ValueError, TypeError):
                    pass

        water_heater = {
            "switch_entity": wh_cfg["switch_entity"],
            "timer_entity": wh_cfg.get("timer_entity", ""),
            "bypass_entity": wh_cfg.get("bypass_entity", ""),
            "name": wh_cfg.get("name", "Water Heater"),
            "icon": wh_cfg.get("icon", "🚿"),
            "switch_on": switch_on,
            "timer_active": timer_active,
            "bypass_on": bypass_on,
            "remaining_min": remaining_min,
            "timer_duration": wh_cfg.get("timer_duration", "00:30:00"),
        }

    # Night tariff info
    night_tariff = None
    if tariff_cfg.get("entity_id"):
        tariff_state = states.get(tariff_cfg["entity_id"], {})
        tariff_on = tariff_state.get("state") == "on"
        local_now = datetime.now()
        tariff_schedule = _get_tariff_schedule_info(local_now, tariff_on)
        night_tariff = {
            "active": tariff_on,
            "name": tariff_cfg.get("name", "Νυχτ."),
            **tariff_schedule,
        }
    else:
        local_now = datetime.now()

    # WD correction values
    base_offset = 0
    solar_correction = 0
    if wd_cfg.get("base_offset_entity"):
        bo_state = states.get(wd_cfg["base_offset_entity"], {})
        try:
            base_offset = int(float(bo_state.get("state", "0")))
        except (ValueError, TypeError):
            pass
    if wd_cfg.get("solar_correction_entity"):
        sc_state = states.get(wd_cfg["solar_correction_entity"], {})
        try:
            solar_correction = int(float(sc_state.get("state", "0")))
        except (ValueError, TypeError):
            pass

    return {
        "indoor_temp": indoor_temp,
        "indoor_humidity": indoor_humidity,
        "outdoor_temp": outdoor_temp,
        "now_time": local_now.strftime("%H:%M"),
        "now_date": _format_date_greek(local_now),
        "forecast": forecast_summary,
        "night_tariff": night_tariff,
        "active_scene": active_scene,
        "climate": {
            "entity_id": climate_cfg.get("entity_id", ""),
            "name": climate_cfg.get("name", "Climate"),
            "target": climate_target,
            "current": climate_current,
            "outdoor_unit": climate_outdoor_unit,
            "action": climate_action,
            "min": climate_cfg.get("min", -10),
            "max": climate_cfg.get("max", 10),
            "step": climate_cfg.get("step", 1),
        },
        "offset_buttons": offset_buttons,
        "scenes": scene_list,
        "water_heater": water_heater,
        "base_offset": base_offset,
        "solar_correction": solar_correction,
    }


# ---------------------------------------------------------------------------
# Night tariff schedule computation
# ---------------------------------------------------------------------------

# Windows as (start_hour, end_hour_exclusive)
_TARIFF_WINTER = [(3, 6), (12, 16)]   # Nov-Mar
_TARIFF_SUMMER = [(2, 5), (11, 16)]   # Apr-Oct


def _get_tariff_schedule_info(now: datetime, is_active: bool) -> dict:
    """Compute next-window / remaining-time info for the night tariff.

    Returns a dict with:
      - remaining  : "H:MM" string if active (time left in window)
      - next_start : "HH:MM" string if inactive (next window start)
      - next_dur_h : int hours of the next window
    """
    month, hour, minute = now.month, now.hour, now.minute
    windows = _TARIFF_WINTER if month in (11, 12, 1, 2, 3) else _TARIFF_SUMMER
    now_minutes = hour * 60 + minute

    if is_active:
        # Find which window we're in and compute remaining time
        for start_h, end_h in windows:
            if start_h <= hour < end_h:
                remaining = end_h * 60 - now_minutes
                rh, rm = divmod(remaining, 60)
                return {"remaining": f"{rh}:{rm:02d}"}
        # Sensor says on but we can't match a window — just show active
        return {"remaining": None}

    # Inactive — find the next window
    best_wait = None
    best_dur = 0
    best_start_h = 0
    for start_h, end_h in windows:
        start_min = start_h * 60
        if start_min > now_minutes:
            wait = start_min - now_minutes
        else:
            wait = (24 * 60 - now_minutes) + start_min  # wraps to tomorrow
        if best_wait is None or wait < best_wait:
            best_wait = wait
            best_dur = end_h - start_h
            best_start_h = start_h

    return {
        "remaining": None,
        "next_start": f"{best_start_h:02d}:00",
        "next_dur_h": best_dur,
    }


# HA condition → display icon, ordered by severity (worst first)
# clear-night is treated as equivalent to sunny (not "worse" weather)
_CONDITION_PRIORITY = [
    ("lightning-rainy", "⛈️"),
    ("lightning", "🌩️"),
    ("pouring", "🌧️"),
    ("rainy", "🌧️"),
    ("hail", "🌨️"),
    ("snowy-rainy", "🌨️"),
    ("snowy", "❄️"),
    ("windy-variant", "💨"),
    ("windy", "💨"),
    ("fog", "🌫️"),
    ("cloudy", "☁️"),
    ("partlycloudy", "⛅"),
    ("exceptional", "⚠️"),
]


def _pick_forecast_icon(conditions: list[str]) -> str:
    """Pick the most severe weather icon from a list of HA conditions.

    Severity-based: the worst upcoming condition wins.
    For the clear-sky fallback, we check whether the majority of forecast
    hours are nighttime (clear-night) and return 🌙 instead of ☀️.
    """
    # Normalise night → day for severity comparison only
    cond_set = {("sunny" if c == "clear-night" else c) for c in conditions}
    for cond, icon in _CONDITION_PRIORITY:
        if cond in cond_set:
            return icon
    # Clear sky fallback — pick moon or sun based on forecast hours
    night_count = sum(1 for c in conditions if c == "clear-night")
    if night_count > len(conditions) / 2:
        return "🌙"
    return "☀️"


_DAYS_EL = ["Δευ", "Τρι", "Τετ", "Πεμ", "Παρ", "Σαβ", "Κυρ"]
_MONTHS_EL = [
    "", "Ιαν", "Φεβ", "Μαρ", "Απρ", "Μαΐ", "Ιουν",
    "Ιουλ", "Αυγ", "Σεπ", "Οκτ", "Νοε", "Δεκ",
]


def _format_date_greek(dt: datetime) -> str:
    """Format a date in short Greek, e.g. 'Τρι 3 Μαρ'."""
    return f"{_DAYS_EL[dt.weekday()]} {dt.day} {_MONTHS_EL[dt.month]}"


def _parse_float(state_dict: dict) -> float | None:
    """Extract a float value from a HA state dict."""
    if not state_dict:
        return None
    try:
        return float(state_dict.get("state", ""))
    except (ValueError, TypeError):
        return None


@app.route("/")
def index():
    """Render the full dashboard page."""
    state = _get_dashboard_state()
    is_htmx = request.headers.get("HX-Request") == "true"
    if is_htmx:
        # If the client's version doesn't match, force a full page reload
        # so new styles / scripts / layout changes are picked up.
        client_version = request.headers.get("X-App-Version", "")
        if client_version and client_version != APP_VERSION:
            log.info("Version mismatch (client=%s, server=%s) — forcing full reload", client_version, APP_VERSION)
            resp = make_response("", 200)
            resp.headers["HX-Refresh"] = "true"
            return resp
        return render_template("partials/dashboard_content.html", **state)
    return render_template("dashboard.html", **state, app_version=APP_VERSION)


@app.route("/action/scene", methods=["POST"])
def action_scene():
    """Activate a scene."""
    entity_id = request.form.get("entity_id") or ""
    if entity_id.startswith("scene."):
        log.info("Activating scene: %s", entity_id)
        ha.activate_scene(entity_id)

    # htmx: return updated dashboard content
    if request.headers.get("HX-Request") == "true":
        state = _get_dashboard_state()
        return render_template("partials/dashboard_content.html", **state)

    # Fallback: POST-Redirect-GET
    return redirect(url_for("index"))


@app.route("/action/climate", methods=["POST"])
def action_climate():
    """Adjust base heating offset.

    Writes to input_number.heating_base_offset instead of the climate
    entity directly.  The HA automation picks up the change and applies
    base + solar correction to the actual climate entity.
    """
    climate_cfg = config.get("climate", {})
    wd_cfg = config.get("wd_correction", {})
    base_offset_entity = wd_cfg.get("base_offset_entity", "")
    min_val = climate_cfg.get("min", -10)
    max_val = climate_cfg.get("max", 10)

    # Accept direct value from slider, or up/down from buttons
    value = request.form.get("value")
    action = request.form.get("action") or ""

    if base_offset_entity:
        if value is not None:
            try:
                new_target = int(float(value))
                new_target = max(min_val, min(max_val, new_target))
                log.info("Setting base offset %s to %s", base_offset_entity, new_target)
                ha.set_input_number(base_offset_entity, new_target)
            except (ValueError, TypeError):
                log.warning("Invalid climate value: %s", value)
        elif action in ("up", "down"):
            step = climate_cfg.get("step", 1)
            current_state = ha.get_state(base_offset_entity)
            if current_state:
                try:
                    current_val = int(float(current_state.get("state", "0")))
                except (ValueError, TypeError):
                    current_val = 0
                new_target = current_val + (step if action == "up" else -step)
                new_target = max(min_val, min(max_val, new_target))
                log.info("Setting base offset: %s → %s", current_val, new_target)
                ha.set_input_number(base_offset_entity, new_target)

    # htmx: return updated dashboard content
    if request.headers.get("HX-Request") == "true":
        state = _get_dashboard_state()
        return render_template("partials/dashboard_content.html", **state)

    # Fallback: POST-Redirect-GET
    return redirect(url_for("index"))


@app.route("/action/water_heater", methods=["POST"])
def action_water_heater():
    """Toggle the water heater switch, or toggle bypass mode."""
    action = request.form.get("action", "")
    wh_cfg = config.get("water_heater", {})
    switch_eid = wh_cfg.get("switch_entity", "")
    timer_eid = wh_cfg.get("timer_entity", "")
    bypass_eid = wh_cfg.get("bypass_entity", "")
    duration = wh_cfg.get("timer_duration", "00:30:00")

    if action == "on" and switch_eid:
        log.info("Water heater ON + timer start")
        ha.call_service("switch", "turn_on", {"entity_id": switch_eid})
        if bypass_eid:
            ha.call_service("input_boolean", "turn_off", {"entity_id": bypass_eid})
        if timer_eid:
            ha.call_service("timer", "start", {"entity_id": timer_eid, "duration": duration})

    elif action == "off" and switch_eid:
        log.info("Water heater OFF")
        ha.call_service("switch", "turn_off", {"entity_id": switch_eid})
        if timer_eid:
            ha.call_service("timer", "cancel", {"entity_id": timer_eid})
        if bypass_eid:
            ha.call_service("input_boolean", "turn_off", {"entity_id": bypass_eid})

    elif action == "bypass":
        # Toggle bypass: if enabling bypass → cancel timer; if disabling → restart timer
        if bypass_eid:
            bypass_state = ha.get_state(bypass_eid)
            currently_on = bypass_state and bypass_state.get("state") == "on"
            if currently_on:
                log.info("Water heater bypass OFF — restarting timer")
                ha.call_service("input_boolean", "turn_off", {"entity_id": bypass_eid})
                if timer_eid:
                    ha.call_service("timer", "start", {"entity_id": timer_eid, "duration": duration})
            else:
                log.info("Water heater bypass ON — cancelling timer")
                ha.call_service("input_boolean", "turn_on", {"entity_id": bypass_eid})
                if timer_eid:
                    ha.call_service("timer", "cancel", {"entity_id": timer_eid})

    # htmx: return updated dashboard content
    if request.headers.get("HX-Request") == "true":
        state = _get_dashboard_state()
        return render_template("partials/dashboard_content.html", **state)

    # Fallback: POST-Redirect-GET
    return redirect(url_for("index"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", config.get("server_port", 5000)))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
