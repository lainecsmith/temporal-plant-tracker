"""
FastAPI application — the HTTP bridge between the React UI and Temporal workflows.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from temporalio.client import Client, WorkflowUpdateFailedError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.service import RPCError, RPCStatusCode

from models.config import settings
from models.plant import (
    AssociateDeviceRequest,
    AssociateSensorRequest,
    CreatePlantRequest,
    HADevice,
    HADeviceEntity,
    HASensor,
    LogWateringRequest,
    PlantState,
    TERMINAL_STATUSES,
    UpdateCareRangesRequest,
    UpdatePlantStatusRequest,
    UpdateRoomRequest,
)
from workflows.plant_workflow import PlantWorkflow, PlantWorkflowInput

with __import__("temporalio").workflow.unsafe.imports_passed_through():
    pass  # ensure sandbox pass-through is loaded

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Temporal client (shared across requests)
# ---------------------------------------------------------------------------

_temporal_client: Optional[Client] = None


async def get_temporal_client() -> Client:
    global _temporal_client
    if _temporal_client is None:
        _temporal_client = await Client.connect(
            settings.temporal_host,
            namespace=settings.temporal_namespace,
            data_converter=pydantic_data_converter,
        )
    return _temporal_client


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Connect to Temporal on startup
    await get_temporal_client()
    yield
    # Nothing to close — SDK manages connection lifecycle


app = FastAPI(
    title="Plant Tracker API",
    description="Manage your plants via Temporal entity workflows",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helper: get a workflow handle, raise 404 if not found
# ---------------------------------------------------------------------------

async def _get_plant_handle(plant_id: str):
    client = await get_temporal_client()
    return client.get_workflow_handle(
        f"plant-{plant_id}",
    #    workflow=PlantWorkflow,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


# ---- Plants ---------------------------------------------------------------


def _slugify(text: str) -> str:
    """
    Convert a plant name into a workflow-ID-safe slug.

    Rules:
      - Lowercase
      - Any run of characters that are not alphanumeric or hyphens → single hyphen
      - Strip leading/trailing hyphens
    """
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "plant"


@app.post("/plants", response_model=PlantState, status_code=status.HTTP_201_CREATED)
async def create_plant(body: CreatePlantRequest):
    """
    Start a new PlantWorkflow. The workflow will immediately look up care
    ranges from OpenPlantbook (or AI) and wait for sensor association.
    """
    # Workflow ID format: plant-<slug>-<short-random>
    # e.g. "Living Room Monstera" → plant-living-room-monstera-a1b2c3d4
    slug = _slugify(body.name)
    short_id = str(uuid.uuid4()).replace("-", "")[:8]
    plant_id = f"{slug}-{short_id}"
    client = await get_temporal_client()

    await client.start_workflow(
        PlantWorkflow.run,
        PlantWorkflowInput(
            plant_id=plant_id,
            name=body.name,
            species=body.species,
            room=body.room,
        ),
        id=f"plant-{plant_id}",
        task_queue=settings.temporal_task_queue,
    )

    # Return initial state — care ranges will be populated asynchronously
    # by the workflow's first activity. The UI should poll or reload.
    handle = client.get_workflow_handle(f"plant-{plant_id}")
    try:
        state: PlantState = await handle.query(PlantWorkflow.get_state)
    except Exception:
        # Workflow may not have processed its first task yet — return stub
        from models.plant import CareRanges, PlantStatus
        from datetime import datetime
        state = PlantState(
            plant_id=plant_id,
            name=body.name,
            species=body.species,
            care_ranges=CareRanges(
                soil_moisture_min=0, soil_moisture_max=100,
                temperature_min=0, temperature_max=50,
                air_humidity_min=0, air_humidity_max=100,
            ),
            care_ranges_source="unknown",
            status=PlantStatus.UNKNOWN,
            created_at=datetime.utcnow(),
        )

    return state


@app.get("/plants", response_model=list[PlantState])
async def list_plants():
    """
    List all running plant workflows by querying each one's state.

    Uses the Temporal visibility API to find all running PlantWorkflow executions.
    """
    client = await get_temporal_client()

    from models.plant import CareRanges, PlantStatus

    plants: list[PlantState] = []
    async for workflow_exec in client.list_workflows(
        'WorkflowType = "PlantWorkflow" AND ExecutionStatus = "Running"'
    ):
        handle = client.get_workflow_handle(workflow_exec.id)
        try:
            state: PlantState = await handle.query(PlantWorkflow.get_state)
            plants.append(state)
        except Exception as e:
            # Query failed (e.g. workflow is mid-replay, executing an activity,
            # or in the middle of continue-as-new).  Log it and include a
            # minimal fallback stub so the plant still appears in the UI
            # rather than silently vanishing.
            logger.warning(
                "Could not query workflow %s — including fallback stub. Error: %s",
                workflow_exec.id,
                e,
            )
            # Workflow ID format is "plant-{plant_id}"
            plant_id = workflow_exec.id.removeprefix("plant-")
            plants.append(
                PlantState(
                    plant_id=plant_id,
                    name=plant_id,  # best available without query
                    species="",
                    care_ranges=CareRanges(
                        soil_moisture_min=0,
                        soil_moisture_max=100,
                        temperature_min=0,
                        temperature_max=50,
                        air_humidity_min=0,
                        air_humidity_max=100,
                    ),
                    care_ranges_source="unknown",
                    status=PlantStatus.UNKNOWN,
                    created_at=workflow_exec.start_time,
                )
            )

    plants.sort(key=lambda p: p.created_at)
    return plants


@app.get("/plants/{plant_id}", response_model=PlantState)
async def get_plant(plant_id: str):
    """Get the current state of a specific plant workflow."""
    handle = await _get_plant_handle(plant_id)
    try:
        state: PlantState = await handle.query(PlantWorkflow.get_state)
        return state
    except RPCError as e:
        if e.status == RPCStatusCode.NOT_FOUND:
            raise HTTPException(status_code=404, detail=f"Plant {plant_id!r} not found")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/plants/{plant_id}/care-ranges", response_model=PlantState)
async def update_care_ranges(plant_id: str, body: UpdateCareRangesRequest):
    """Update the care ranges for a plant with synchronous validation.

    Uses a Temporal Update (not Signal) so that:
    - The validator runs before any state mutation.
    - Validation errors are returned synchronously to the caller.
    - No sleep/polling hack is needed — execute_update blocks until processed.
    """
    handle = await _get_plant_handle(plant_id)
    try:
        await handle.execute_update(PlantWorkflow.update_care_ranges, body.care_ranges)
        state: PlantState = await handle.query(PlantWorkflow.get_state)
        return state
    except WorkflowUpdateFailedError as e:
        # Validator raised — return the rejection reason as HTTP 422
        detail = str(e.cause) if e.cause else str(e)
        raise HTTPException(status_code=422, detail=detail)
    except RPCError as e:
        if e.status == RPCStatusCode.NOT_FOUND:
            raise HTTPException(status_code=404, detail=f"Plant {plant_id!r} not found")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/plants/{plant_id}/sensor", response_model=PlantState)
async def associate_sensor(plant_id: str, body: AssociateSensorRequest):
    """Legacy: associate a single Zigbee plant sensor entity ID with this plant."""
    handle = await _get_plant_handle(plant_id)
    try:
        await handle.signal(PlantWorkflow.associate_sensor, body.sensor_entity_id)
        await asyncio.sleep(0.5)
        state: PlantState = await handle.query(PlantWorkflow.get_state)
        return state
    except RPCError as e:
        if e.status == RPCStatusCode.NOT_FOUND:
            raise HTTPException(status_code=404, detail=f"Plant {plant_id!r} not found")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/plants/{plant_id}/device", response_model=PlantState)
async def associate_device(plant_id: str, body: AssociateDeviceRequest):
    """Associate a Home Assistant plant sensor device with this plant."""
    handle = await _get_plant_handle(plant_id)
    try:
        await handle.signal(
            PlantWorkflow.associate_device,
            args=[body.device_id, body.device_name, body.sensor_entities],
        )
        await asyncio.sleep(0.5)
        state: PlantState = await handle.query(PlantWorkflow.get_state)
        return state
    except RPCError as e:
        if e.status == RPCStatusCode.NOT_FOUND:
            raise HTTPException(status_code=404, detail=f"Plant {plant_id!r} not found")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/plants/{plant_id}/water", response_model=PlantState)
async def log_watering(plant_id: str, body: Optional[LogWateringRequest] = None):
    """
    Record that a plant was watered.

    If body.watered_at is provided, that datetime is used; otherwise defaults
    to the current time. Sends a record_watering signal to the workflow, which:
    - Sets last_watered_at to the specified (or current) time
    - Clears any 'watering_overdue' warning
    - Transitions status to OK (from any non-terminal status) if no other issues
    """
    handle = await _get_plant_handle(plant_id)
    try:
        # Convert datetime to ISO string for the signal argument (None = use now)
        watered_at_iso: Optional[str] = None
        if body and body.watered_at:
            watered_at_iso = body.watered_at.isoformat()

        await handle.signal(PlantWorkflow.record_watering, watered_at_iso)
        await asyncio.sleep(0.5)
        state: PlantState = await handle.query(PlantWorkflow.get_state)
        return state
    except RPCError as e:
        if e.status == RPCStatusCode.NOT_FOUND:
            raise HTTPException(status_code=404, detail=f"Plant {plant_id!r} not found")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/plants/{plant_id}/refresh", response_model=PlantState)
async def refresh_plant(plant_id: str):
    """Force an immediate sensor poll for this plant."""
    handle = await _get_plant_handle(plant_id)
    try:
        await handle.signal(PlantWorkflow.refresh_readings)
        import asyncio
        await asyncio.sleep(0.5)
        state: PlantState = await handle.query(PlantWorkflow.get_state)
        return state
    except RPCError as e:
        if e.status == RPCStatusCode.NOT_FOUND:
            raise HTTPException(status_code=404, detail=f"Plant {plant_id!r} not found")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/plants/{plant_id}/status", response_model=PlantState)
async def update_plant_status(plant_id: str, body: UpdatePlantStatusRequest):
    """
    Change the lifecycle status of a plant with synchronous validation.

    Uses a Temporal Update (not Signal) so that unknown status strings are
    rejected synchronously rather than silently dropped.
    Terminal statuses (e.g. 'dead', 'given_away') cause the workflow to exit
    cleanly; the last-known state is returned before it fully completes.
    """
    handle = await _get_plant_handle(plant_id)
    is_terminal = body.status in TERMINAL_STATUSES
    try:
        await handle.execute_update(PlantWorkflow.set_plant_status, body.status.value)

        # For terminal statuses the workflow begins exiting immediately after the
        # update is processed — the query may race with completion, so fall back
        # gracefully if the workflow is already gone.
        if is_terminal:
            from models.plant import CareRanges
            from datetime import datetime
            try:
                state: PlantState = await handle.query(PlantWorkflow.get_state)
            except Exception:
                state = PlantState(
                    plant_id=plant_id,
                    name="",
                    species="",
                    care_ranges=CareRanges(
                        soil_moisture_min=0, soil_moisture_max=100,
                        temperature_min=0, temperature_max=50,
                        air_humidity_min=0, air_humidity_max=100,
                    ),
                    care_ranges_source="unknown",
                    status=body.status,
                    created_at=datetime.utcnow(),
                )
            return state

        state = await handle.query(PlantWorkflow.get_state)
        return state
    except WorkflowUpdateFailedError as e:
        # Validator rejected the status value — return the reason as HTTP 422
        detail = str(e.cause) if e.cause else str(e)
        raise HTTPException(status_code=422, detail=detail)
    except RPCError as e:
        if e.status == RPCStatusCode.NOT_FOUND:
            raise HTTPException(status_code=404, detail=f"Plant {plant_id!r} not found")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/plants/{plant_id}/room", response_model=PlantState)
async def update_room(plant_id: str, body: UpdateRoomRequest):
    """Move a plant to a room, or clear its room (room=null)."""
    handle = await _get_plant_handle(plant_id)
    try:
        await handle.signal(PlantWorkflow.set_room, body.room)
        await asyncio.sleep(0.3)
        state: PlantState = await handle.query(PlantWorkflow.get_state)
        return state
    except RPCError as e:
        if e.status == RPCStatusCode.NOT_FOUND:
            raise HTTPException(status_code=404, detail=f"Plant {plant_id!r} not found")
        raise HTTPException(status_code=500, detail=str(e))


# ---- Sensors (Home Assistant) — legacy entity-level ----------------------

@app.get("/sensors", response_model=list[HASensor])
async def list_sensors():
    """
    List available plant sensor entities from Home Assistant (legacy endpoint).
    Prefer GET /devices for device-level grouping.
    """
    import httpx
    from models.config import settings as cfg

    if not cfg.ha_token:
        raise HTTPException(
            status_code=503,
            detail="Home Assistant is not configured. Set HA_URL and HA_TOKEN in .env",
        )

    headers = {
        "Authorization": f"Bearer {cfg.ha_token}",
        "Content-Type": "application/json",
    }

    _PLANT_SENSOR_KEYWORDS = [
        "miflora", "plant", "flora", "hhcc", "parrot",
        "flower_care", "flower_power", "smart_plant",
    ]

    try:
        async with httpx.AsyncClient(timeout=15) as client_http:
            resp = await client_http.get(
                f"{cfg.ha_url.rstrip('/')}/api/states",
                headers=headers,
            )
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Could not reach Home Assistant: {e}",
        )

    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid Home Assistant token")
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Home Assistant returned {resp.status_code}",
        )

    all_states = resp.json()
    sensors: list[HASensor] = []
    seen_friendly: set[str] = set()

    for entity in all_states:
        entity_id: str = entity.get("entity_id", "")
        attributes: dict = entity.get("attributes", {})
        friendly_name: str = attributes.get("friendly_name", entity_id)
        domain = entity_id.split(".")[0] if "." in entity_id else ""

        if domain not in ("sensor", "plant"):
            continue

        lower_id = entity_id.lower()
        lower_name = friendly_name.lower()
        is_plant_sensor = any(
            kw in lower_id or kw in lower_name for kw in _PLANT_SENSOR_KEYWORDS
        )
        if not is_plant_sensor:
            continue

        if friendly_name in seen_friendly:
            continue
        seen_friendly.add(friendly_name)

        sensors.append(
            HASensor(
                entity_id=entity_id,
                friendly_name=friendly_name,
                state=entity.get("state"),
            )
        )

    return sensors


# ---- Devices (Home Assistant) — device-level grouping --------------------

# Measurement suffixes appended to entity IDs by plant sensor integrations.
# Used to strip the suffix and recover the base device name prefix, and to
# infer device_class when it is absent from state attributes.
# Order matters: longer suffixes must appear before their shorter substrings
# (e.g. "soil_moisture" before "moisture").
_MEASUREMENT_SUFFIXES = [
    "soil_moisture", "moisture", "conductivity", "temperature",
    "air_humidity", "humidity", "illuminance", "light", "battery",
]

# Maps entity_id trailing suffixes → canonical device_class.
_SUFFIX_TO_DEVICE_CLASS: dict[str, str] = {
    "soil_moisture": "moisture",
    "moisture": "moisture",
    "conductivity": "moisture",   # some probes expose conductivity as proxy for moisture
    "temperature": "temperature",
    "air_humidity": "humidity",
    "humidity": "humidity",      # re-mapped to moisture below for plant_sensor_* entities
    "illuminance": "illuminance",
    "light": "illuminance",
    "battery": "battery",
}


def _device_prefix(entity_id: str) -> str:
    """
    Derive a stable device-group key from a sensor entity_id by stripping
    the trailing measurement suffix.

    e.g. "sensor.miflora_desk_moisture"  -> "miflora_desk"
         "sensor.hhcc_plant_temperature" -> "hhcc_plant"
         "sensor.spike_soil_moisture"    -> "spike"
    """
    name = entity_id.removeprefix("sensor.")
    for suffix in _MEASUREMENT_SUFFIXES:
        if name.endswith(f"_{suffix}"):
            return name[: -(len(suffix) + 1)]
    # Fallback: strip the last underscore segment
    if "_" in name:
        return name.rsplit("_", 1)[0]
    return name


@app.get("/devices", response_model=list[HADevice])
async def list_devices():
    """
    List available plant sensor *devices* from Home Assistant, with their
    sensor entities grouped by device.

    Derives device groupings directly from /api/states by clustering sensor.*
    entities that share a common name prefix and have a plant-relevant
    device_class — no device/entity registry endpoints required.
    """
    import httpx
    from models.config import settings as cfg

    if not cfg.ha_token:
        raise HTTPException(
            status_code=503,
            detail="Home Assistant is not configured. Set HA_URL and HA_TOKEN in .env",
        )

    headers = {
        "Authorization": f"Bearer {cfg.ha_token}",
        "Content-Type": "application/json",
    }

    ha_base = cfg.ha_url.rstrip("/")

    try:
        async with httpx.AsyncClient(timeout=15) as http:
            states_resp = await http.get(f"{ha_base}/api/states", headers=headers)
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Could not reach Home Assistant: {e}")

    if states_resp.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid Home Assistant token")
    if states_resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Home Assistant returned {states_resp.status_code}",
        )

    all_states: list[dict] = states_resp.json()

    # Collect all sensor.plant_sensor_* entities and group by device prefix.
    # The entity_id pattern is the sole inclusion criterion — no device_class
    # filtering needed since every entity in this namespace belongs to a plant probe.
    groups: dict[str, list[HADeviceEntity]] = {}
    for entity in all_states:
        entity_id: str = entity.get("entity_id", "")
        if not entity_id.startswith("sensor.plant_sensor_"):
            continue

        attributes: dict = entity.get("attributes", {})
        friendly_name: str = attributes.get("friendly_name") or entity_id

        # Determine device_class: prefer the attribute value, fall back to
        # inferring from the entity_id suffix.
        device_class: str = (attributes.get("device_class") or "").lower()
        if not device_class:
            name_part = entity_id.removeprefix("sensor.")
            for suffix, dc in _SUFFIX_TO_DEVICE_CLASS.items():
                if name_part.endswith(f"_{suffix}"):
                    device_class = dc
                    break

        # On these plant probes the _humidity entity reports soil moisture —
        # always reclassify it so it displays and sorts as moisture.
        if device_class == "humidity":
            device_class = "moisture"

        prefix = _device_prefix(entity_id)
        groups.setdefault(prefix, []).append(
            HADeviceEntity(
                entity_id=entity_id,
                friendly_name=friendly_name,
                device_class=device_class or None,
            )
        )

    # Build one HADevice per prefix group.
    _DC_ORDER = {"moisture": 0, "temperature": 1, "humidity": 2, "illuminance": 3, "battery": 4}
    result: list[HADevice] = []

    for prefix, entities in groups.items():
        entities.sort(key=lambda e: _DC_ORDER.get(e.device_class or "", 99))

        # Derive a human-friendly device name from the first entity's friendly_name:
        # strip the trailing measurement word (e.g. "MiFlora Desk Moisture" → "MiFlora Desk")
        sample_name = entities[0].friendly_name or prefix
        for suffix in _MEASUREMENT_SUFFIXES:
            if sample_name.lower().endswith(f" {suffix}"):
                sample_name = sample_name[: -(len(suffix) + 1)]
                break

        result.append(HADevice(
            device_id=prefix,
            name=sample_name,
            manufacturer=None,
            model=None,
            area_name=None,
            entities=entities,
        ))

    result.sort(key=lambda d: d.name.lower())
    return result
