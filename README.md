# 🌿 Plant Tracker

A durable plant health monitoring system built on **Temporal** (Entity Workflow pattern), **FastAPI**, and **React**. Each plant is represented as a long-running Temporal workflow that automatically polls Zigbee sensors in Home Assistant and alerts you when your plants need attention.

## Features

- **Entity workflow per plant** — each plant is a persistent Temporal workflow with ID `plant-{uuid}`
- **Automatic care range lookup** — searches [OpenPlantbook.io](https://open.plantbook.io) first; falls back to GPT-4o structured output if not found
- **Editable care ranges** — review and modify soil moisture, temperature, humidity, and light ranges from the UI
- **Home Assistant integration** — discovers Zigbee plant sensors (MiFlora, HHCC, etc.) and reads them hourly
- **Smart alerts** — sends an HA notification and turns an indicator light red when readings go out of range; turns it green when things are back to normal
- **Durable by design** — Temporal ensures the polling loop survives crashes, restarts, and worker upgrades

---

## Architecture

```
React UI (Vite)
    │  HTTP (proxied to :8000)
    ▼
FastAPI backend          ← signals/queries Temporal workflows
    │
    ▼
Temporal Cluster (dev server on :7233)
    │
    ▼
Temporal Worker          ← executes activities
    ├── activities/openplantbook.py   — OpenPlantbook API
    ├── activities/llm.py             — GPT-4o fallback
    └── activities/home_assistant.py  — HA sensor reads & alerts
```

Each plant workflow lifecycle:
1. **Start** → look up care ranges (OpenPlantbook → GPT-4o fallback)
2. **Wait** for a sensor to be associated (via UI signal)
3. **Hourly loop**: poll HA sensor → compare to ranges → alert if needed
4. **Continue-as-new** when history grows large (Temporal best practice)

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
HA_INDICATOR_LIGHT_ENTITY=light.your_indicator_light

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

Open **four terminal tabs** in the `plant-tracker` directory:

```bash
# Tab 1 — Temporal dev server
temporal server start-dev

# Tab 2 — Temporal worker (executes workflows & activities)
cd backend && uv run python worker.py

# Tab 3 — FastAPI backend (API + Temporal client)
cd backend && uv run uvicorn api.main:app --reload --port 8000

# Tab 4 — React frontend
cd frontend && npm run dev
```

Then open **http://localhost:5173** in your browser.

---

## Using the App

### Adding a Plant

1. Click **"Add Plant"**
2. Enter the plant's name (e.g. "Living Room Monstera") and species (e.g. "Monstera deliciosa")
3. The workflow starts immediately and looks up care ranges:
   - 📖 Found in OpenPlantbook — ranges pre-filled from database
   - 🤖 Not found — GPT-4o suggests ranges automatically
4. Review and optionally edit the ranges, then click **"These look good →"**
5. Pick a Zigbee sensor from your Home Assistant setup (or skip for now)

### Dashboard

Each plant card shows:
- 🟢 **Healthy** / ⚠️ **Needs Attention** / ⏳ **No Sensor** status
- Live metric bars showing current reading vs. acceptable range (green zone)
- Last checked timestamp
- **Refresh** button to poll the sensor immediately
- Expand (▼) to edit care ranges inline

### Alerts

When a sensor reading goes out of range:
- A notification is sent to Home Assistant (via `HA_NOTIFICATION_SERVICE`)
- The indicator light turns **red** (if `HA_INDICATOR_LIGHT_ENTITY` is set)
- When readings return to normal, the light turns **green** automatically

---

## Project Structure

```
plant-tracker/
├── backend/
│   ├── models/
│   │   ├── config.py           # pydantic-settings config
│   │   └── plant.py            # shared Pydantic models
│   ├── workflows/
│   │   └── plant_workflow.py   # PlantWorkflow (entity pattern)
│   ├── activities/
│   │   ├── openplantbook.py    # OpenPlantbook API
│   │   ├── llm.py              # GPT-4o structured output
│   │   └── home_assistant.py   # HA sensors + alerts
│   ├── api/
│   │   └── main.py             # FastAPI routes
│   ├── worker.py               # Temporal worker
│   ├── pyproject.toml
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── api/client.ts           # API client
│   │   ├── types/index.ts          # TypeScript types
│   │   ├── components/
│   │   │   ├── AddPlantModal.tsx   # Multi-step add plant flow
│   │   │   ├── PlantCard.tsx       # Plant status card
│   │   │   └── CareRangesEditor.tsx
│   │   ├── App.tsx
│   │   └── main.tsx
│   └── package.json
└── README.md
```

---

## Temporal Workflow Details

### Workflow ID
`plant-{uuid}` — stable across continue-as-new

### Signals
| Signal | Description |
|---|---|
| `update_care_ranges(CareRanges)` | User edits care ranges from UI |
| `associate_sensor(entity_id)` | Associate an HA Zigbee sensor |
| `refresh_readings()` | Force immediate sensor poll |

### Queries
| Query | Description |
|---|---|
| `get_state() → PlantState` | Full current state of the plant |

### Polling
The workflow uses `workflow.sleep(1 hour)` between polls, interruptible by the `refresh_readings` signal via `workflow.wait_condition(..., timeout=1h)`.

### Continue-as-New
Checked on every loop iteration via `workflow.info().is_continue_as_new_suggested()`. Full `PlantState` is passed forward so no data is lost.

---

## API Reference

| Method | Path | Description |
|---|---|---|
| `POST` | `/plants` | Create plant + start workflow |
| `GET` | `/plants` | List all running plant workflows |
| `GET` | `/plants/{id}` | Get plant state |
| `PUT` | `/plants/{id}/care-ranges` | Update care ranges (signal) |
| `POST` | `/plants/{id}/sensor` | Associate HA sensor (signal) |
| `POST` | `/plants/{id}/refresh` | Force sensor poll (signal) |
| `GET` | `/sensors` | List available HA plant sensors |

Interactive docs: **http://localhost:8000/docs**

---

## Viewing Workflows in Temporal UI

The Temporal dev server includes a built-in UI:

```
http://localhost:8233
```

You can see all running plant workflows, their event history, signals received, and current state.
