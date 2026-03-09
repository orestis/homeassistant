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
