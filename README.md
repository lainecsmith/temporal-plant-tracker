# 🌿 Plant Tracker

A durable plant health monitoring system built on **Temporal** (Entity Workflow pattern), **FastAPI**, and **React**. Each plant is a long-running Temporal workflow that automatically polls Zigbee sensors in Home Assistant, tracks watering history, and alerts you when your plants need attention.

## Features

- **Entity workflow per plant** — each plant is a persistent Temporal workflow with ID `plant-{slug}-{short-id}` (e.g. `plant-living-room-monstera-a1b2c3`)
- **Automatic care range lookup** — searches [OpenPlantbook.io](https://open.plantbook.io) first; falls back to GPT-4o structured output if not found
- **Editable care ranges** — review and modify soil moisture, temperature (°F), humidity, light, and watering interval from the UI; validated synchronously via Temporal Updates
- **Device-level sensor association** — discovers Zigbee plant sensor devices (MiFlora, HHCC, etc.) from Home Assistant and maps all their entities (moisture, temperature, humidity, light) in one step
- **Watering tracking** — auto-detected from a 40-point soil moisture spike for sensor plants; manually logged from the UI for sensorless plants; warns when overdue based on `watering_interval_days`
- **Room grouping** — assign plants to rooms; the dashboard groups and sorts them accordingly
- **Smart alerts** — sends an HA notification and turns an indicator light red when readings go out of range; turns it green when things return to normal
- **Plant lifecycle** — mark plants as `dead` or `given_away` to cleanly end the workflow and remove them from the dashboard
- **Durable by design** — Temporal ensures the polling loop survives crashes, restarts, and worker upgrades via continue-as-new
- **Error visibility** — a "desired-vs-applied" error pattern surfaces sensor, association, care-range, and alert failures directly on each plant card

---

## Architecture

```
React UI (Vite dev / nginx:3000)
    │  HTTP  /api/* → proxy → :8000
    ▼
FastAPI backend (:8000)       ← signals/updates/queries Temporal workflows
    │
    ▼
Temporal Cluster (:7233)
    │
    ▼
Temporal Worker               ← executes workflows & activities
    ├── activities/openplantbook.py   — OpenPlantbook API lookup
    ├── activities/llm.py             — GPT-4o fallback (async)
    └── activities/home_assistant.py  — HA sensor reads & alerts (sync)
```

### Plant Workflow Lifecycle

1. **Start** → look up care ranges (OpenPlantbook → GPT-4o fallback); temperatures from both sources are converted from °C to °F automatically
2. **Hourly polling loop starts immediately** — all plants enter the loop, sensor or not
   - **With sensor**: poll HA → compare readings to care ranges → trigger or clear HA alert; auto-detect watering from a 40-point moisture spike
   - **Without sensor**: check if `watering_interval_days` has elapsed since `last_watered_at`; set `watering_overdue` if so
3. **Signals/updates** modify state mid-loop without interrupting the timer
4. **Continue-as-new** when Temporal recommends it — full `PlantWorkflowContinuation` is forwarded so no state is lost
5. **Terminal status** (`dead` / `given_away`) — workflow exits cleanly on the next loop iteration

---

## Prerequisites

| Tool | Install |
|---|---|
| Python 3.11+ | [python.org](https://python.org) |
| uv | `brew install uv` or [docs.astral.sh/uv](https://docs.astral.sh/uv) |
| Node 18+ | [nodejs.org](https://nodejs.org) |
| Temporal CLI | `brew install temporal` |
| Home Assistant | Running on your local network with a Long-Lived Access Token |

---

## Setup

### 1. Clone / navigate to the project

```bash
cd temporal-plant-tracker
```

### 2. Configure environment variables

```bash
cp backend/.env.example backend/.env
```

Edit `backend/.env` and fill in:

```env
# Temporal (leave as-is for local dev)
TEMPORAL_HOST=localhost:7233
TEMPORAL_NAMESPACE=default
TEMPORAL_TASK_QUEUE=plant-tracker

# OpenAI — required for AI fallback when plant not in OpenPlantbook
OPENAI_API_KEY=sk-...

# OpenPlantbook — register free at https://open.plantbook.io/registrations/
OPENPLANTBOOK_CLIENT_ID=your-client-id
OPENPLANTBOOK_CLIENT_SECRET=your-client-secret

# Home Assistant
HA_URL=http://homeassistant.local:8123
HA_TOKEN=your-long-lived-access-token

# Optional: entity ID of an RGB light to turn red/green on alerts
HA_INDICATOR_LIGHT_ENTITY=light.plant_indicator

# Notification service (notify.mobile_app_yourphone, notify.persistent_notification, etc.)
HA_NOTIFICATION_SERVICE=notify.persistent_notification
```

### 3. Install backend dependencies

```bash
cd backend
uv sync
```

### 4. Install frontend dependencies

```bash
cd frontend
npm install
```

---

## Running

### Option A — Local dev (four terminal tabs)

```bash
# Tab 1 — Temporal dev server (includes UI at http://localhost:8233)
temporal server start-dev

# Tab 2 — Temporal worker (executes workflows & activities)
cd backend && uv run python worker.py

# Tab 3 — FastAPI backend
cd backend && uv run uvicorn api.main:app --reload --port 8000

# Tab 4 — React frontend (proxies /api → :8000 automatically)
cd frontend && npm run dev
```

Then open **http://localhost:5173** in your browser.

### Option B — Docker Compose

The repo includes a `docker-compose.yml` that runs three services: the FastAPI API server, the Temporal worker (same image, different entrypoint), and the React frontend served by nginx. You still need an external Temporal server — point `TEMPORAL_HOST` in `backend/.env` at it.

```bash
docker compose up --build
```

| Service | URL |
|---|---|
| React UI | http://localhost:3000 |
| FastAPI | http://localhost:8000 |
| Temporal UI | http://localhost:8233 (external server) |

> **Note:** The nginx config strips the `/api` prefix and proxies to `backend-api:8000`, matching the Vite dev server behaviour, so the same frontend build works in both environments.

---

## Using the App

### Adding a Plant

1. Click **"Add Plant"** — a 3-step modal opens
2. **Step 1 — Name & Species**: enter the plant's display name, its species, and optionally a room
   - The workflow starts immediately on submit
3. **Step 2 — Care Ranges**: the UI polls the workflow (up to 60 s) until care ranges are ready
   - 📖 **Found in OpenPlantbook** — ranges pre-filled from the database
   - 🤖 **Not found** — GPT-4o suggests ranges; per-metric reasoning is shown inline
   - ⚠️ **Both failed** — enter ranges manually
   - Edit any values, then click **"These look good →"** (only sends an update signal if you changed something)
4. **Step 3 — Associate a Sensor**: pick a Zigbee sensor device from your Home Assistant setup
   - Each device shows which metrics it reports (💧 Moisture, 🌡️ Temperature, 💦 Humidity, ☀️ Light, 🔋 Battery)
   - Skip for now and add later from the plant card

### Dashboard

Plants are grouped by room (named rooms sorted alphabetically; unassigned plants last). Each plant card shows:

- Status dot: 🟢 **Healthy** / ⚠️ **Needs Attention** / ⏳ **Not Tracked**
- Sensor name and last-checked timestamp
- Watering row: last watered, configured interval, and a **Log Watering** button for sensorless plants
- Live metric bars — current reading plotted against the green acceptable range
- Inline error notices for any failed operation (sensor read, HA alert, etc.)
- **Refresh** button to force an immediate sensor poll
- **▼ expand** to edit care ranges inline
- **⋯ menu** to change room, mark as Dead, or mark as Given Away

The dashboard auto-refreshes every 2 minutes and additionally polls every 3 s for any plant whose care ranges are still loading.

### Alerts

When a sensor reading goes out of range:
- A notification is sent to Home Assistant via `HA_NOTIFICATION_SERVICE`
- The indicator light turns **red** (if `HA_INDICATOR_LIGHT_ENTITY` is set)
- When all readings return to normal, the light turns **green** automatically

### Watering Tracking

**Sensor plants** — watering is auto-detected when soil moisture jumps ≥ 40 percentage points from a previous reading that was already below `soil_moisture_min`. You can also use **Log Watering** to override or backfill.

**Sensorless plants** — use the **Log Watering** button on the card to record a date (defaults to today). If `watering_interval_days` is configured (from OpenPlantbook or AI), the card shows a `⚠️ Watering overdue` badge when the interval has elapsed.

### Plant Lifecycle

Use the **⋯ menu** on any card to mark a plant as **Dead** or **Given Away**. This sends a Temporal Update to the workflow, which then exits cleanly. The plant is removed from the dashboard immediately.

---

## Project Structure

```
temporal-plant-tracker/
├── backend/
│   ├── models/
│   │   ├── config.py              # pydantic-settings config (reads backend/.env)
│   │   └── plant.py               # shared Pydantic models (CareRanges, PlantState, etc.)
│   ├── workflows/
│   │   └── plant_workflow.py      # PlantWorkflow entity pattern
│   ├── activities/
│   │   ├── openplantbook.py       # OpenPlantbook API + watering-interval text parser
│   │   ├── llm.py                 # GPT-4o structured output (async)
│   │   └── home_assistant.py      # HA sensor reads & alerts (sync)
│   ├── api/
│   │   └── main.py                # FastAPI routes (HTTP ↔ Temporal bridge)
│   ├── worker.py                  # Temporal worker (ThreadPoolExecutor for sync activities)
│   ├── Dockerfile                 # Single image used by both API and worker services
│   ├── pyproject.toml
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── api/client.ts              # Typed fetch wrappers for every API endpoint
│   │   ├── types/index.ts             # TypeScript types matching backend Pydantic models
│   │   ├── components/
│   │   │   ├── AddPlantModal.tsx      # 3-step add-plant flow with async range polling
│   │   │   ├── PlantCard.tsx          # Plant card with metrics, watering, and status menu
│   │   │   ├── CareRangesEditor.tsx   # Editable care-range form with AI reasoning tooltips
│   │   │   └── AssociateSensorModal.tsx  # Change-sensor modal on existing cards
│   │   ├── App.tsx                    # Root — room grouping, auto-refresh, global state
│   │   └── main.tsx
│   ├── nginx.conf                 # Strips /api prefix, proxies to backend-api
│   └── Dockerfile                 # Multi-stage: Node build → nginx serve
├── docker-compose.yml             # backend-api + backend-worker + frontend
└── README.md
```

---

## Temporal Workflow Details

### Workflow ID

`plant-{slug}-{short-random-8-chars}` — e.g. `plant-living-room-monstera-a1b2c3d4`

The slug is derived from the plant's display name (lowercased, non-alphanumeric runs → `-`). The short random suffix prevents collisions for plants with identical names.

### Signals

| Signal | Description |
|---|---|
| `associate_sensor(entity_id)` | Legacy: associate a single HA entity ID; triggers immediate poll |
| `associate_device(device_id, device_name, sensor_entities)` | Associate an HA device with its full `device_class → entity_id` map; triggers immediate poll |
| `refresh_readings()` | Skip the current sleep and poll immediately |
| `record_watering(watered_at_iso?)` | Record a watering event; clears `watering_overdue`; triggers immediate loop iteration |
| `set_room(room)` | Assign to a room or clear the assignment (`None`) |

### Updates (synchronous — validated before state mutation)

| Update | Validator | Description |
|---|---|---|
| `update_care_ranges(CareRanges)` | Ensures min ≤ max for all ranges | Replaces care ranges; source becomes `"manual"` |
| `set_plant_status(status)` | Rejects unknown status strings | Changes lifecycle status; terminal statuses (`dead`, `given_away`) cause the workflow to exit |

### Queries

| Query | Returns |
|---|---|
| `get_state()` | Full `PlantState` including readings, errors, watering timestamp, and room |

### Polling & Sleep

The workflow uses `workflow.wait_condition(lambda: self._force_poll or self._stop_requested, timeout=1h)` between polls. Any signal that sets `_force_poll = True` (associate_device, associate_sensor, refresh_readings, record_watering) immediately interrupts the sleep.

### Continue-as-New

Triggered by `workflow.info().is_continue_as_new_suggested()` at the top of every loop iteration. All state is passed forward in `PlantWorkflowContinuation`, including:
- Care ranges and their source/reasoning
- Sensor device ID, name, and entity map
- Last readings, last checked / watered timestamps
- All four last-error fields
- Room assignment

### Error Fields (Desired-vs-Applied)

Each error field is `None` when the last operation succeeded and is set to a human-readable string when it failed. Cleared automatically on the next success. Surfaced as inline warning banners on the plant card.

| Field | Set when |
|---|---|
| `last_association_error` | `associate_device` receives an empty entity map |
| `last_sensor_read_error` | `get_sensor_readings` fails (bad entity ID, HA unreachable) |
| `last_care_ranges_fetch_error` | Both OpenPlantbook and GPT-4o fail; user must set ranges manually |
| `last_alert_error` | `trigger_ha_alert` or `clear_ha_alert_light` fails |

---

## API Reference

### Plants

| Method | Path | Description |
|---|---|---|
| `POST` | `/plants` | Create plant + start workflow; returns initial state |
| `GET` | `/plants` | List all running plant workflows (uses Temporal visibility API) |
| `GET` | `/plants/{id}` | Get plant state (workflow query) |
| `PUT` | `/plants/{id}/care-ranges` | Update care ranges (Temporal Update — validated synchronously) |
| `POST` | `/plants/{id}/device` | Associate HA sensor device (signal) |
| `POST` | `/plants/{id}/sensor` | Associate single HA entity ID — legacy (signal) |
| `POST` | `/plants/{id}/refresh` | Force immediate sensor poll (signal) |
| `POST` | `/plants/{id}/water` | Log a watering event; `watered_at` defaults to now (signal) |
| `POST` | `/plants/{id}/status` | Change lifecycle status (Temporal Update — validated synchronously) |
| `PUT` | `/plants/{id}/room` | Assign to a room or clear assignment (signal) |

### Sensors / Devices

| Method | Path | Description |
|---|---|---|
| `GET` | `/devices` | List HA plant sensor *devices* grouped by device prefix (preferred) |
| `GET` | `/sensors` | List HA plant sensor entities — legacy entity-level endpoint |

Interactive docs: **http://localhost:8000/docs**

---

## Viewing Workflows in Temporal UI

The Temporal dev server includes a built-in UI at:

```
http://localhost:8233
```

You can see all running plant workflows, their event history (signals, updates, activity results), and query current state. Each workflow ID maps directly to a plant: `plant-living-room-monstera-a1b2c3d4`.
