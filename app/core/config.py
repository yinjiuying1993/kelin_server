from enum import StrEnum
from functools import lru_cache
from typing import Literal, Self

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["test", "dev", "prod"]


class ConfigPresence(StrEnum):
    SET = "set"
    UNSET = "unset"


class Settings(BaseSettings):
    """Boot settings. Secret values never appear in logs or dumps."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: AppEnv = "test"
    log_level: str = "INFO"
    api_version: str = "v1"
    schema_version: int = 2
    database_url_api: SecretStr | None = None

    @property
    def docs_enabled(self) -> bool:
        return self.app_env != "prod"

    @field_validator("database_url_api", mode="before")
    @classmethod
    def empty_database_url_to_none(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @model_validator(mode="after")
    def require_prod_database_url(self) -> Self:
        if self.app_env == "prod" and self.database_url_api is None:
            raise ValueError("DATABASE_URL_API is required when APP_ENV=prod")
        return self

    def config_presence(self) -> dict[str, str]:
        return {
            "app_env": self.app_env,
            "log_level": self.log_level,
            "api_version": self.api_version,
            "schema_version": str(self.schema_version),
            "database_url_api": (
                ConfigPresence.SET.value
                if self.database_url_api is not None
                else ConfigPresence.UNSET.value
            ),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
