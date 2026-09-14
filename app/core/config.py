import hashlib
from base64 import urlsafe_b64encode
from enum import StrEnum
from functools import lru_cache
from typing import Literal, Self

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["test", "dev", "prod"]
_DEV_DEVICE_TOKEN_FERNET_KEY = urlsafe_b64encode(hashlib.sha256(b"kelin-dev-device-token").digest())


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
    supabase_jwt_issuer: str | None = None
    supabase_jwt_audience: str | None = None
    supabase_jwks_url: str | None = None
    cursor_hmac_secret: SecretStr | None = None
    device_token_key: SecretStr | None = None
    bailian_api_key: SecretStr | None = None
    bailian_workspace_id: SecretStr | None = None
    bailian_app_id: str | None = None
    bailian_chat_model: str | None = None
    bailian_extract_model: str | None = None
    bailian_asr_model: str | None = None
    bailian_tts_model: str | None = None
    bailian_tts_voice: str | None = None
    bailian_vision_model: str | None = None
    bailian_safety_model: str | None = None
    bailian_search_model: str | None = None
    bailian_search_enabled: bool = False
    ai_daily_budget_micros: int = 10_000_000
    ai_budget_warning_percent: int = 70
    ai_budget_critical_percent: int = 100
    ai_cost_input_micros_per_unit: int = 3
    ai_cost_output_micros_per_unit: int = 15
    ai_cost_audio_micros_per_second: int = 1_000
    ai_cost_image_micros_per_image: int = 40_000
    outbox_lease_seconds: int = 120
    enable_debug_api_for_tests: bool = False
    debug_token: SecretStr | None = None
    debug_allowlist: str = ""

    @property
    def docs_enabled(self) -> bool:
        return self.app_env != "prod"

    @field_validator(
        "database_url_api",
        "supabase_jwt_issuer",
        "supabase_jwt_audience",
        "supabase_jwks_url",
        "cursor_hmac_secret",
        "device_token_key",
        "debug_token",
        "bailian_api_key",
        "bailian_workspace_id",
        "bailian_app_id",
        "bailian_chat_model",
        "bailian_extract_model",
        "bailian_asr_model",
        "bailian_tts_model",
        "bailian_tts_voice",
        "bailian_vision_model",
        "bailian_safety_model",
        "bailian_search_model",
        mode="before",
    )
    @classmethod
    def empty_optional_to_none(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @model_validator(mode="after")
    def require_prod_secrets_and_jwt(self) -> Self:
        if self.app_env != "prod":
            return self
        if self.enable_debug_api_for_tests:
            raise ValueError("ENABLE_DEBUG_API_FOR_TESTS must be false when APP_ENV=prod")
        if self.database_url_api is None:
            raise ValueError("DATABASE_URL_API is required when APP_ENV=prod")
        if (
            self.supabase_jwt_issuer is None
            or self.supabase_jwt_audience is None
            or self.supabase_jwks_url is None
        ):
            raise ValueError("Supabase JWT issuer, audience, and JWKS URL are required in prod")
        if self.cursor_hmac_secret is None:
            raise ValueError("CURSOR_HMAC_SECRET is required when APP_ENV=prod")
        if self.device_token_key is None:
            raise ValueError("DEVICE_TOKEN_KEY is required when APP_ENV=prod")
        return self

    @model_validator(mode="after")
    def require_budget_bounds(self) -> Self:
        if self.ai_daily_budget_micros <= 0:
            raise ValueError("AI_DAILY_BUDGET_MICROS must be positive")
        if not 1 <= self.ai_budget_warning_percent <= 100:
            raise ValueError("AI_BUDGET_WARNING_PERCENT must be 1..100")
        if not 1 <= self.ai_budget_critical_percent <= 100:
            raise ValueError("AI_BUDGET_CRITICAL_PERCENT must be 1..100")
        if self.ai_budget_warning_percent > self.ai_budget_critical_percent:
            raise ValueError("AI_BUDGET_WARNING_PERCENT must be <= AI_BUDGET_CRITICAL_PERCENT")
        for field, value in (
            ("AI_COST_INPUT_MICROS_PER_UNIT", self.ai_cost_input_micros_per_unit),
            ("AI_COST_OUTPUT_MICROS_PER_UNIT", self.ai_cost_output_micros_per_unit),
            ("AI_COST_AUDIO_MICROS_PER_SECOND", self.ai_cost_audio_micros_per_second),
            ("AI_COST_IMAGE_MICROS_PER_IMAGE", self.ai_cost_image_micros_per_image),
        ):
            if value < 0:
                raise ValueError(f"{field} must be >= 0")
        if self.outbox_lease_seconds < 1:
            raise ValueError("OUTBOX_LEASE_SECONDS must be >= 1")
        return self

    def sqlalchemy_async_url(self) -> str:
        if self.database_url_api is None:
            raise ValueError("DATABASE_URL_API is unset")
        raw = self.database_url_api.get_secret_value()
        if raw.startswith("postgresql+asyncpg://"):
            return raw
        if raw.startswith("postgresql://"):
            return f"postgresql+asyncpg://{raw.removeprefix('postgresql://')}"
        raise ValueError("DATABASE_URL_API must be a postgresql URL")

    def cursor_signing_key(self) -> bytes:
        if self.cursor_hmac_secret is not None:
            return self.cursor_hmac_secret.get_secret_value().encode("utf-8")
        if self.app_env == "prod":
            raise ValueError("CURSOR_HMAC_SECRET is required when APP_ENV=prod")
        return b"kelin-dev-cursor-hmac"

    def device_token_fernet_key(self) -> bytes:
        if self.device_token_key is not None:
            return self.device_token_key.get_secret_value().encode("ascii")
        if self.app_env == "prod":
            raise ValueError("DEVICE_TOKEN_KEY is required when APP_ENV=prod")
        return _DEV_DEVICE_TOKEN_FERNET_KEY

    def config_presence(self) -> dict[str, str]:
        return {
            "app_env": self.app_env,
            "log_level": self.log_level,
            "api_version": self.api_version,
            "schema_version": str(self.schema_version),
            "database_url_api": _presence(self.database_url_api),
            "supabase_jwt_issuer": _presence(self.supabase_jwt_issuer),
            "supabase_jwt_audience": _presence(self.supabase_jwt_audience),
            "supabase_jwks_url": _presence(self.supabase_jwks_url),
            "cursor_hmac_secret": _presence(self.cursor_hmac_secret),
            "device_token_key": _presence(self.device_token_key),
            "bailian_api_key": _presence(self.bailian_api_key),
            "bailian_workspace_id": _presence(self.bailian_workspace_id),
            "bailian_app_id": _presence(self.bailian_app_id),
            "bailian_chat_model": _presence(self.bailian_chat_model),
            "bailian_extract_model": _presence(self.bailian_extract_model),
            "bailian_asr_model": _presence(self.bailian_asr_model),
            "bailian_tts_model": _presence(self.bailian_tts_model),
            "bailian_tts_voice": _presence(self.bailian_tts_voice),
            "bailian_vision_model": _presence(self.bailian_vision_model),
            "bailian_safety_model": _presence(self.bailian_safety_model),
            "bailian_search_model": _presence(self.bailian_search_model),
            "bailian_search_enabled": "true" if self.bailian_search_enabled else "false",
            "ai_daily_budget_micros": str(self.ai_daily_budget_micros),
            "ai_budget_warning_percent": str(self.ai_budget_warning_percent),
            "ai_budget_critical_percent": str(self.ai_budget_critical_percent),
            "ai_cost_input_micros_per_unit": str(self.ai_cost_input_micros_per_unit),
            "ai_cost_output_micros_per_unit": str(self.ai_cost_output_micros_per_unit),
            "ai_cost_audio_micros_per_second": str(self.ai_cost_audio_micros_per_second),
            "ai_cost_image_micros_per_image": str(self.ai_cost_image_micros_per_image),
            "outbox_lease_seconds": str(self.outbox_lease_seconds),
            "enable_debug_api_for_tests": ("true" if self.enable_debug_api_for_tests else "false"),
            "debug_token": _presence(self.debug_token),
            "debug_allowlist": (
                ConfigPresence.SET.value
                if self.debug_allowlist.strip()
                else ConfigPresence.UNSET.value
            ),
        }


def _presence(value: object) -> str:
    return ConfigPresence.SET.value if value is not None else ConfigPresence.UNSET.value


@lru_cache
def get_settings() -> Settings:
    return Settings()
