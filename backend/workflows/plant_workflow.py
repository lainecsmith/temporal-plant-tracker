"""
PlantWorkflow — Entity workflow representing a single plant.

Each plant is a long-running Temporal workflow with ID "plant-{plant_id}".
The workflow:
  1. Fetches care ranges from OpenPlantbook (or AI as fallback) on creation.
  2. Waits for the user to associate a Zigbee sensor device via signal.
  3. Polls the sensor hourly, compares readings to care ranges, and triggers
     Home Assistant alerts when readings are out of range.
  4. Uses continue-as-new to prevent unbounded history growth.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Optional

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    import pydantic  # noqa: F401 — ensures pydantic_core/annotated_types load inside sandbox
    from activities.openplantbook import search_openplantbook
    from activities.llm import get_care_ranges_from_ai
    from activities.home_assistant import (
        get_sensor_readings,
        trigger_ha_alert,
        clear_ha_alert_light,
    )
    from models.plant import CareRanges, CareRangesWithReasoning, PlantState, PlantStatus, SensorReadings, TERMINAL_STATUSES


# ---------------------------------------------------------------------------
# Input / continuation models
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field


@dataclass
class PlantWorkflowInput:
    plant_id: str
    name: str
    species: str


@dataclass
class PlantWorkflowContinuation:
    """Passed to continue-as-new so the new execution picks up where we left off."""
    plant_id: str
    name: str
    species: str
    care_ranges: CareRanges
    care_ranges_source: str
    sensor_entity_id: Optional[str]
    last_readings: Optional[SensorReadings]
    out_of_range_fields: list
    status: str  # PlantStatus value
    created_at: str  # ISO datetime string
    last_checked_at: Optional[str]
    # If True, skip the initial fetch — ranges already loaded
    ranges_already_fetched: bool = False
    # Device-level sensor association (preferred over sensor_entity_id)
    sensor_device_id: Optional[str] = None
    sensor_device_name: Optional[str] = None
    sensor_entities: Optional[dict] = None  # device_class -> entity_id
    # Per-metric AI reasoning (populated only when source == "ai")
    care_ranges_reasoning: Optional[dict] = None


# ---------------------------------------------------------------------------
# Polling interval
# ---------------------------------------------------------------------------

POLL_INTERVAL = timedelta(hours=1)


# ---------------------------------------------------------------------------
# Workflow definition
# ---------------------------------------------------------------------------

@workflow.defn
class PlantWorkflow:
    """
    Long-running entity workflow for a single plant.

    Workflow ID convention: "plant-{plant_id}"
    """

    @workflow.init
    def __init__(self, input: PlantWorkflowInput | PlantWorkflowContinuation) -> None:
        # -------------------------------------------------------------------
        # Core identity
        # -------------------------------------------------------------------
        self._plant_id: str = input.plant_id
        self._name: str = input.name
        self._species: str = input.species

        # -------------------------------------------------------------------
        # Care ranges — populated after OpenPlantbook/AI lookup
        # -------------------------------------------------------------------
        self._care_ranges: Optional[CareRanges] = None
        self._care_ranges_source: str = "unknown"
        self._care_ranges_reasoning: Optional[dict[str, str]] = None
        self._ranges_already_fetched: bool = False

        # -------------------------------------------------------------------
        # Sensor & readings (legacy single-entity)
        # -------------------------------------------------------------------
        self._sensor_entity_id: Optional[str] = None

        # -------------------------------------------------------------------
        # Sensor & readings (device-level — preferred)
        # -------------------------------------------------------------------
        self._sensor_device_id: Optional[str] = None
        self._sensor_device_name: Optional[str] = None
        self._sensor_entities: Optional[dict[str, str]] = None  # device_class -> entity_id

        self._last_readings: Optional[SensorReadings] = None
        self._out_of_range_fields: list[str] = []
        self._status: PlantStatus = PlantStatus.UNKNOWN

        # -------------------------------------------------------------------
        # Timestamps
        # -------------------------------------------------------------------
        self._created_at: datetime = workflow.now()
        self._last_checked_at: Optional[datetime] = None

        # -------------------------------------------------------------------
        # Restore state when resuming via continue-as-new
        # -------------------------------------------------------------------
        if isinstance(input, PlantWorkflowContinuation):
            if input.care_ranges is not None:
                self._care_ranges = input.care_ranges
            self._care_ranges_source = input.care_ranges_source
            self._care_ranges_reasoning = input.care_ranges_reasoning or None
            self._ranges_already_fetched = input.ranges_already_fetched
            self._sensor_entity_id = input.sensor_entity_id
            self._sensor_device_id = input.sensor_device_id
            self._sensor_device_name = input.sensor_device_name
            self._sensor_entities = input.sensor_entities
            self._last_readings = input.last_readings
            self._out_of_range_fields = input.out_of_range_fields or []
            self._status = PlantStatus(input.status)
            try:
                self._created_at = datetime.fromisoformat(input.created_at)
            except (ValueError, TypeError):
                pass
            if input.last_checked_at:
                try:
                    self._last_checked_at = datetime.fromisoformat(input.last_checked_at)
                except (ValueError, TypeError):
                    pass

        # Force-wakeup flag used to skip sleep on refresh_readings signal
        self._force_poll: bool = False

        # Set to True when a terminal status is received — causes workflow to exit cleanly
        self._stop_requested: bool = False

    # -----------------------------------------------------------------------
    # Helpers: is a sensor associated?
    # -----------------------------------------------------------------------

    def _has_sensor(self) -> bool:
        """Return True if any sensor (device or legacy entity) is associated."""
        return self._sensor_device_id is not None or self._sensor_entity_id is not None

    # -----------------------------------------------------------------------
    # Signals
    # -----------------------------------------------------------------------

    #TODO: this should be an update
    @workflow.signal
    def update_care_ranges(self, care_ranges: CareRanges) -> None:
        """User has edited the care ranges from the UI."""
        workflow.logger.info(
            f"[{self._name}] Care ranges updated by user"
        )
        self._care_ranges = care_ranges
        self._care_ranges_source = "manual"

    @workflow.signal
    def associate_sensor(self, sensor_entity_id: str) -> None:
        """Legacy: associate a single HA entity ID with this plant."""
        workflow.logger.info(
            f"[{self._name}] Sensor associated (legacy): {sensor_entity_id}"
        )
        self._sensor_entity_id = sensor_entity_id

    @workflow.signal
    def associate_device(self, device_id: str, device_name: str, sensor_entities: dict) -> None:
        """Associate a Home Assistant device (with its entity map) with this plant."""
        workflow.logger.info(
            f"[{self._name}] Device associated: {device_name!r} ({device_id}), "
            f"entities: {list(sensor_entities.keys())}"
        )
        self._sensor_device_id = device_id
        self._sensor_device_name = device_name
        self._sensor_entities = sensor_entities
        # Also set sensor_entity_id to the first available entity for backward compat
        if sensor_entities:
            self._sensor_entity_id = next(iter(sensor_entities.values()))

    @workflow.signal
    def refresh_readings(self) -> None:
        """Force an immediate sensor poll, skipping the current sleep."""
        workflow.logger.info(f"[{self._name}] Immediate refresh requested")
        self._force_poll = True

    @workflow.signal
    def set_plant_status(self, status: str) -> None:
        """
        Change the plant's lifecycle status.

        If the new status is a terminal status (e.g. 'dead', 'given_away'),
        the workflow will exit cleanly after this signal is processed.
        """
        try:
            new_status = PlantStatus(status)
        except ValueError:
            workflow.logger.error(
                f"[{self._name}] Unknown status {status!r} — ignoring"
            )
            return

        workflow.logger.info(
            f"[{self._name}] Status changed to {new_status.value!r}"
        )
        self._status = new_status

        if new_status in TERMINAL_STATUSES:
            workflow.logger.info(
                f"[{self._name}] Terminal status set — workflow will exit cleanly"
            )
            self._stop_requested = True

    # -----------------------------------------------------------------------
    # Query
    # -----------------------------------------------------------------------

    @workflow.query
    def get_state(self) -> PlantState:
        """Return the current full state of this plant."""
        return PlantState(
            plant_id=self._plant_id,
            name=self._name,
            species=self._species,
            care_ranges=self._care_ranges or CareRanges(
                soil_moisture_min=0, soil_moisture_max=100,
                temperature_min=0, temperature_max=50,
                air_humidity_min=0, air_humidity_max=100,
            ),
            care_ranges_source=self._care_ranges_source,
            care_ranges_reasoning=self._care_ranges_reasoning,
            sensor_entity_id=self._sensor_entity_id,
            sensor_device_id=self._sensor_device_id,
            sensor_device_name=self._sensor_device_name,
            sensor_entities=self._sensor_entities,
            last_readings=self._last_readings,
            out_of_range_fields=self._out_of_range_fields,
            status=self._status,
            created_at=self._created_at,
            last_checked_at=self._last_checked_at,
        )

    # -----------------------------------------------------------------------
    # Main workflow logic
    # -----------------------------------------------------------------------

    @workflow.run
    async def run(self, input: PlantWorkflowInput | PlantWorkflowContinuation) -> None:
        # -------------------------------------------------------------------
        # Phase 1: Fetch care ranges (skip if resuming via continue-as-new)
        # -------------------------------------------------------------------
        if not self._ranges_already_fetched or self._care_ranges is None:
            await self._fetch_care_ranges()

        # -------------------------------------------------------------------
        # Phase 2: Wait until sensor is associated
        # (May already be set if resuming via continue-as-new)
        # -------------------------------------------------------------------
        if not self._has_sensor():
            workflow.logger.info(
                f"[{self._name}] Waiting for sensor association..."
            )
            await workflow.wait_condition(
                lambda: self._has_sensor() or self._stop_requested
            )
            if self._stop_requested:
                workflow.logger.info(
                    f"[{self._name}] Terminal status received while awaiting sensor — exiting"
                )
                return
            workflow.logger.info(
                f"[{self._name}] Sensor associated: device={self._sensor_device_id!r} "
                f"entity={self._sensor_entity_id!r}"
            )

        # -------------------------------------------------------------------
        # Phase 3: Hourly polling loop
        # -------------------------------------------------------------------
        workflow.logger.info(
            f"[{self._name}] Starting hourly sensor polling loop"
        )

        while True:
            # Exit cleanly if a terminal status was signalled
            if self._stop_requested:
                workflow.logger.info(
                    f"[{self._name}] Exiting workflow (status={self._status.value!r})"
                )
                return

            # Check if Temporal recommends we continue-as-new
            if workflow.info().is_continue_as_new_suggested():
                workflow.logger.info(
                    f"[{self._name}] History growing large — continuing as new"
                )
                workflow.continue_as_new(
                    args=[self._build_continuation()],
                )

            # Poll the sensor
            await self._poll_sensor()

            # Sleep for 1 hour, but allow refresh_readings or stop signal to wake us early
            self._force_poll = False
            try:
                await workflow.wait_condition(
                    lambda: self._force_poll or self._stop_requested,
                    timeout=POLL_INTERVAL,
                )
                if self._stop_requested:
                    workflow.logger.info(
                        f"[{self._name}] Woken by terminal status signal — exiting"
                    )
                    return
                workflow.logger.info(f"[{self._name}] Early wakeup due to refresh signal")
            except asyncio.TimeoutError:
                pass  # Normal hourly tick

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    async def _fetch_care_ranges(self) -> None:
        """Try OpenPlantbook first; fall back to GPT-4o if not found."""
        workflow.logger.info(
            f"[{self._name}] Fetching care ranges for species: {self._species!r}"
        )

        retry_policy = RetryPolicy(
            initial_interval=timedelta(seconds=5),
            maximum_interval=timedelta(minutes=2),
            maximum_attempts=5,
        )

        # Try OpenPlantbook
        care_ranges: Optional[CareRanges] = await workflow.execute_activity(
            search_openplantbook,
            self._species,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=retry_policy,
        )

        if care_ranges is not None:
            workflow.logger.info(
                f"[{self._name}] Found care ranges in OpenPlantbook"
            )
            # OpenPlantbook returns temperatures in Celsius — convert to Fahrenheit
            self._care_ranges = _convert_care_ranges_temp_to_f(care_ranges)
            self._care_ranges_source = "openplantbook"
        else:
            # Fallback to AI
            workflow.logger.info(
                f"[{self._name}] Not found in OpenPlantbook — asking AI"
            )
            ai_ranges: CareRangesWithReasoning = await workflow.execute_activity(
                get_care_ranges_from_ai,
                self._species,
                start_to_close_timeout=timedelta(seconds=90),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=10),
                    maximum_interval=timedelta(minutes=5),
                    maximum_attempts=3,
                ),
            )
            # Extract reasoning before temperature conversion (model_copy preserves it)
            self._care_ranges_reasoning = {
                "soil_moisture_reasoning": ai_ranges.soil_moisture_reasoning,
                "temperature_reasoning": ai_ranges.temperature_reasoning,
                "air_humidity_reasoning": ai_ranges.air_humidity_reasoning,
                "light_lux_reasoning": ai_ranges.light_lux_reasoning,
            }
            # AI returns temperatures in Celsius — convert to Fahrenheit
            self._care_ranges = _convert_care_ranges_temp_to_f(ai_ranges)
            self._care_ranges_source = "ai"

        self._ranges_already_fetched = True
        workflow.logger.info(
            f"[{self._name}] Care ranges ready (source: {self._care_ranges_source})"
        )

    async def _poll_sensor(self) -> None:
        """Read sensor, compare to care ranges, trigger alerts if needed."""
        if not self._has_sensor() or self._care_ranges is None:
            return

        # Prefer device-level entities dict; fall back to legacy entity_id
        activity_arg = self._sensor_entities or self._sensor_entity_id
        workflow.logger.info(
            f"[{self._name}] Polling sensor (device={self._sensor_device_id!r}, "
            f"entity={self._sensor_entity_id!r})"
        )

        try:
            readings: SensorReadings = await workflow.execute_activity(
                get_sensor_readings,
                activity_arg,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=5),
                    maximum_interval=timedelta(minutes=1),
                    maximum_attempts=3,
                ),
            )
        except Exception as e:
            workflow.logger.error(
                f"[{self._name}] Failed to read sensor: {e}"
            )
            return

        self._last_readings = readings
        self._last_checked_at = workflow.now()

        # Compare readings to care ranges
        out_of_range = _check_out_of_range(readings, self._care_ranges)
        previous_issues = set(self._out_of_range_fields)
        self._out_of_range_fields = out_of_range

        # Use device name or entity id for alerts
        alert_entity = self._sensor_entity_id or self._sensor_device_id or ""

        if out_of_range:
            self._status = PlantStatus.WARNING
            workflow.logger.warning(
                f"[{self._name}] Out of range: {out_of_range}"
            )
            await workflow.execute_activity(
                trigger_ha_alert,
                args=[
                    self._name,
                    alert_entity,
                    out_of_range,
                    readings,
                    self._care_ranges,
                ],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
        else:
            self._status = PlantStatus.OK
            # If we just cleared a warning, turn the light green
            if previous_issues:
                workflow.logger.info(
                    f"[{self._name}] All metrics back in range — clearing alert"
                )
                await workflow.execute_activity(
                    clear_ha_alert_light,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )

    def _build_continuation(self) -> "PlantWorkflowContinuation":
        return PlantWorkflowContinuation(
            plant_id=self._plant_id,
            name=self._name,
            species=self._species,
            care_ranges=self._care_ranges,
            care_ranges_source=self._care_ranges_source,
            care_ranges_reasoning=self._care_ranges_reasoning,
            sensor_entity_id=self._sensor_entity_id,
            sensor_device_id=self._sensor_device_id,
            sensor_device_name=self._sensor_device_name,
            sensor_entities=self._sensor_entities,
            last_readings=self._last_readings,
            out_of_range_fields=self._out_of_range_fields,
            status=self._status.value,
            created_at=self._created_at.isoformat(),
            last_checked_at=(
                self._last_checked_at.isoformat() if self._last_checked_at else None
            ),
            ranges_already_fetched=True,
        )


# ---------------------------------------------------------------------------
# Helper: unit conversion
# ---------------------------------------------------------------------------

def _c_to_f(celsius: float) -> float:
    """Convert Celsius to Fahrenheit."""
    return celsius * 9 / 5 + 32


def _convert_care_ranges_temp_to_f(ranges: CareRanges) -> CareRanges:
    """Return a new CareRanges with temperature_min/max converted from °C to °F."""
    return ranges.model_copy(update={
        "temperature_min": _c_to_f(ranges.temperature_min),
        "temperature_max": _c_to_f(ranges.temperature_max),
    })


# ---------------------------------------------------------------------------
# Helper: compare readings to care ranges
# ---------------------------------------------------------------------------

def _check_out_of_range(
    readings: SensorReadings, ranges: CareRanges
) -> list[str]:
    """Return a list of metric names that are outside the acceptable ranges."""
    issues: list[str] = []

    if readings.soil_moisture is not None:
        if (
            readings.soil_moisture < ranges.soil_moisture_min
            or readings.soil_moisture > ranges.soil_moisture_max
        ):
            issues.append("soil_moisture")

    if readings.temperature is not None:
        if (
            readings.temperature < ranges.temperature_min
            or readings.temperature > ranges.temperature_max
        ):
            issues.append("temperature")

    if readings.air_humidity is not None:
        if (
            readings.air_humidity < ranges.air_humidity_min
            or readings.air_humidity > ranges.air_humidity_max
        ):
            issues.append("air_humidity")

    if readings.light_lux is not None and ranges.light_lux_min is not None and ranges.light_lux_max is not None:
        if (
            readings.light_lux < ranges.light_lux_min
            or readings.light_lux > ranges.light_lux_max
        ):
            issues.append("light_lux")

    return issues
