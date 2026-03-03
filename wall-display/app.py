"""Wall Display Dashboard — Flask application."""

import json
import logging
import os
from pathlib import Path

from flask import Flask, redirect, render_template, request, url_for

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
    if climate_state:
        attrs = climate_state.get("attributes", {})
        climate_target = attrs.get("temperature")
        climate_current = attrs.get("current_temperature")
        climate_action = climate_state.get("state", "unknown")

    return {
        "indoor_temp": indoor_temp,
        "indoor_humidity": indoor_humidity,
        "outdoor_temp": outdoor_temp,
        "climate": {
            "entity_id": climate_cfg.get("entity_id", ""),
            "name": climate_cfg.get("name", "Climate"),
            "target": climate_target,
            "current": climate_current,
            "action": climate_action,
            "min": climate_cfg.get("min", -10),
            "max": climate_cfg.get("max", 10),
            "step": climate_cfg.get("step", 1),
        },
        "scenes": config.get("scenes", []),
    }


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
        return render_template("partials/dashboard_content.html", **state)
    return render_template("dashboard.html", **state)


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
    """Adjust climate target temperature."""
    entity_id = request.form.get("entity_id") or ""
    action = request.form.get("action") or ""
    climate_cfg = config.get("climate", {})
    step = climate_cfg.get("step", 1)
    min_val = climate_cfg.get("min", -10)
    max_val = climate_cfg.get("max", 10)

    if entity_id and action in ("up", "down"):
        # Get current target
        current_state = ha.get_state(entity_id)
        if current_state:
            current_target = current_state.get("attributes", {}).get("temperature")
            if current_target is not None:
                new_target = current_target + (step if action == "up" else -step)
                new_target = max(min_val, min(max_val, new_target))
                log.info("Setting %s temperature: %s → %s", entity_id, current_target, new_target)
                ha.set_climate_temperature(entity_id, new_target)

    # htmx: return updated dashboard content
    if request.headers.get("HX-Request") == "true":
        state = _get_dashboard_state()
        return render_template("partials/dashboard_content.html", **state)

    # Fallback: POST-Redirect-GET
    return redirect(url_for("index"))


if __name__ == "__main__":
    port = config.get("server_port", 5000)
    app.run(host="0.0.0.0", port=port)
