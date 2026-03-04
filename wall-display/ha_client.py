"""Home Assistant REST API client."""

from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error

log = logging.getLogger(__name__)


class HAClient:
    """Thin wrapper around the HA REST API."""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _request(self, method: str, path: str, data: dict | None = None) -> dict | list | None:
        """Make an authenticated request to HA."""
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            log.error("HA API %s %s → HTTP %s: %s", method, path, e.code,
                      e.read().decode("utf-8", errors="replace")[:200])
            return None
        except Exception as e:
            log.error("HA API %s %s → %s", method, path, e)
            return None

    def get_state(self, entity_id: str) -> dict | None:
        """Get the state of a single entity."""
        return self._request("GET", f"/api/states/{entity_id}")

    def get_states(self, entity_ids: list[str]) -> dict[str, dict]:
        """Get states for multiple entities. Returns {entity_id: state_dict}."""
        results = {}
        for eid in entity_ids:
            state = self.get_state(eid)
            if state:
                results[eid] = state
        return results

    def call_service(self, domain: str, service: str, data: dict | None = None) -> bool:
        """Call a HA service. Returns True on success."""
        result = self._request("POST", f"/api/services/{domain}/{service}", data or {})
        return result is not None

    def activate_scene(self, entity_id: str) -> bool:
        """Activate a scene."""
        return self.call_service("scene", "turn_on", {"entity_id": entity_id})

    def set_climate_temperature(self, entity_id: str, temperature: float) -> bool:
        """Set climate target temperature."""
        return self.call_service("climate", "set_temperature", {
            "entity_id": entity_id,
            "temperature": temperature,
        })

    def set_input_number(self, entity_id: str, value: float) -> bool:
        """Set an input_number entity value."""
        return self.call_service("input_number", "set_value", {
            "entity_id": entity_id,
            "value": value,
        })

    def get_weather_forecast(self, entity_id: str, forecast_type: str = "hourly") -> list[dict]:
        """Get weather forecast entries via the weather.get_forecasts service."""
        result = self._request(
            "POST",
            f"/api/services/weather/get_forecasts?return_response",
            {"entity_id": entity_id, "type": forecast_type},
        )
        if not isinstance(result, dict):
            return []
        # Handle response: {"weather.x": {"forecast": [...]}}
        # or {"service_response": {"weather.x": {"forecast": [...]}}}
        data = result.get("service_response", result)
        if isinstance(data, dict):
            entity_data = data.get(entity_id, data)
            if isinstance(entity_data, dict):
                return entity_data.get("forecast", [])
        return []
