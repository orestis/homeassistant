"""Home Assistant REST + WebSocket API client."""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
import urllib.error

import websockets

log = logging.getLogger(__name__)


class HAClient:
    """Thin wrapper around the HA REST and WebSocket APIs."""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://") + "/api/websocket"
        self._msg_id = 0

    # ------------------------------------------------------------------
    # REST helpers
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # WebSocket helpers
    # ------------------------------------------------------------------

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def ws_command(self, msg_type: str, **kwargs) -> dict | None:
        """Send a single WebSocket command and return the result.

        Connects, authenticates, sends the command, and disconnects.
        Suitable for one-off operations (helper creation, config reads, etc.).
        """
        return asyncio.get_event_loop().run_until_complete(
            self._ws_command_async(msg_type, **kwargs)
        ) if asyncio.get_event_loop().is_running() is False else asyncio.run(
            self._ws_command_async(msg_type, **kwargs)
        )

    def ws_command_sync(self, msg_type: str, **kwargs) -> dict | None:
        """Synchronous wrapper for ws_command — always safe to call."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            # We're inside an existing event loop (e.g. Jupyter) — use a thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, self._ws_command_async(msg_type, **kwargs)).result()
        return asyncio.run(self._ws_command_async(msg_type, **kwargs))

    async def _ws_command_async(self, msg_type: str, **kwargs) -> dict | None:
        """Send a single command over a fresh WebSocket connection."""
        try:
            async with websockets.connect(self._ws_url) as ws:
                # auth_required
                msg = json.loads(await ws.recv())
                if msg.get("type") != "auth_required":
                    log.error("WS unexpected greeting: %s", msg)
                    return None
                # authenticate
                await ws.send(json.dumps({"type": "auth", "access_token": self.token}))
                msg = json.loads(await ws.recv())
                if msg.get("type") != "auth_ok":
                    log.error("WS auth failed: %s", msg)
                    return None
                # send command
                cmd_id = self._next_id()
                payload = {"id": cmd_id, "type": msg_type, **kwargs}
                await ws.send(json.dumps(payload))
                msg = json.loads(await ws.recv())
                if not msg.get("success"):
                    log.error("WS command %s failed: %s", msg_type,
                              msg.get("error", {}).get("message", msg))
                    return None
                return msg.get("result")
        except Exception as e:
            log.error("WS %s → %s", msg_type, e)
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

    def get_scene_config(self, entity_id: str) -> dict | None:
        """Get the full config for a UI-created scene (entities + metadata).

        The entity_id should be e.g. 'scene.relax'. The internal scene ID
        is looked up automatically from the entity state attributes.
        """
        state = self.get_state(entity_id)
        if not state:
            return None
        scene_id = state.get("attributes", {}).get("id")
        if not scene_id:
            log.error("Scene %s has no internal id (may be YAML-defined)", entity_id)
            return None
        return self._request("GET", f"/api/config/scene/config/{scene_id}")

    def update_scene_config(self, entity_id: str, config: dict) -> bool:
        """Update a UI-created scene config.

        Pass the full config dict (as returned by get_scene_config, modified).
        The internal scene ID is looked up from the entity state.
        """
        state = self.get_state(entity_id)
        if not state:
            return False
        scene_id = state.get("attributes", {}).get("id")
        if not scene_id:
            log.error("Scene %s has no internal id (may be YAML-defined)", entity_id)
            return False
        result = self._request("POST", f"/api/config/scene/config/{scene_id}", config)
        return result is not None

    def list_scenes(self) -> list[dict]:
        """List all scenes with their entity_id, friendly_name, and internal id."""
        all_states = self.get_all_states()
        scenes = []
        for s in all_states:
            if s["entity_id"].startswith("scene."):
                scenes.append({
                    "entity_id": s["entity_id"],
                    "friendly_name": s["attributes"].get("friendly_name", ""),
                    "id": s["attributes"].get("id"),
                    "entity_ids": s["attributes"].get("entity_id", []),
                })
        return scenes

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

    def get_statistics(
        self,
        entity_ids: list[str],
        start: str,
        end: str | None = None,
        period: str = "hour",
        types: list[str] | None = None,
    ) -> dict[str, list[dict]]:
        """Fetch long-term statistics via the WS API.

        Uses ``recorder/statistics_during_period`` which stores hourly
        aggregates far beyond the short-term history retention window.

        Args:
            entity_ids: List of entity IDs to query.
            start: ISO-8601 start timestamp.
            end: ISO-8601 end timestamp (optional).
            period: One of "5minute", "hour", "day", "week", "month".
            types: Stat types to fetch, e.g. ["mean", "state", "sum", "change"].

        Returns:
            ``{entity_id: [{start, end, mean, min, max, state, ...}, ...]}``
        """
        kwargs: dict = {
            "statistic_ids": entity_ids,
            "period": period,
            "start_time": start,
            "types": types or ["mean", "state"],
        }
        if end:
            kwargs["end_time"] = end

        result = self.ws_command_sync("recorder/statistics_during_period", **kwargs)
        if not isinstance(result, dict):
            return {}
        return result

    def list_statistic_ids(self, statistic_type: str | None = None) -> list[dict]:
        """List available statistic IDs.

        Args:
            statistic_type: Filter by "mean" or "sum". If None, returns both.

        Returns:
            List of dicts with keys like statistic_id, unit_of_measurement, source.
        """
        if statistic_type:
            result = self.ws_command_sync(
                "recorder/list_statistic_ids", statistic_type=statistic_type,
            )
            return result or []

        all_stats = []
        for st in ("mean", "sum"):
            result = self.ws_command_sync(
                "recorder/list_statistic_ids", statistic_type=st,
            )
            if result:
                all_stats.extend(result)
        return all_stats

    def get_energy_consumption(
        self,
        entity_id: str,
        start: str,
        end: str,
        period: str = "hour",
    ) -> float | None:
        """Get total energy consumption for a cumulative sensor over a period.

        Uses the recorder's ``sum`` and ``change`` fields.  Returns
        ``sum(change)`` which is the most accurate method for sensors
        that periodically reset (daily/weekly/monthly counters).

        Args:
            entity_id: A cumulative energy sensor (e.g. daily/monthly consumption).
            start: ISO-8601 start timestamp.
            end: ISO-8601 end timestamp.
            period: Granularity — "hour" (default) or "day".

        Returns:
            Total consumption in the sensor's unit (typically kWh), or None.
        """
        stats = self.get_statistics(
            [entity_id], start, end, period=period, types=["sum", "change"],
        )
        rows = stats.get(entity_id, [])
        if not rows:
            return None

        changes = [r["change"] for r in rows if r.get("change") is not None]
        if changes:
            return sum(changes)

        # Fallback: sum field diff
        first_sum = rows[0].get("sum")
        last_sum = rows[-1].get("sum")
        if first_sum is not None and last_sum is not None:
            return last_sum - first_sum

        return None

    # ------------------------------------------------------------------
    # Entity / device discovery
    # ------------------------------------------------------------------

    def get_all_states(self) -> list[dict]:
        """Get states for all entities."""
        return self._request("GET", "/api/states") or []

    def get_device_entities(self, device_id: str) -> list[dict]:
        """List all entity registry entries for a given device ID."""
        entities = self.ws_command_sync("config/entity_registry/list")
        if not entities:
            return []
        return [e for e in entities if e.get("device_id") == device_id]

    def search_entities(
        self,
        keywords: list[str] | None = None,
        device_class: str | None = None,
    ) -> list[dict]:
        """Search entities by keyword (in entity_id or friendly_name) and/or device_class.

        Returns list of state dicts matching the criteria.
        """
        all_states = self.get_all_states()
        results = all_states

        if keywords:
            kw_lower = [k.lower() for k in keywords]
            results = [
                s for s in results
                if any(k in s["entity_id"].lower() for k in kw_lower)
                or any(k in s["attributes"].get("friendly_name", "").lower() for k in kw_lower)
            ]

        if device_class:
            results = [
                s for s in results
                if s["attributes"].get("device_class") == device_class
            ]

        return results

    def get_services(self) -> list[dict]:
        """Get all available services from HA.

        Returns a list of dicts, each with 'domain' and 'services' keys.
        Useful for discovering available notify targets, etc.
        """
        result = self._request("GET", "/api/services")
        return result if isinstance(result, list) else []

    def get_config_entries(self, domain: str) -> list[dict]:
        """List config entries (integrations) for a given domain.

        Useful for checking if an integration is set up, e.g.
        ``ha.get_config_entries("tplink")`` or ``ha.get_config_entries("tapo")``.
        """
        return self.ws_command_sync("config_entries/get", domain=domain) or []
