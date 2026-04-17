"""
Activities for integrating with Home Assistant.

Covers:
  - Listing available Zigbee plant sensors
  - Reading current sensor values
  - Sending out-of-range alerts (notification + indicator light)
"""

from datetime import datetime
from typing import Optional

import httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError

from models.config import settings
from models.plant import CareRanges, HASensor, SensorReadings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ha_headers() -> dict[str, str]:
    if not settings.ha_token:
        raise ApplicationError(
            "Home Assistant token not configured",
            type="ConfigurationError",
            non_retryable=True,
        )
    return {
        "Authorization": f"Bearer {settings.ha_token}",
        "Content-Type": "application/json",
    }


def _ha_get(path: str, timeout: int = 15) -> dict | list:
    url = f"{settings.ha_url.rstrip('/')}{path}"
    try:
        resp = httpx.get(url, headers=_ha_headers(), timeout=timeout)
    except httpx.RequestError as e:
        raise ApplicationError(f"Network error reaching Home Assistant: {e}", type="NetworkError")

    if resp.status_code == 401:
        raise ApplicationError(
            "Home Assistant token is invalid or expired",
            type="AuthenticationError",
            non_retryable=True,
        )
    if resp.status_code != 200:
        raise ApplicationError(
            f"Home Assistant API error {resp.status_code}: {resp.text}",
            type="APIError",
        )
    return resp.json()


def _ha_post(path: str, payload: dict, timeout: int = 15) -> dict:
    url = f"{settings.ha_url.rstrip('/')}{path}"
    try:
        resp = httpx.post(url, headers=_ha_headers(), json=payload, timeout=timeout)
    except httpx.RequestError as e:
        raise ApplicationError(f"Network error reaching Home Assistant: {e}", type="NetworkError")

    if resp.status_code == 401:
        raise ApplicationError(
            "Home Assistant token is invalid or expired",
            type="AuthenticationError",
            non_retryable=True,
        )
    if resp.status_code not in (200, 201):
        raise ApplicationError(
            f"Home Assistant API error {resp.status_code}: {resp.text}",
            type="APIError",
        )
    return resp.json() if resp.content else {}


# ---------------------------------------------------------------------------
# Activity: list available Zigbee plant sensors
# ---------------------------------------------------------------------------

# Device classes and integration patterns used by common plant probes:
#   - MiFlora / HHCC Plant Joy report entities with device_class in
#     ["moisture", "temperature", "humidity", "illuminance"]
#   - The parent sensor entity often contains "miflora", "plantbook",
#     "plant_probe", or "hhcc" in its entity_id or platform.
# We return any entity whose entity_id or friendly_name suggests it is
# a plant sensor, letting the user pick the right one.
_PLANT_SENSOR_KEYWORDS = [
    "miflora", "plant", "flora", "hhcc", "parrot", "flower_care",
    "flower_power", "smart_plant",
]


@activity.defn
def get_zigbee_plant_sensors() -> list[HASensor]:
    """
    Return a list of sensor entities in Home Assistant that look like
    Zigbee / BLE plant probes, so the user can pick one to associate with a plant.
    """
    activity.logger.info("Fetching entity list from Home Assistant")

    all_states: list[dict] = _ha_get("/api/states")  # type: ignore[assignment]

    sensors: list[HASensor] = []
    seen_friendly: set[str] = set()

    for entity in all_states:
        entity_id: str = entity.get("entity_id", "")
        attributes: dict = entity.get("attributes", {})
        friendly_name: str = attributes.get("friendly_name", entity_id)
        domain = entity_id.split(".")[0] if "." in entity_id else ""

        if domain not in ("sensor", "plant"):
            continue

        # Include entities that look like plant sensors based on keywords
        lower_id = entity_id.lower()
        lower_name = friendly_name.lower()
        is_plant_sensor = any(
            kw in lower_id or kw in lower_name for kw in _PLANT_SENSOR_KEYWORDS
        )
        if not is_plant_sensor:
            continue

        # Deduplicate by friendly name to avoid showing every sub-sensor
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

    activity.logger.info(f"Found {len(sensors)} candidate plant sensor(s)")
    return sensors


