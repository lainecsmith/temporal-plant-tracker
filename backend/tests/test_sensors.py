"""
Integration tests for GET /sensors.

These tests call the *real* Home Assistant instance using credentials from
backend/.env — no mocking of the HA HTTP layer.

Run from the backend/ directory:
    uv run pytest tests/test_sensors.py -v

If Home Assistant is not reachable on the current network (e.g. in CI) the
tests are automatically skipped rather than failing.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.main import list_sensors
from models.plant import HASensor


# ---------------------------------------------------------------------------
# Test 1 — call the route handler directly (no Temporal client needed)
# ---------------------------------------------------------------------------

async def test_list_sensors_direct(ha_reachable: bool):
    """
    Await the route handler function directly.

    Confirms that the function:
    - Successfully reaches the real Home Assistant instance
    - Returns a plain Python list
    - Every item is a valid HASensor with a non-empty entity_id / friendly_name
      and a proper '<domain>.<object_id>' shape
    """
    if not ha_reachable:
        pytest.skip("Home Assistant not reachable — skipping live integration test")

    result = await list_sensors()

    assert isinstance(result, list), "Expected list[HASensor]"

    for sensor in result:
        assert isinstance(sensor, HASensor), f"Got non-HASensor item: {sensor!r}"
        assert sensor.entity_id, "entity_id must not be empty"
        assert sensor.friendly_name, "friendly_name must not be empty"
        assert "." in sensor.entity_id, (
            f"entity_id should be a HA entity id like 'sensor.foo' (got {sensor.entity_id!r})"
        )
        domain = sensor.entity_id.split(".")[0]
        assert domain in ("sensor", "plant"), (
            f"Unexpected entity domain {domain!r} — endpoint should only return "
            "sensor.* or plant.* entities"
        )


# ---------------------------------------------------------------------------
# Test 2 — full HTTP round-trip through FastAPI
# ---------------------------------------------------------------------------

async def test_list_sensors_http_200(ha_reachable: bool):
    """
    Mount only the /sensors route on a minimal FastAPI app (no Temporal
    lifespan) and verify the full HTTP response.

    Confirms:
    - HTTP 200 status
    - Response body is a JSON array
    - Each item has the expected keys
    """
    if not ha_reachable:
        pytest.skip("Home Assistant not reachable — skipping live integration test")

    @asynccontextmanager
    async def _noop_lifespan(app: FastAPI):
        yield  # skip Temporal startup — not needed for /sensors

    slim_app = FastAPI(lifespan=_noop_lifespan)
    slim_app.add_api_route("/sensors", list_sensors, methods=["GET"])

    async with AsyncClient(
        transport=ASGITransport(app=slim_app), base_url="http://test"
    ) as client:
        response = await client.get("/sensors")

    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}: {response.text}"
    )

    payload = response.json()
    assert isinstance(payload, list), f"Expected JSON array, got: {type(payload)}"

    for item in payload:
        assert "entity_id" in item, f"Missing 'entity_id' in {item}"
        assert "friendly_name" in item, f"Missing 'friendly_name' in {item}"
        # 'state' key should be present (value may be None)
        assert "state" in item, f"Missing 'state' key in {item}"
