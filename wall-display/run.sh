#!/usr/bin/with-contenv bashio
# ==============================================================================
# Start the Wall Display Dashboard
# ==============================================================================

bashio::log.info "Starting Wall Display Dashboard..."
bashio::log.info "SUPERVISOR_TOKEN present: $([ -n "$SUPERVISOR_TOKEN" ] && echo yes || echo no)"
cd /app
exec python3 -u app.py
