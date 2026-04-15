from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "qa-validator"
    environment: str = "production"
    log_level: str = "INFO"
    admin_api_key: str = Field(..., alias="ADMIN_API_KEY")

    database_url: str = Field(..., alias="DATABASE_URL")

    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-haiku-4-5-20251001", alias="ANTHROPIC_MODEL")

    max_concurrency: int = 150
    batch_flush_size: int = 100
    checkpoint_batch_size: int = 100
    http_timeout_seconds: float = 20.0
    http_connect_timeout_seconds: float = 8.0
    http_read_timeout_seconds: float = 15.0
    http_max_retries: int = 2
    user_agent: str = "MenuIQ-QA-Validator/1.0"

    default_sample_size: int = 500
    scheduler_enabled: bool = True
    scheduler_cron_hour_utc: int = 3
    scheduler_cron_minute_utc: int = 0

    freshness_pass_threshold: float = 0.70
    freshness_ambiguous_low: float = 0.40
    freshness_ambiguous_high: float = 0.70

    venue_name_pass_threshold: float = 0.75
    menu_validity_ambiguous_low: float = 0.30
    menu_validity_ambiguous_high: float = 0.70

    advisory_lock_key: int = 84127319
    pipeline_name: str = "qa_validation_service"

    @property
    def composite_weights(self) -> dict[str, float]:
        return {
            "menu_validity": 0.35,
            "venue_accuracy": 0.30,
            "freshness": 0.20,
            "beverage_relevance": 0.15,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
