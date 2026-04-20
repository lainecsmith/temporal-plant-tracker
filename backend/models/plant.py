from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Care ranges — acceptable values for each metric
# ---------------------------------------------------------------------------

class CareRanges(BaseModel):
    """Acceptable ranges for plant health metrics."""

    soil_moisture_min: float = Field(description="Minimum soil moisture (%)")
    soil_moisture_max: float = Field(description="Maximum soil moisture (%)")
    temperature_min: float = Field(description="Minimum temperature (°F)")
    temperature_max: float = Field(description="Maximum temperature (°F)")
    air_humidity_min: float = Field(description="Minimum air humidity (%)")
    air_humidity_max: float = Field(description="Maximum air humidity (%)")
    light_lux_min: Optional[float] = Field(
        default=None, description="Minimum light level (lux)"
    )
    light_lux_max: Optional[float] = Field(
        default=None, description="Maximum light level (lux)"
    )


class CareRangesWithReasoning(CareRanges):
    """CareRanges extended with GPT-4o explanations for each metric range."""

    soil_moisture_reasoning: str = Field(
        description="Why this soil moisture range was chosen for this species"
    )
    temperature_reasoning: str = Field(
        description="Why this temperature range was chosen for this species"
    )
    air_humidity_reasoning: str = Field(
        description="Why this air humidity range was chosen for this species"
    )
    light_lux_reasoning: str = Field(
        description="Why this light level range was chosen for this species"
    )


# ---------------------------------------------------------------------------
# Sensor readings — current values from the Zigbee sensor
# ---------------------------------------------------------------------------

class SensorReadings(BaseModel):
    """Current sensor values reported by the Zigbee plant probe."""

    soil_moisture: Optional[float] = None   # %
    temperature: Optional[float] = None     # °F
    air_humidity: Optional[float] = None    # %
    light_lux: Optional[float] = None       # lux
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Plant status
# ---------------------------------------------------------------------------

class PlantStatus(str, Enum):
    OK = "ok"
    WARNING = "warning"       # one or more metrics out of range
    UNKNOWN = "unknown"       # no sensor associated yet, or no readings


# ---------------------------------------------------------------------------
# Full plant state — this is what the workflow holds in memory
# ---------------------------------------------------------------------------

class PlantState(BaseModel):
    """Complete state of a plant, stored in (and returned by) its workflow."""

    plant_id: str
    name: str
    species: str

    care_ranges: CareRanges
    # Where the care ranges came from
    care_ranges_source: str = "unknown"  # "openplantbook" | "ai" | "manual"
    # Per-metric AI reasoning — only populated when care_ranges_source == "ai"
    care_ranges_reasoning: Optional[dict[str, str]] = None

    # Legacy single-entity association (kept for backward compat)
    sensor_entity_id: Optional[str] = None  # HA entity id, e.g. "sensor.miflora_1"

    # Device-level association (preferred)
    sensor_device_id: Optional[str] = None          # HA device registry id
    sensor_device_name: Optional[str] = None        # Human-readable device name
    sensor_entities: Optional[dict[str, str]] = None  # device_class -> entity_id

    last_readings: Optional[SensorReadings] = None
    out_of_range_fields: list[str] = Field(default_factory=list)  # e.g. ["soil_moisture"]
    status: PlantStatus = PlantStatus.UNKNOWN

    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_checked_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Home Assistant sensor descriptor (legacy entity-level model)
# ---------------------------------------------------------------------------

class HASensor(BaseModel):
    """A Zigbee plant sensor entity available in Home Assistant."""

    entity_id: str          # e.g. "sensor.miflora_living_room"
    friendly_name: str      # e.g. "MiFlora Living Room"
    state: Optional[str] = None


# ---------------------------------------------------------------------------
# Home Assistant device-level models
# ---------------------------------------------------------------------------

class HADeviceEntity(BaseModel):
    """A single sensor entity belonging to a plant sensor device."""

    entity_id: str
    friendly_name: str
    device_class: Optional[str] = None  # "moisture", "temperature", "humidity", "illuminance", "battery"


class HADevice(BaseModel):
    """A Home Assistant device containing one or more plant sensor entities."""

    device_id: str
    name: str
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    area_name: Optional[str] = None
    entities: list[HADeviceEntity] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# API request / response bodies
# ---------------------------------------------------------------------------

class CreatePlantRequest(BaseModel):
    name: str
    species: str


class UpdateCareRangesRequest(BaseModel):
    care_ranges: CareRanges


class AssociateSensorRequest(BaseModel):
    """Legacy: associate by a single entity ID."""
    sensor_entity_id: str


class AssociateDeviceRequest(BaseModel):
    """Associate a plant sensor device by device ID and resolved entity mapping."""
    device_id: str
    device_name: str
    sensor_entities: dict[str, str]  # device_class -> entity_id
