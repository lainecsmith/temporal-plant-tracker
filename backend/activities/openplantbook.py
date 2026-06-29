"""
Activities for looking up plant care information from OpenPlantbook.io.

OpenPlantbook provides a free API with care data for thousands of plant species.
API docs: https://open.plantbook.io/docs/
"""

import re
from typing import Optional

import httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError

from models.config import settings
from models.plant import CareRanges


# ---------------------------------------------------------------------------
# Auth token management
# ---------------------------------------------------------------------------

_token_cache: dict = {}


def _get_auth_headers() -> dict[str, str]:
    """Return auth headers for OpenPlantbook API. Caches the token in memory."""
    # In activities, we re-fetch the token if it's not cached.
    # Since activities run in threads, this is safe.
    if "access_token" in _token_cache:
        return {"Authorization": f"Bearer {_token_cache['access_token']}"}

    if not settings.openplantbook_client_id or not settings.openplantbook_client_secret:
        raise ApplicationError(
            "OpenPlantbook credentials not configured",
            type="ConfigurationError",
            non_retryable=True,
        )

    response = httpx.post(
        "https://open.plantbook.io/api/v1/token/",
        data={
            "grant_type": "client_credentials",
            "client_id": settings.openplantbook_client_id,
            "client_secret": settings.openplantbook_client_secret,
        },
        timeout=15,
    )

    if response.status_code == 401:
        raise ApplicationError(
            "Invalid OpenPlantbook credentials",
            type="AuthenticationError",
            non_retryable=True,
        )
    response.raise_for_status()

    data = response.json()
    _token_cache["access_token"] = data["access_token"]
    return {"Authorization": f"Bearer {data['access_token']}"}


# ---------------------------------------------------------------------------
# Watering text parser
# ---------------------------------------------------------------------------

def _parse_watering_interval_days(text: str) -> Optional[float]:
    """
    Attempt to extract a numeric watering interval (in days) from a free-text
    care description returned by the OpenPlantbook 'watering' field.

    Handles common patterns like:
      "Water every 7-10 days"       → 8.5
      "every week"                  → 7.0
      "twice a week"                → 3.5
      "once a week"                 → 7.0
      "every 2 weeks"               → 14.0
      "every 10 days"               → 10.0
      "Water every 1-2 weeks"       → 10.5
    Returns None if no recognisable pattern is found.
    """
    if not text:
        return None

    t = text.lower()

    # "every X-Y days" → average of range
    m = re.search(r'every\s+(\d+)\s*[-–to]+\s*(\d+)\s*day', t)
    if m:
        return (float(m.group(1)) + float(m.group(2))) / 2

    # "every X days"
    m = re.search(r'every\s+(\d+)\s*day', t)
    if m:
        return float(m.group(1))

    # "every X-Y weeks" → average of range, in days
    m = re.search(r'every\s+(\d+)\s*[-–to]+\s*(\d+)\s*week', t)
    if m:
        return ((float(m.group(1)) + float(m.group(2))) / 2) * 7

    # "every X weeks"
    m = re.search(r'every\s+(\d+)\s*week', t)
    if m:
        return float(m.group(1)) * 7

    # "X-Y days" (without "every")
    m = re.search(r'(\d+)\s*[-–]\s*(\d+)\s*day', t)
    if m:
        return (float(m.group(1)) + float(m.group(2))) / 2

    # "twice a week" → ~3.5 days
    if re.search(r'twice\s+a\s+week', t):
        return 3.5

    # "once a week" / "every week" / "weekly"
    if re.search(r'once\s+a\s+week', t) or re.search(r'every\s+week\b', t) or 'weekly' in t:
        return 7.0

    # "biweekly" → every 2 weeks
    if 'biweekly' in t or 'bi-weekly' in t or 'bi weekly' in t:
        return 14.0

    # "monthly"
    if 'monthly' in t or 'once a month' in t:
        return 30.0

    return None


# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------

