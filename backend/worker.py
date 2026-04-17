"""
Temporal worker — registers all workflows and activities, then runs indefinitely.

Start with:
    uv run python worker.py
"""

import asyncio
import concurrent.futures
import logging

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from activities.home_assistant import (
    clear_ha_alert_light,
    get_sensor_readings,
    get_zigbee_plant_sensors,
    trigger_ha_alert,
)
from activities.llm import get_care_ranges_from_ai
from activities.openplantbook import search_openplantbook
from models.config import settings
from workflows.plant_workflow import PlantWorkflow

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    logger.info(f"Connecting to Temporal at {settings.temporal_host} ...")
    client = await Client.connect(
        settings.temporal_host,
        namespace=settings.temporal_namespace,
        data_converter=pydantic_data_converter,
    )
    logger.info("Connected.")

    # Sync activities run in a thread pool; async activities run on the event loop.
    # search_openplantbook, get_sensor_readings, get_zigbee_plant_sensors,
    # trigger_ha_alert, and clear_ha_alert_light are all sync (use httpx sync).
    # get_care_ranges_from_ai is async (uses AsyncOpenAI).
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as activity_executor:
        worker = Worker(
            client,
            task_queue=settings.temporal_task_queue,
            workflows=[PlantWorkflow],
            activities=[
                # OpenPlantbook (sync)
                search_openplantbook,
                # LLM / OpenAI (async)
                get_care_ranges_from_ai,
                # Home Assistant (sync)
                get_zigbee_plant_sensors,
                get_sensor_readings,
                trigger_ha_alert,
                clear_ha_alert_light,
            ],
            activity_executor=activity_executor,
        )

        logger.info(
            f"Worker started on task queue: {settings.temporal_task_queue!r}"
        )
        await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
