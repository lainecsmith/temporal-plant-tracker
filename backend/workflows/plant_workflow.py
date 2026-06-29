"""
PlantWorkflow — Entity workflow representing a single plant.

Each plant is a long-running Temporal workflow with ID "plant-{plant_id}".
The workflow:
  1. Fetches care ranges from OpenPlantbook (or AI as fallback) on creation.
  2. Enters the hourly polling loop immediately (no longer blocks on sensor
     association — sensorless plants benefit from watering-overdue checks).
  3. Polls the sensor hourly when a sensor is associated, compares readings to
     care ranges, and triggers Home Assistant alerts when readings are out of range.
  4. Tracks the last-watered timestamp: auto-detected via 40-point soil-moisture
     spike for sensor plants; manually recorded via the record_watering signal
     for sensorless plants.
  5. For sensorless plants with a watering interval configured, flags status as
     WARNING and adds "watering_overdue" to out_of_range_fields when overdue.
  6. Uses continue-as-new to prevent unbounded history growth.
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
    room: Optional[str] = None


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
    # Last-error fields — desired-vs-applied pattern
    last_association_error: Optional[str] = None
    last_sensor_read_error: Optional[str] = None
    last_care_ranges_fetch_error: Optional[str] = None
    last_alert_error: Optional[str] = None
    # Watering tracking
    last_watered_at: Optional[str] = None  # ISO datetime string
    # Room assignment
    room: Optional[str] = None


# ---------------------------------------------------------------------------
# Polling interval
# ---------------------------------------------------------------------------

POLL_INTERVAL = timedelta(hours=1)

# Minimum soil-moisture increase (percentage points) to auto-detect a watering
# event. Only fires when the previous reading was below soil_moisture_min.
WATERING_SPIKE_THRESHOLD = 40.0


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
        self._room: Optional[str] = input.room if hasattr(input, "room") else None

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
        self._last_watered_at: Optional[datetime] = None

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
            if input.last_watered_at:
                try:
                    self._last_watered_at = datetime.fromisoformat(input.last_watered_at)
                except (ValueError, TypeError):
                    pass

        # -------------------------------------------------------------------
        # Last-error fields — desired-vs-applied pattern.
        # Each is None when the last operation succeeded; set to an error string
        # on failure. They persist across continue-as-new via the continuation.
        # -------------------------------------------------------------------
        self._last_association_error: Optional[str] = None
        self._last_sensor_read_error: Optional[str] = None
        self._last_care_ranges_fetch_error: Optional[str] = None
        self._last_alert_error: Optional[str] = None

        # Restore error fields from continuation
        if isinstance(input, PlantWorkflowContinuation):
            self._last_association_error = input.last_association_error
            self._last_sensor_read_error = input.last_sensor_read_error
            self._last_care_ranges_fetch_error = input.last_care_ranges_fetch_error
            self._last_alert_error = input.last_alert_error

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

    @workflow.update
    def update_care_ranges(self, care_ranges: CareRanges) -> None:
        """User has edited the care ranges from the UI.

        Converted from signal → update so that validation errors are returned
        synchronously to the caller instead of being silently dropped.
        """
        workflow.logger.info(
            f"[{self._name}] Care ranges updated by user"
        )
        self._care_ranges = care_ranges
        self._care_ranges_source = "manual"

    @update_care_ranges.validator
    def validate_update_care_ranges(self, care_ranges: CareRanges) -> None:
        """Validate ranges before applying.

        Runs before any state mutation — if this raises, the handler is never
        called and the rejection reason is returned synchronously to the caller.
        This is the key advantage of Updates over Signals for entity patterns.
        """
        errors: list[str] = []
        if care_ranges.soil_moisture_min > care_ranges.soil_moisture_max:
            errors.append(
                f"soil_moisture_min ({care_ranges.soil_moisture_min}) "
                f"must be ≤ soil_moisture_max ({care_ranges.soil_moisture_max})"
            )
        if care_ranges.temperature_min > care_ranges.temperature_max:
            errors.append(
                f"temperature_min ({care_ranges.temperature_min}) "
                f"must be ≤ temperature_max ({care_ranges.temperature_max})"
            )
        if care_ranges.air_humidity_min > care_ranges.air_humidity_max:
            errors.append(
                f"air_humidity_min ({care_ranges.air_humidity_min}) "
                f"must be ≤ air_humidity_max ({care_ranges.air_humidity_max})"
            )
        if (
            care_ranges.light_lux_min is not None
            and care_ranges.light_lux_max is not None
            and care_ranges.light_lux_min > care_ranges.light_lux_max
        ):
            errors.append(
                f"light_lux_min ({care_ranges.light_lux_min}) "
                f"must be ≤ light_lux_max ({care_ranges.light_lux_max})"
            )
        if errors:
            raise ValueError("; ".join(errors))

    @workflow.signal
    def associate_sensor(self, sensor_entity_id: str) -> None:
        """Legacy: associate a single HA entity ID with this plant."""
        workflow.logger.info(
            f"[{self._name}] Sensor associated (legacy): {sensor_entity_id}"
        )
        self._sensor_entity_id = sensor_entity_id
        # Trigger an immediate poll so status updates promptly rather than
        # waiting up to an hour for the next scheduled loop iteration.
        self._force_poll = True

    @workflow.signal
    def associate_device(self, device_id: str, device_name: str, sensor_entities: dict) -> None:
        """Associate a Home Assistant device (with its entity map) with this plant.

        Stores a last_association_error if the entity map is empty — the desired
        state (device selected) is recorded, but the applied state is flagged as
        unusable until a valid entity map is provided.
        """
        workflow.logger.info(
            f"[{self._name}] Device associated: {device_name!r} ({device_id}), "
            f"entities: {list(sensor_entities.keys())}"
        )
        self._sensor_device_id = device_id
        self._sensor_device_name = device_name
        self._sensor_entities = sensor_entities

        if not sensor_entities:
            # Record the failure — no readable entities, can't poll this device
            err = (
                f"Device {device_name!r} ({device_id}) has no readable sensor entities. "
                "Sensor polling will not start until a valid device is associated."
            )
            workflow.logger.warning(f"[{self._name}] {err}")
            self._last_association_error = err
        else:
            # Valid association — clear any prior error and set the legacy entity_id
            self._last_association_error = None
            self._sensor_entity_id = next(iter(sensor_entities.values()))
            # Trigger an immediate poll so status updates promptly rather than
            # waiting up to an hour for the next scheduled loop iteration.
            self._force_poll = True

    @workflow.signal
    def refresh_readings(self) -> None:
        """Force an immediate sensor poll, skipping the current sleep."""
        workflow.logger.info(f"[{self._name}] Immediate refresh requested")
        self._force_poll = True

    @workflow.signal
    def record_watering(self, watered_at_iso: Optional[str] = None) -> None:
        """
        Manually record that the plant was watered.

        watered_at_iso — optional ISO-8601 datetime string for the watering time.
        If omitted, defaults to the current workflow time (i.e. right now).

        For sensor plants this is normally auto-detected from a moisture spike,
        but this signal also works as a manual override or backfill.
        Clears any 'watering_overdue' warning immediately and updates status.
        """
        if watered_at_iso:
            try:
                self._last_watered_at = datetime.fromisoformat(watered_at_iso)
                workflow.logger.info(
                    f"[{self._name}] Watering recorded for {watered_at_iso}"
                )
            except (ValueError, TypeError):
                workflow.logger.warning(
                    f"[{self._name}] Invalid watered_at_iso {watered_at_iso!r} — using now"
                )
                self._last_watered_at = workflow.now()
        else:
            self._last_watered_at = workflow.now()
            workflow.logger.info(f"[{self._name}] Watering recorded (now)")

        # Trigger an immediate loop iteration — _poll_sensor() and
        # _check_watering_overdue() will both run, evaluating the new timestamp.
        # This is the same mechanism as associate_device and refresh_readings:
        # all three paths converge on _force_poll = True so behavior is consistent.
        self._force_poll = True

    @workflow.update
    def set_plant_status(self, status: str) -> None:
        """
        Change the plant's lifecycle status.

        Converted from signal → update so that unknown status values are
        rejected synchronously to the caller instead of being silently dropped.
        If the new status is a terminal status (e.g. 'dead', 'given_away'),
        the workflow will exit cleanly after this update is processed.
        """
        new_status = PlantStatus(status)  # already validated by the validator below
        workflow.logger.info(
            f"[{self._name}] Status changed to {new_status.value!r}"
        )
        self._status = new_status

        if new_status in TERMINAL_STATUSES:
            workflow.logger.info(
                f"[{self._name}] Terminal status set — workflow will exit cleanly"
            )
            self._stop_requested = True

    @set_plant_status.validator
    def validate_set_plant_status(self, status: str) -> None:
        """Reject unknown status strings before any state mutation."""
        try:
            PlantStatus(status)
        except ValueError:
            valid = ", ".join(f"'{s.value}'" for s in PlantStatus)
            raise ValueError(
                f"Unknown plant status {status!r}. Valid values: {valid}"
            )

    @workflow.signal
    def set_room(self, room: Optional[str]) -> None:
        """Move the plant to a room, or clear its room assignment (room=None)."""
        workflow.logger.info(
            f"[{self._name}] Room set to {room!r}"
        )
        self._room = room

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
            room=self._room,
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
            last_watered_at=self._last_watered_at,
            # Last-error fields — desired-vs-applied pattern
            last_association_error=self._last_association_error,
            last_sensor_read_error=self._last_sensor_read_error,
            last_care_ranges_fetch_error=self._last_care_ranges_fetch_error,
            last_alert_error=self._last_alert_error,
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
        # Phase 2: Hourly polling loop
        # All plants enter the loop immediately — sensorless plants skip the
        # sensor read but still receive watering-overdue checks.
        # -------------------------------------------------------------------
        workflow.logger.info(
            f"[{self._name}] Starting hourly polling loop"
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

            # Poll the sensor (no-op if no sensor is associated)
            await self._poll_sensor()

            # For sensorless plants: check if watering is overdue
            self._check_watering_overdue()

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
        """Try OpenPlantbook first; fall back to GPT-4o if not found.

        If both sources fail after their retry budgets are exhausted, the error
        is stored in last_care_ranges_fetch_error and the workflow continues
        (waiting for a sensor association) rather than crashing.  The user can
        manually set care ranges from the UI once the workflow is running.
        """
        workflow.logger.info(
            f"[{self._name}] Fetching care ranges for species: {self._species!r}"
        )

        retry_policy = RetryPolicy(
            initial_interval=timedelta(seconds=5),
            maximum_interval=timedelta(minutes=2),
            maximum_attempts=5,
        )

        try:
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
                if ai_ranges.watering_interval_reasoning:
                    self._care_ranges_reasoning["watering_interval_reasoning"] = (
                        ai_ranges.watering_interval_reasoning
                    )
                # AI returns temperatures in Celsius — convert to Fahrenheit
                self._care_ranges = _convert_care_ranges_temp_to_f(ai_ranges)
                self._care_ranges_source = "ai"

            # Success — clear any previous fetch error
            self._last_care_ranges_fetch_error = None
            self._ranges_already_fetched = True
            workflow.logger.info(
                f"[{self._name}] Care ranges ready (source: {self._care_ranges_source})"
            )

        except Exception as e:
            err = f"Failed to fetch care ranges for {self._species!r}: {e}"
            workflow.logger.error(f"[{self._name}] {err}")
            self._last_care_ranges_fetch_error = err
            # Mark as fetched so we don't retry on the next continue-as-new — the
            # user must set ranges manually via update_care_ranges.
            self._ranges_already_fetched = True

    async def _poll_sensor(self) -> None:
        """Read sensor, compare to care ranges, trigger alerts if needed.

        Also auto-detects watering events: if the previous soil moisture reading
        was below soil_moisture_min AND the new reading jumped by at least
        WATERING_SPIKE_THRESHOLD percentage points, records a watering event.
        """
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
            # Successful read — clear any previous sensor read error
            self._last_sensor_read_error = None
        except Exception as e:
            err = (
                f"Sensor read failed for device={self._sensor_device_id!r} "
                f"entity={self._sensor_entity_id!r}: {e}"
            )
            workflow.logger.error(f"[{self._name}] {err}")
            self._last_sensor_read_error = err
            return

        # -------------------------------------------------------------------
        # Auto-detect watering from a significant moisture spike.
        # Condition: previous reading was below soil_moisture_min AND the
        # new reading jumped by >= WATERING_SPIKE_THRESHOLD percentage points.
        # -------------------------------------------------------------------
        prev_moisture = (
            self._last_readings.soil_moisture
            if self._last_readings is not None
            else None
        )
        new_moisture = readings.soil_moisture

        if (
            prev_moisture is not None
            and new_moisture is not None
            and prev_moisture < self._care_ranges.soil_moisture_min
            and (new_moisture - prev_moisture) >= WATERING_SPIKE_THRESHOLD
        ):
            workflow.logger.info(
                f"[{self._name}] Watering detected: moisture jumped "
                f"{prev_moisture:.1f}% → {new_moisture:.1f}% "
                f"(spike: {new_moisture - prev_moisture:.1f}pp)"
            )
            self._last_watered_at = workflow.now()

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
            try:
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
                self._last_alert_error = None
            except Exception as e:
                err = f"Failed to trigger HA alert for {alert_entity!r}: {e}"
                workflow.logger.error(f"[{self._name}] {err}")
                self._last_alert_error = err
        else:
            self._status = PlantStatus.OK
            # If we just cleared a warning, turn the light green
            if previous_issues:
                workflow.logger.info(
                    f"[{self._name}] All metrics back in range — clearing alert"
                )
                try:
                    await workflow.execute_activity(
                        clear_ha_alert_light,
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=RetryPolicy(maximum_attempts=3),
                    )
                    self._last_alert_error = None
                except Exception as e:
                    err = f"Failed to clear HA alert light: {e}"
                    workflow.logger.error(f"[{self._name}] {err}")
                    self._last_alert_error = err

    def _check_watering_overdue(self) -> None:
        """
        For plants without a sensor: check if watering is overdue based on
        last_watered_at and care_ranges.watering_interval_days.

        Sets status to WARNING and adds "watering_overdue" to out_of_range_fields
        when the interval has been exceeded; clears both when watered.

        No-op when:
          - The plant has a sensor (sensor tracks moisture directly)
          - watering_interval_days is not configured
          - last_watered_at has never been recorded
        """
        # Sensor plants manage their own status via _poll_sensor
        if self._has_sensor():
            return

        interval = (
            self._care_ranges.watering_interval_days
            if self._care_ranges is not None
            else None
        )

        if interval is None or self._last_watered_at is None:
            # Can't evaluate — ensure "watering_overdue" is absent
            if "watering_overdue" in self._out_of_range_fields:
                self._out_of_range_fields = [
                    f for f in self._out_of_range_fields if f != "watering_overdue"
                ]
            return

        days_since_watered = (
            (workflow.now() - self._last_watered_at).total_seconds() / 86400
        )

        if days_since_watered > interval:
            if "watering_overdue" not in self._out_of_range_fields:
                self._out_of_range_fields = self._out_of_range_fields + ["watering_overdue"]
                workflow.logger.warning(
                    f"[{self._name}] Watering overdue: "
                    f"{days_since_watered:.1f} days since last watered "
                    f"(interval: {interval} days)"
                )
            if self._status not in TERMINAL_STATUSES:
                self._status = PlantStatus.WARNING
        else:
            if "watering_overdue" in self._out_of_range_fields:
                self._out_of_range_fields = [
                    f for f in self._out_of_range_fields if f != "watering_overdue"
                ]
                workflow.logger.info(f"[{self._name}] Watering is up to date")
            # Move to OK from any non-terminal status when no issues remain.
            # This covers the transition from UNKNOWN → OK when a sensorless
            # plant has a configured interval and a recent watering recorded.
            if not self._out_of_range_fields and self._status not in TERMINAL_STATUSES:
                self._status = PlantStatus.OK

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
            last_watered_at=(
                self._last_watered_at.isoformat() if self._last_watered_at else None
            ),
            ranges_already_fetched=True,
            # Persist last-error state across continue-as-new
            last_association_error=self._last_association_error,
            last_sensor_read_error=self._last_sensor_read_error,
            last_care_ranges_fetch_error=self._last_care_ranges_fetch_error,
            last_alert_error=self._last_alert_error,
            # Room assignment
            room=self._room,
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
