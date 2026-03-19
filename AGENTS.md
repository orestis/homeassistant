Never install python utils globally. Use the .venv at the root.

The project is structured as an installable Python package (`ha-tools`).
Core client libraries live in `src/ha_tools/` — install with `pip install -e .`.
Operational scripts live in `scripts/` (organized by domain: `shelly/`, `zte/`).
The `wall-display/` directory is a deployable Flask-based HA add-on.

For home assistant (HA) interactions, use `ha_tools.ha_client` (`from ha_tools.ha_client import HAClient`), and if what you need isn't covered, add it as a method there.

For Shelly device interactions, use `ha_tools.shelly_client` (`from ha_tools.shelly_client import rpc`).

Prefer to create scripts as files to run them, instead of ad-hoc scripts in the command line.