# ---------------------------------------------------------------------------
# Activity: read current sensor values for an associated plant probe
# ---------------------------------------------------------------------------

# Attribute name mappings for common plant sensor integrations.
# Some sensors report sub-entities; others pack everything into attributes.
_MOISTURE_ATTRS = ["moisture", "soil_moisture"]
_TEMP_ATTRS = ["temperature"]
_HUMIDITY_ATTRS = ["humidity", "air_humidity"]
_LIGHT_ATTRS = ["illuminance", "light_lux", "lux", "brightness"]

# Maps device_class values (from HA entity registry) to reading fields
_DEVICE_CLASS_TO_FIELD = {
    "moisture": "moisture",
    "temperature": "temperature",
    "humidity": "humidity",
    "illuminance": "illuminance",
}


def _safe_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@activity.defn
def get_sensor_readings(sensor_input: "str | dict[str, str]") -> SensorReadings:
    """
    Read the current sensor values for a Zigbee plant probe.

    Accepts two input formats:
    1. A dict mapping device_class → entity_id (preferred, from device association).
       e.g. {"temperature": "sensor.spike_temperature", "humidity": "sensor.spike_humidity"}
    2. A plain entity_id string (legacy fallback). In this case we look at the
       entity's own attributes and also scan for companion sensor entities by
       matching the base name prefix.
    """
    all_states: list[dict] = _ha_get("/api/states")  # type: ignore[assignment]
    state_map: dict[str, dict] = {s["entity_id"]: s for s in all_states}

    moisture: Optional[float] = None
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    light_lux: Optional[float] = None

    # ------------------------------------------------------------------
    # Path 1: dict of device_class -> entity_id (device-level association)
    # ------------------------------------------------------------------
    if isinstance(sensor_input, dict):
        activity.logger.info(
            f"Reading sensor data for device entities: {list(sensor_input.keys())}"
        )
        for device_class, eid in sensor_input.items():
            entity_state = state_map.get(eid)
            if entity_state is None:
                activity.logger.warning(f"Entity {eid!r} not found in HA states — skipping")
                continue
            val = _safe_float(entity_state.get("state"))
            if val is None:
                continue
            dc = device_class.lower()
            if dc in ("moisture", "soil_moisture"):
                moisture = val
            elif dc == "temperature":
                temperature = val
            elif dc in ("humidity", "air_humidity"):
                humidity = val
            elif dc in ("illuminance", "light_lux", "lux"):
                light_lux = val

    # ------------------------------------------------------------------
    # Path 2: legacy single entity_id string
    # ------------------------------------------------------------------
    else:
        entity_id: str = sensor_input
        activity.logger.info(f"Reading sensor data for entity: {entity_id}")

        primary = state_map.get(entity_id)
        if primary is None:
            raise ApplicationError(
                f"Entity {entity_id!r} not found in Home Assistant",
                type="EntityNotFound",
                non_retryable=True,
            )

        attrs = primary.get("attributes", {})

        # Try reading from primary entity attributes first
        moisture = _safe_float(attrs.get("moisture") or attrs.get("soil_moisture"))
        temperature = _safe_float(attrs.get("temperature"))
        humidity = _safe_float(attrs.get("humidity") or attrs.get("air_humidity"))
        light_lux = _safe_float(attrs.get("illuminance") or attrs.get("lux"))

        # Also scan for companion sensor.* entities that share the same base name.
        # e.g. "plant.miflora_desk" → look for "sensor.miflora_desk_moisture" etc.
        base_name = entity_id.split(".", 1)[-1]  # strip domain prefix

        for sid, sdata in state_map.items():
            if not sid.startswith("sensor."):
                continue
            sid_lower = sid.lower()
            # Match entities whose ID starts with the same base prefix
            # (handles e.g. sensor.miflora_desk_moisture, sensor.miflora_desk_temperature)
            base_prefix = base_name.lower().rsplit("_", 1)[0] if "_" in base_name else base_name.lower()
            if not sid_lower.startswith(f"sensor.{base_prefix}"):
                continue

            val = _safe_float(sdata.get("state"))
            if val is None:
                continue

            if moisture is None and any(kw in sid_lower for kw in _MOISTURE_ATTRS):
                moisture = val
            if temperature is None and any(kw in sid_lower for kw in _TEMP_ATTRS):
                temperature = val
            if humidity is None and any(kw in sid_lower for kw in _HUMIDITY_ATTRS):
                humidity = val
            if light_lux is None and any(kw in sid_lower for kw in _LIGHT_ATTRS):
                light_lux = val

    readings = SensorReadings(
        soil_moisture=moisture,
        temperature=temperature,
        air_humidity=humidity,
        light_lux=light_lux,
        timestamp=datetime.utcnow(),
    )

    activity.logger.info(
        f"Readings: moisture={moisture}%, temp={temperature}°F, "
        f"humidity={humidity}%, lux={light_lux}"
    )
    return readings


