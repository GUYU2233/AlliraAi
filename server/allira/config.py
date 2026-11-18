"""Configuration is owned by the server, never supplied by the chat client."""

from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ALLIRA_", env_file=".env", extra="ignore")

    environment: Literal["development", "production"] = "development"
    database_path: str = "allira.dev.sqlite3"
    allow_dev_auth: bool = False
    cors_origins: list[str] = Field(default_factory=list)
    session_ttl_seconds: int = Field(default=86400, ge=60, le=604800)
    provider: Literal["mock", "openai"] = "mock"
    upstream_base_url: str = "https://api.openai.com/v1"
    upstream_model: str = ""
    upstream_api_key: str | None = Field(default=None, repr=False)
    run_timeout_seconds: float = Field(default=180, gt=0, le=1800)
    max_prompt_characters: int = Field(default=120000, ge=1000)
    max_output_characters: int = Field(default=60000, ge=1, le=1000000)

    @model_validator(mode="after")
    def validate_settings(self) -> "Settings":
        if self.environment == "production" and self.allow_dev_auth:
            raise ValueError("Development authentication must be disabled in production.")
        if self.provider == "openai" and not self.upstream_model:
            raise ValueError("ALLIRA_UPSTREAM_MODEL is required for the openai provider.")
        if "*" in self.cors_origins:
            raise ValueError("CORS requires explicit origins; wildcard is not supported.")
        return self
