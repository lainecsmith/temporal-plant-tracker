"""
Shared pytest fixtures for the plant-tracker backend test suite.
"""
from __future__ import annotations

import pytest
import httpx

from models.config import settings


@pytest.fixture(scope="session")
async def ha_reachable() -> bool:
    """
    Session-scoped fixture: probe Home Assistant once and cache the result.

    Returns True if HA responds (even with 401 — that still means it's up),
    False if the host is unreachable.  Tests that need a live HA instance
    should receive this fixture and call pytest.skip() when it is False.
    """
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"{settings.ha_url.rstrip('/')}/api/",
                headers={"Authorization": f"Bearer {settings.ha_token}"},
            )
        # 200 = authenticated, 401 = wrong token but HA is up — both count as reachable
        return resp.status_code in (200, 401)
    except httpx.RequestError:
        return False
