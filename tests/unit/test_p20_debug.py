"""P20 dev-only Debug: env gate, independent auth, whitelist, no secrets."""

from __future__ import annotations

from uuid import uuid4

from app.core.config import Settings
from app.core.security import CurrentUser, require_current_user
from app.domain.debug import (
    debug_identity_allowed,
    debug_router_enabled,
    source_host_allowed,
)
from app.main import create_app
from app.schemas.debug import DebugSpiritStateRequest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from pytest import raises


def _prod_settings(**extra: object) -> Settings:
    return Settings(
        app_env="prod",
        database_url_api=SecretStr("postgresql://probe:probe@127.0.0.1:1/kelin"),
        supabase_jwt_issuer="https://example.supabase.co/auth/v1",
        supabase_jwt_audience="authenticated",
        supabase_jwks_url="https://example.supabase.co/auth/v1/.well-known/jwks.json",
        cursor_hmac_secret=SecretStr("test-only-cursor-hmac"),
        device_token_key=SecretStr("kelin-test-device-token-key-32bytes"),
        **extra,  # type: ignore[arg-type]
    )


def test_debug_router_env_gate() -> None:
    assert debug_router_enabled(Settings(app_env="test")) is False
    assert debug_router_enabled(Settings(app_env="test", enable_debug_api_for_tests=True)) is True
    assert debug_router_enabled(Settings(app_env="dev")) is True
    assert debug_router_enabled(_prod_settings()) is False
    with raises(ValidationError):
        _prod_settings(enable_debug_api_for_tests=True)


def test_source_and_identity_rules() -> None:
    assert source_host_allowed("127.0.0.1", app_env="dev") is True
    assert source_host_allowed("10.0.0.8", app_env="dev") is True
    assert source_host_allowed("8.8.8.8", app_env="dev") is False
    assert source_host_allowed("testclient", app_env="test") is True
    uid = uuid4()
    settings = Settings(
        app_env="test",
        enable_debug_api_for_tests=True,
        debug_allowlist=str(uid),
        debug_token=SecretStr("kelin-debug-token"),
    )
    assert debug_identity_allowed(user_id=uid, settings=settings, provided_token=None) is True
    other = uuid4()
    assert debug_identity_allowed(user_id=other, settings=settings, provided_token="nope") is False
    assert (
        debug_identity_allowed(user_id=other, settings=settings, provided_token="kelin-debug-token")
        is True
    )
    dumped = str(settings.config_presence())
    assert "kelin-debug-token" not in dumped
    assert str(uid) not in dumped


def test_state_schema_forbids_arbitrary_fields() -> None:
    DebugSpiritStateRequest.model_validate({"status": "lost", "hunger": 10})
    with raises(ValidationError):
        DebugSpiritStateRequest.model_validate({"user_id": str(uuid4()), "status": "lost"})
    with raises(ValidationError):
        DebugSpiritStateRequest.model_validate({"sql": "drop table", "hunger": 1})
    with raises(ValidationError):
        DebugSpiritStateRequest.model_validate({"hunger": 101})


def test_prod_and_default_test_openapi_have_no_debug() -> None:
    test_paths = create_app(Settings(app_env="test")).openapi().get("paths", {})
    assert not any("debug" in path for path in test_paths)
    prod_app = create_app(_prod_settings())
    client = TestClient(prod_app)
    assert client.get("/api/v1/debug/usage").status_code == 404
    assert client.post("/api/v1/debug/spirit/state", json={"status": "lost"}).status_code == 404


def test_debug_auth_negatives_and_illegal_body() -> None:
    allowlisted = uuid4()
    outsider = uuid4()
    token = "kelin-debug-token"
    application = create_app(
        Settings(
            app_env="test",
            enable_debug_api_for_tests=True,
            debug_allowlist=str(allowlisted),
            debug_token=SecretStr(token),
        )
    )
    application.dependency_overrides[require_current_user] = lambda: CurrentUser(id=outsider)
    client = TestClient(application)
    denied = client.post("/api/v1/debug/spirit/state", json={"status": "lost"})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "FORBIDDEN"
    assert token not in denied.text
    wrong = client.post(
        "/api/v1/debug/spirit/state",
        json={"status": "lost"},
        headers={"X-Debug-Token": "guessable"},
    )
    assert wrong.status_code == 403
    assert token not in wrong.text
    application.dependency_overrides[require_current_user] = lambda: CurrentUser(id=allowlisted)
    illegal = client.post(
        "/api/v1/debug/spirit/state",
        json={"status": "lost", "user_id": str(outsider), "sql": "select 1"},
    )
    assert illegal.status_code == 422
    paths = application.openapi().get("paths", {})
    assert "/api/v1/debug/spirit/state" in paths
    assert "/api/v1/debug/usage" in paths
    assert "/api/v1/debug/reset" in paths
