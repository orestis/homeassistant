# Wall Display Dashboard — Plan

## Goal

Build a **zero-JavaScript**, server-side rendered HTML dashboard for the Shelly Wall Display (4" touchscreen, MTK6580 Android SoC, 1 GB RAM). The page must be fast, touch-friendly, and usable by aging parents and young kids.

## Device Constraints

| Spec              | Value                                    |
| ----------------- | ---------------------------------------- |
| CPU               | MTK6580 (quad-core Cortex-A7, ~2015 era) |
| RAM               | 1 GB                                     |
| Flash             | 8 GB                                     |
| Display           | 4" capacitive touch (likely 480×480 px)  |
| Connectivity      | Wi-Fi 802.11 b/g/n                       |
| Browser           | Android WebView (old, slow JS engine)    |
| Scripting support | None (not a Gen2+ device)                |

**Key takeaway:** The browser is a low-end Android WebView. Heavy JS frameworks (React, Vue, etc.) will be unusable. Our approach is **progressive enhancement**:

1. **Server-side rendering** — the full UI is visible immediately as plain HTML+CSS, no JS required to see or use the dashboard
2. **htmx for snappy interactions** — a tiny (~14KB) library that replaces full page reloads with partial HTML swaps, giving instant visual feedback on tap
3. **Graceful degradation** — if JS fails to load, plain HTML forms still work (POST-Redirect-GET fallback)

## Architecture

```
┌──────────────────┐         HTTP GET/POST          ┌──────────────────────┐
│  Shelly Wall     │  ──────────────────────────►   │  Python Web Server   │
│  Display         │  ◄──────────────────────────   │  (Flask)             │
│  (Android browser)│      Pre-rendered HTML        │                      │
└──────────────────┘                                │  Calls HA REST API   │
                                                    │  for state & actions │
                                                    └──────────┬───────────┘
                                                               │
                                                    HA REST API (port 8123)
                                                               │
                                                    ┌──────────▼───────────┐
                                                    │  Home Assistant      │
                                                    │  (lights, climate,   │
                                                    │   scenes, covers)    │
                                                    └──────────────────────┘
```

### How It Works

1. Wall Display browser loads `http://<server>:5000/`
2. Server queries HA REST API for current states (lights, climate, covers)
3. Server renders a complete HTML page with all state baked in — **immediately visible, no JS needed to render**
4. htmx (~14KB) loads in the background and progressively enhances the buttons

**With htmx loaded (normal case):**

- Tap a button → htmx sends an AJAX POST → server returns an HTML fragment → htmx swaps just the changed part of the page
- Instant visual feedback: CSS class toggled on tap (e.g., button dims/highlights immediately)
- No full page reload — feels responsive even on slow hardware

**Without JS (fallback):**

- Every button is wrapped in a `<form method="POST">` that still works
- Server calls HA API → redirects back to GET (PRG pattern) → full page reload
- Slower, but fully functional

### State Freshness

- **`<meta http-equiv="refresh" content="30">`** — reloads the page every 30s as a baseline (pure HTML, works without JS)
- **htmx polling (enhancement)**: `hx-trigger="every 15s"` on the dashboard container swaps in fresh state without a visible reload

### Why htmx?

| Feature            | htmx                   | Vanilla JS                   | React/Vue/Svelte             |
| ------------------ | ---------------------- | ---------------------------- | ---------------------------- |
| Size               | ~14KB gzipped          | 0KB                          | 30-100KB+                    |
| Custom JS to write | None (HTML attributes) | Everything                   | Everything                   |
| Server rendering   | Yes (returns HTML)     | Returns JSON, client renders | Returns JSON, client renders |
| Works without JS   | Yes (form fallback)    | No                           | No                           |
| Learning curve     | Minimal                | Medium                       | High                         |

htmx is the sweet spot: server stays in control (Python/Jinja2 does all rendering), the client just swaps HTML fragments. No build step, no bundler, no node_modules.

## Serving Options (Comparison)

| Option                       | Pros                                          | Cons                                                |
| ---------------------------- | --------------------------------------------- | --------------------------------------------------- |
| **HA Add-on (Docker)**       | Managed by HA, auto-start, easy config        | Requires building a Docker add-on, more setup       |
| **Standalone Python server** | Simple to develop/test, runs anywhere         | Manual service management, separate from HA         |
| **HA www folder**            | Zero setup                                    | Cannot do server-side rendering — static files only |
| **AppDaemon app**            | Already an HA add-on, Python, has HTTP server | Awkward fit for a web app, limited HTTP routing     |

### Recommendation: Start standalone, package as HA add-on later

1. **Phase 1**: Build a standalone Flask app (easy to develop and test from any machine)
2. **Phase 2**: Package it as an HA add-on (Dockerfile + add-on config) for production use

## UI Design Principles

- **Large touch targets**: Minimum 60×60 px buttons (ideally larger) — must work for small kids and elderly
- **High contrast**: Dark background, bright buttons — readable in hallway lighting
- **Minimal text**: Icons + short labels, no clutter
- **Flat hierarchy**: Everything reachable from the home screen, maximum 1 tap to any action
- **Visual feedback**: Active/on states shown with color (e.g., yellow = light on, blue = heating active)
- **No scrolling if possible**: Everything fits on one screen (480×480 viewport)

## Screens / Layout

### Home Screen (single page, tabbed sections)

Rather than multiple pages (which require navigation and slow page loads), everything fits on **one page** with a compact layout:

```
┌─────────────────────────────────┐
│  🌡️ 21.5°C    ☀️ Living Room    │  ← Status bar (current temp, room name)
├─────────────────────────────────┤
│                                 │
│  ┌─────────┐  ┌─────────┐      │
│  │  Wake   │  │  Relax  │      │  ← Scene buttons (top priority)
│  │   Up    │  │         │      │
│  └─────────┘  └─────────┘      │
│  ┌─────────┐  ┌─────────┐      │
│  │  Sleep  │  │ All Off │      │
│  │         │  │         │      │
│  └─────────┘  └─────────┘      │
│                                 │
├─────────────────────────────────┤
│  Heating: 21.5°C → 22°C        │  ← Climate section
│  ┌───┐  ┌───────────┐  ┌───┐   │
│  │ - │  │  22.0 °C  │  │ + │   │
│  └───┘  └───────────┘  └───┘   │
├─────────────────────────────────┤
│  ┌──────┐ ┌──────┐ ┌──────┐    │  ← Quick actions row
│  │Lights│ │Roller│ │ Tents│    │
│  │On/Off│ │ ▲ ■ ▼│ │ ▲ ■ ▼│    │
│  └──────┘ └──────┘ └──────┘    │
└─────────────────────────────────┘
```

This is a rough sketch — actual layout will be a CSS grid optimized for 480×480.

## Tech Stack

| Component          | Choice                                       | Rationale                                            |
| ------------------ | -------------------------------------------- | ---------------------------------------------------- |
| Language           | Python 3                                     | Already used in this project, runs on HA host        |
| Web framework      | Flask                                        | Lightweight, minimal dependencies, Jinja2 templating |
| Templating         | Jinja2 (built into Flask)                    | Server-side rendering, full UI without JS            |
| Client enhancement | htmx (~14KB)                                 | Partial page swaps via HTML attributes, no custom JS |
| Styling            | Single `<style>` block + inline critical CSS | No external requests, instant first paint            |
| HA communication   | REST API via `requests`                      | Simple, well documented, long-lived access token     |
| Process manager    | systemd service / Docker                     | For production. During dev, just `python3 app.py`    |

### htmx Integration Pattern

Every action button follows this dual pattern:

```html
<!-- Works with AND without JS -->
<form method="POST" action="/action/scene/activate">
  <input type="hidden" name="entity_id" value="scene.wake_up" />
  <button
    type="submit"
    class="tile scene"
    hx-post="/action/scene/activate"
    hx-vals='{"entity_id": "scene.wake_up"}'
    hx-target="#dashboard"
    hx-swap="outerHTML"
    hx-indicator="this"
  >
    ☀️ Wake Up
  </button>
</form>
```

- **Without htmx**: normal form POST → server redirect → full page reload
- **With htmx**: AJAX POST → server returns updated `#dashboard` fragment → htmx swaps it in-place
- **`hx-indicator`**: adds `.htmx-request` class during the request → CSS shows a subtle loading state instantly on tap

## HA REST API Endpoints We'll Use

| Purpose          | Endpoint                                | Method                                      |
| ---------------- | --------------------------------------- | ------------------------------------------- |
| Get entity state | `GET /api/states/<entity_id>`           | Read current state + attributes             |
| Get all states   | `GET /api/states`                       | Bulk read for dashboard                     |
| Call a service   | `POST /api/services/<domain>/<service>` | Toggle lights, set climate, activate scenes |

Authentication: `Authorization: Bearer <LONG_LIVED_ACCESS_TOKEN>` header.

## Configuration

The server needs a config file (YAML or JSON) that maps Wall Display pages to HA entities:

```yaml
ha_url: "http://homeassistant.local:8123"
ha_token: "<long-lived-access-token>" # or read from env var
server_port: 5000

# What to show on the dashboard
climate:
  entity_id: "climate.living_room"
  name: "Heating"
  step: 0.5 # temperature increment per button press

scenes:
  - name: "Wake Up"
    icon: "☀️"
    entity_id: "scene.wake_up"
    color: "#FFA500"
  - name: "Relax"
    icon: "🌙"
    entity_id: "scene.relax"
    color: "#6A5ACD"
  - name: "Sleep"
    icon: "😴"
    entity_id: "scene.sleep"
    color: "#191970"
  - name: "All Off"
    icon: "⚫"
    entity_id: "scene.all_off"
    color: "#333333"

lights:
  - name: "Living Room"
    entity_id: "light.living_room"
  - name: "Kitchen"
    entity_id: "light.kitchen"

covers:
  - name: "Roller"
    entity_id: "cover.roller_livingroom"
  - name: "Tents"
    entity_id: "cover.tents_living_room"
```

This makes the dashboard fully configurable without touching code. Multiple Wall Displays could each have their own config file.

## Implementation Phases

### Phase 1: Core Server + Scene Buttons

- [ ] Set up Flask app skeleton with config loading
- [ ] Implement HA REST API client (get states, call services)
- [ ] Build home page template with scene buttons (dual: form + htmx)
- [ ] POST-Redirect-GET flow (fallback) + htmx partial swap (enhanced)
- [ ] Bundle htmx.min.js (served locally, not from CDN — avoids external dependency)
- [ ] Auto-refresh: `<meta refresh>` baseline + htmx polling enhancement
- [ ] Basic CSS for 480×480 touch-friendly layout with tap feedback states
- [ ] `.htmx-request` CSS for instant visual feedback on button tap

### Phase 2: Climate Control

- [ ] Read climate entity state (current temp, target temp, mode)
- [ ] +/- buttons for temperature adjustment
- [ ] Visual indicator for heating active/idle

### Phase 3: Lights & Covers

- [ ] Light toggle buttons with on/off state display
- [ ] Cover open/stop/close buttons with position display
- [ ] State-dependent button colors

### Phase 4: Polish & Deploy

- [ ] Test on actual Wall Display browser — verify no JS needed, check rendering
- [ ] Optimize: inline all CSS, minimize HTML size, remove unnecessary whitespace
- [ ] Add a sample config with the actual entity IDs from this home
- [ ] Write deployment instructions (systemd service or Docker)
- [ ] (Optional) Package as HA add-on

## Open Questions

1. **Display resolution**: Need to verify the exact pixel resolution by checking the Wall Display's browser user-agent or viewport size. Design assumes 480×480.
2. **Entity IDs**: Need to discover the actual HA entity IDs for climate, scenes, and covers. This can be done from HA Developer Tools or the REST API.
3. **Auth token**: A long-lived access token needs to be created in HA (Profile → Long-Lived Access Tokens).
4. **Wall Display browser URL config**: How to set the Wall Display to open a custom URL on boot/wake? This may be configurable via the Shelly app or web UI.
5. **Network**: The Flask server needs to be reachable from the Wall Display's Wi-Fi. Running on the HA host (same network) is simplest.

## File Structure

```
wall-display/
├── PLAN.md              ← this file
├── config.yaml          ← dashboard configuration (entity IDs, HA connection)
├── config.example.yaml  ← example config (committed to git, no secrets)
├── app.py               ← Flask application entry point
├── ha_client.py         ← Home Assistant REST API client
├── templates/
│   ├── dashboard.html   ← Jinja2 full page template
│   └── partials/
│       ├── scenes.html      ← scene buttons fragment (htmx swappable)
│       ├── climate.html     ← climate controls fragment
│       ├── lights.html      ← light toggles fragment
│       └── covers.html      ← cover controls fragment
├── static/
│   └── htmx.min.js      ← htmx library (~14KB, served locally)
├── requirements.txt     ← Python dependencies (flask, requests, pyyaml)
└── README.md            ← Setup & deployment instructions
```

The `partials/` templates are the same fragments returned by htmx POST endpoints — this means the full page and the htmx updates share the same rendering code (DRY).