@activity.defn
def search_openplantbook(species: str) -> CareRanges | None:
    """
    Search OpenPlantbook for care ranges for a given plant species.

    Returns a CareRanges object if found, or None if the plant is not in the
    database (caller should fall back to AI).
    """
    activity.logger.info(f"Searching OpenPlantbook for species: {species!r}")

    try:
        headers = _get_auth_headers()
    except ApplicationError:
        raise

    # Step 1: search for the plant to get its pid (plant ID)
    try:
        search_resp = httpx.get(
            "https://open.plantbook.io/api/v1/plant/search",
            params={"alias": species, "limit": 5},
            headers=headers,
            timeout=15,
        )
    except httpx.RequestError as e:
        raise ApplicationError(
            f"Network error contacting OpenPlantbook: {e}",
            type="NetworkError",
        )

    if search_resp.status_code == 401:
        # Token may have expired — clear cache and let Temporal retry
        _token_cache.clear()
        raise ApplicationError(
            "OpenPlantbook token expired, will retry",
            type="TokenExpired",
        )

    if search_resp.status_code != 200:
        raise ApplicationError(
            f"OpenPlantbook search failed: {search_resp.status_code}",
            type="APIError",
        )

    results = search_resp.json().get("results", [])
    if not results:
        activity.logger.info(f"No results found for species: {species!r}")
        return None

    pid = results[0].get("pid")
    if not pid:
        return None

    # Step 2: fetch detailed care info for that pid, requesting the care text block
    try:
        detail_resp = httpx.get(
            f"https://open.plantbook.io/api/v1/plant/detail/{pid}/",
            params={"include": "care"},
            headers=headers,
            timeout=15,
        )
    except httpx.RequestError as e:
        raise ApplicationError(
            f"Network error fetching plant detail: {e}",
            type="NetworkError",
        )

    if detail_resp.status_code != 200:
        activity.logger.warning(
            f"Could not fetch details for pid {pid}: {detail_resp.status_code}"
        )
        return None

    data = detail_resp.json()
    activity.logger.info(f"Found plant data for pid={pid}: {data.get('display_pid')}")
    activity.logger.debug(f"Raw OpenPlantbook response for {pid}: {data}")

    # Extract watering free-text and attempt to parse an interval in days.
    # OpenPlantbook returns a top-level "watering" key with a prose description.
    watering_text: Optional[str] = data.get("watering")
    watering_interval_days: Optional[float] = None
    if watering_text:
        activity.logger.info(f"OpenPlantbook watering text for {pid!r}: {watering_text!r}")
        watering_interval_days = _parse_watering_interval_days(watering_text)
        if watering_interval_days is not None:
            activity.logger.info(
                f"Parsed watering interval for {pid!r}: {watering_interval_days} days"
            )
        else:
            activity.logger.info(
                f"Could not parse a numeric interval from watering text: {watering_text!r}"
            )

    # Map OpenPlantbook fields to our CareRanges model.
    # Fields: min_soil_moist, max_soil_moist, min_temp, max_temp,
    #         min_env_humid, max_env_humid, min_light_lux, max_light_lux
    # Note: temperature values are in Celsius — the workflow converts them to °F.
    try:
        return CareRanges(
            soil_moisture_min=float(data.get("min_soil_moist", 20)),
            soil_moisture_max=float(data.get("max_soil_moist", 60)),
            temperature_min=float(data.get("min_temp", 15)),
            temperature_max=float(data.get("max_temp", 30)),
            air_humidity_min=float(data.get("min_env_humid", 30)),
            air_humidity_max=float(data.get("max_env_humid", 80)),
            light_lux_min=float(data["min_light_lux"]) if data.get("min_light_lux") else None,
            light_lux_max=float(data["max_light_lux"]) if data.get("max_light_lux") else None,
            watering_interval_days=watering_interval_days,
        )
    except (KeyError, TypeError, ValueError) as e:
        activity.logger.warning(f"Failed to parse care ranges from OpenPlantbook: {e}")
        return None
