from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env relative to this file (backend/models/config.py -> backend/.env)
_ENV_FILE = Path(__file__).parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), extra="ignore")

    # Temporal — sensible defaults are fine here
    temporal_host: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "plant-tracker"

    # OpenAI — required, no default so a missing value raises a clear error
    openai_api_key: str

    # OpenPlantbook — required
    openplantbook_client_id: str
    openplantbook_client_secret: str

    # Home Assistant — URL/entity/service have sensible defaults; token is required
    ha_url: str = "http://homeassistant.local:8123"
    ha_token: str
    ha_indicator_light_entity: str = "light.plant_indicator"
    ha_notification_service: str = "notify.persistent_notification"


settings = Settings()
