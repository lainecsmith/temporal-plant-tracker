from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


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
    watering_interval_days: Optional[float] = Field(
        default=None, description="Typical number of days between waterings"
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
    watering_interval_reasoning: Optional[str] = Field(
        default=None,
        description="Why this watering interval was chosen for this species"
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
    DEAD = "dead"             # plant has died — workflow ends
    GIVEN_AWAY = "given_away" # plant was given away — workflow ends


# Statuses that cause the workflow to complete (easy to extend later)
TERMINAL_STATUSES: frozenset[PlantStatus] = frozenset({
    PlantStatus.DEAD,
    PlantStatus.GIVEN_AWAY,
})


# ---------------------------------------------------------------------------
# Full plant state — this is what the workflow holds in memory
# ---------------------------------------------------------------------------

class PlantState(BaseModel):
    """Complete state of a plant, stored in (and returned by) its workflow."""

    plant_id: str
    name: str
    species: str
    room: Optional[str] = None  # e.g. "Living Room", "Bedroom"

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
    last_watered_at: Optional[datetime] = None

    # ---------------------------------------------------------------------------
    # Last-error fields — desired-vs-applied pattern.
    # Each field is None when the last operation succeeded (or has never been
    # attempted) and contains an error string when it last failed.
    # They are cleared automatically when the corresponding operation succeeds.
    # ---------------------------------------------------------------------------

    # Set when associate_device / associate_sensor receives invalid input
    # (e.g. empty sensor_entities dict).
    last_association_error: Optional[str] = None

    # Set when get_sensor_readings fails (bad device ID, HA unreachable, etc.).
    # Cleared on the next successful sensor read.
    last_sensor_read_error: Optional[str] = None

    # Set when the care-ranges fetch (OpenPlantbook + AI fallback) fails entirely.
    # Cleared once care ranges are successfully loaded.
    last_care_ranges_fetch_error: Optional[str] = None

    # Set when trigger_ha_alert or clear_ha_alert_light fails.
    # Cleared on the next successful alert / clear call.
    last_alert_error: Optional[str] = None


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
    room: Optional[str] = None


class UpdateRoomRequest(BaseModel):
    """Request to move a plant to a different room (or clear its room assignment)."""
    room: Optional[str] = None


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


class UpdatePlantStatusRequest(BaseModel):
    """Request to change a plant's lifecycle status."""
    status: PlantStatus


class LogWateringRequest(BaseModel):
    """Request to record a watering event. watered_at defaults to now if omitted."""
    watered_at: Optional[datetime] = Field(
        default=None,
        description="When the plant was watered. Defaults to the current time if not provided.",
    )

    @field_validator("watered_at")
    @classmethod
    def must_not_be_future(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is None:
            return v
        # Normalise to UTC-aware for a safe comparison. The frontend sends an
        # ISO string with a 'Z' suffix so v will already be tz-aware; fall back
        # to treating a naive value as UTC.
        v_utc = v if v.tzinfo is not None else v.replace(tzinfo=timezone.utc)
        if v_utc > datetime.now(timezone.utc):
            raise ValueError("watered_at cannot be in the future")
        return v
