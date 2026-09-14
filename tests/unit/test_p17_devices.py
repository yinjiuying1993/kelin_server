"""P17-T01 devices contract, encryption, and environment isolation."""

from __future__ import annotations

from inspect import getsource
from pathlib import Path

from app.contracts.openapi import export_openapi
from app.core.config import Settings
from app.core.logging import redact_event_dict
from app.domain.devices import (
    decrypt_apns_token,
    encrypt_apns_token,
    environment_is_compatible,
    hash_apns_token,
    required_device_environment,
)
from app.main import _lifespan, create_app
from app.schemas.devices import DeviceRegistration, RegisterDeviceRequest
from app.services.devices import register_device
from pydantic import SecretStr, ValidationError
from pytest import raises

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    ROOT
    / "app"
    / "db"
    / "migrations"
    / "versions"
    / "20260908_0017_p17_devices_outbox_notifications.py"
)
TOKEN = "a" * 64


def test_environment_matches_app_env() -> None:
    assert required_device_environment("test") == "sandbox"
    assert required_device_environment("dev") == "sandbox"
    assert required_device_environment("prod") == "production"
    assert environment_is_compatible("test", "sandbox")
    assert not environment_is_compatible("test", "production")
    assert environment_is_compatible("prod", "production")


def test_token_hash_and_encrypt_roundtrip_without_plaintext_in_cipher() -> None:
    settings = Settings(app_env="test")
    digest = hash_apns_token(TOKEN)
    assert len(digest) == 64
    assert digest == hash_apns_token(TOKEN)
    cipher = encrypt_apns_token(TOKEN, settings.device_token_fernet_key())
    assert TOKEN not in cipher
    assert decrypt_apns_token(cipher, settings.device_token_fernet_key()) == TOKEN


def test_device_token_key_presence_is_not_the_secret() -> None:
    secret = "gAAAA-not-a-real-fernet-key-value"
    settings = Settings(app_env="dev", device_token_key=SecretStr(secret))
    presence = settings.config_presence()
    assert presence["device_token_key"] == "set"
    assert secret not in str(presence)


def test_prod_requires_device_token_key() -> None:
    with raises(ValidationError):
        Settings(
            app_env="prod",
            database_url_api=SecretStr("postgresql://probe:probe@127.0.0.1:1/kelin"),
            supabase_jwt_issuer="https://example.supabase.co/auth/v1",
            supabase_jwt_audience="authenticated",
            supabase_jwks_url="https://example.supabase.co/auth/v1/.well-known/jwks.json",
            cursor_hmac_secret=SecretStr("cursor-secret"),
        )


def test_register_logs_and_source_omit_plaintext_token() -> None:
    source = getsource(register_device)
    logged = source.split("_LOGGER.info")[-1]
    assert "apns_token" not in logged
    assert "encrypt_apns_token" in source
    redacted = redact_event_dict({"apns_token": TOKEN, "device_id": "abc"})
    assert redacted["apns_token"] == "[redacted]"
    assert TOKEN not in str(redacted)


def test_openapi_has_devices_and_lifespan_is_not_a_worker() -> None:
    exported = export_openapi(create_app(Settings(app_env="test")))
    paths = exported.document["paths"]
    assert "/api/v1/devices" in paths
    assert "post" in paths["/api/v1/devices"]
    boot = getsource(create_app) + getsource(_lifespan)
    assert "run_scheduler_tick" not in boot
    assert "run_worker_tick" not in boot
    assert "python -m app.scheduler" not in boot


def test_upsert_device_is_api_only_in_migration() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "GRANT EXECUTE ON FUNCTION private.upsert_device(" in sql
    assert "timestamptz) TO kelin_api" in sql
    assert "upsert_device(" not in "".join(
        line for line in sql.splitlines() if "kelin_worker" in line
    )


def test_request_and_result_shapes() -> None:
    body = RegisterDeviceRequest.model_validate(
        {
            "client_id": "00000000-0000-4000-8000-0000000000c1",
            "installation_id": "00000000-0000-4000-8000-0000000000c2",
            "apns_token": TOKEN,
            "environment": "sandbox",
            "enabled": False,
            "app_version": "1.0.0",
            "locale": "zh-Hans",
        }
    )
    assert body.enabled is False
    DeviceRegistration.model_validate(
        {
            "device_id": "00000000-0000-4000-8000-0000000000a1",
            "enabled": False,
            "updated_at": "2026-09-12T09:00:00Z",
        }
    )