# ---------------------------------------------------------------------------
# Activity: trigger alert in Home Assistant
# ---------------------------------------------------------------------------

@activity.defn
def trigger_ha_alert(
    plant_name: str,
    sensor_entity_id: str,
    out_of_range_fields: list[str],
    readings: SensorReadings,
    care_ranges: CareRanges,
) -> None:
    """
    Notify Home Assistant when a plant's readings are outside acceptable ranges.

    Sends:
    1. A notification via the configured HA notification service.
    2. Turns the indicator light red (if configured).
    """
    if not out_of_range_fields:
        return

    # Build a human-readable message
    issues: list[str] = []
    for field in out_of_range_fields:
        if field == "soil_moisture" and readings.soil_moisture is not None:
            issues.append(
                f"Soil moisture: {readings.soil_moisture:.1f}% "
                f"(ideal: {care_ranges.soil_moisture_min}-{care_ranges.soil_moisture_max}%)"
            )
        elif field == "temperature" and readings.temperature is not None:
            issues.append(
                f"Temperature: {readings.temperature:.1f}°F "
                f"(ideal: {care_ranges.temperature_min:.1f}-{care_ranges.temperature_max:.1f}°F)"
            )
        elif field == "air_humidity" and readings.air_humidity is not None:
            issues.append(
                f"Air humidity: {readings.air_humidity:.1f}% "
                f"(ideal: {care_ranges.air_humidity_min}-{care_ranges.air_humidity_max}%)"
            )
        elif field == "light_lux" and readings.light_lux is not None:
            issues.append(
                f"Light: {readings.light_lux:.0f} lux "
                f"(ideal: {care_ranges.light_lux_min}-{care_ranges.light_lux_max} lux)"
            )

    message = f"🌿 {plant_name} needs attention!\n" + "\n".join(f"• {i}" for i in issues)
    title = f"Plant Alert: {plant_name}"

    activity.logger.warning(f"Sending HA alert for {plant_name}: {out_of_range_fields}")

    # 1. Send notification
    service_domain, service_name = settings.ha_notification_service.split(".", 1)
    _ha_post(
        f"/api/services/{service_domain}/{service_name}",
        {"title": title, "message": message},
    )

    # 2. Change indicator light colour (red for warning, skip if not configured)
    light_entity = settings.ha_indicator_light_entity
    if light_entity and light_entity != "light.plant_indicator":
        # Light exists — turn it on red
        _ha_post(
            "/api/services/light/turn_on",
            {
                "entity_id": light_entity,
                "rgb_color": [255, 30, 0],   # red
                "brightness": 200,
            },
        )
        activity.logger.info(f"Set indicator light {light_entity} to red")


@activity.defn
def clear_ha_alert_light() -> None:
    """
    Turn the indicator light green when all metrics are back in range.
    """
    light_entity = settings.ha_indicator_light_entity
    if light_entity and light_entity != "light.plant_indicator":
        _ha_post(
            "/api/services/light/turn_on",
            {
                "entity_id": light_entity,
                "rgb_color": [0, 200, 50],   # green
                "brightness": 150,
            },
        )
        activity.logger.info(f"Set indicator light {light_entity} to green (all clear)")
