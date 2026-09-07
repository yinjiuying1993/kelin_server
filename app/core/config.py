from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["test", "dev", "prod"]


class Settings(BaseSettings):
    """Minimal boot settings for the empty shell. Full secret validation is P01-T06."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: AppEnv = "test"
    log_level: str = "INFO"
    api_version: str = "v1"

    @property
    def docs_enabled(self) -> bool:
        return self.app_env != "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
