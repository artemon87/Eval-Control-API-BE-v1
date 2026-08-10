from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Eval Control API"
    app_env: str = "local"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_database: str = "bryte-evals"
    mongodb_max_pool_size: int = Field(default=100, ge=1, le=1000)
    mongodb_min_pool_size: int = Field(default=5, ge=0, le=100)
    mongodb_server_selection_timeout_ms: int = Field(default=5000, ge=500, le=30000)

    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:5173"]
    )

    unit_runs_collection: str = "unit_eval_runs"
    unit_cases_collection: str = "unit_eval_cases"
    e2e_runs_collection: str = "e2e_eval_runs"
    e2e_cases_collection: str = "e2e_eval_cases"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
