from uuid import UUID

from app.core.config import Settings, get_settings
from app.main import create_app
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr


def test_create_app_returns_fastapi() -> None:
    application = create_app(Settings(app_env="test"))
    assert isinstance(application, FastAPI)


def test_api_v1_registers_bootstrap_and_spirits() -> None:
    application = create_app(Settings(app_env="test"))
    spec_paths = set(application.openapi().get("paths", {}))
    api_paths = {path for path in spec_paths if path.startswith("/api/")}
    assert api_paths == {
        "/api/v1/account",
        "/api/v1/bootstrap",
        "/api/v1/chat",
        "/api/v1/devices",
        "/api/v1/extract",
        "/api/v1/feed",
        "/api/v1/feeds/{feed_id}",
        "/api/v1/feeds/{feed_id}/cancel",
        "/api/v1/feeds/{feed_id}/complete",
        "/api/v1/friends",
        "/api/v1/friends/{friend_id}",
        "/api/v1/memories",
        "/api/v1/memories/{memory_id}",
        "/api/v1/messages",
        "/api/v1/moderate-sight",
        "/api/v1/onboarding/complete",
        "/api/v1/pact-answer",
        "/api/v1/pact-session",
        "/api/v1/pact-skip",
        "/api/v1/pacts",
        "/api/v1/postcards",
        "/api/v1/recall",
        "/api/v1/postcards/{postcard_id}/read",
        "/api/v1/report",
        "/api/v1/report/line",
        "/api/v1/spirit",
        "/api/v1/spirits",
        "/api/v1/storage/sight-upload-url",
        "/api/v1/synthesize",
        "/api/v1/transcribe",
    }


def test_request_id_header_is_echoed_when_uuid() -> None:
    application = create_app(Settings(app_env="test"))
    client = TestClient(application)
    request_id = "0199a000-0000-4000-8000-000000000001"
    response = client.get("/missing", headers={"X-Request-ID": request_id})
    assert response.status_code == 404
    assert response.headers["X-Request-ID"] == request_id
    assert response.json()["request_id"] == request_id


def test_invalid_request_id_header_is_replaced() -> None:
    application = create_app(Settings(app_env="test"))
    client = TestClient(application)
    response = client.get("/missing", headers={"X-Request-ID": "req-test-1"})
    assert response.status_code == 404
    header = response.headers["X-Request-ID"]
    UUID(header)
    assert header != "req-test-1"


def test_prod_disables_docs() -> None:
    get_settings.cache_clear()
    application = create_app(
        Settings(
            app_env="prod",
            database_url_api=SecretStr("postgresql://probe:probe@127.0.0.1:1/kelin"),
            supabase_jwt_issuer="https://example.supabase.co/auth/v1",
            supabase_jwt_audience="authenticated",
            supabase_jwks_url="https://example.supabase.co/auth/v1/.well-known/jwks.json",
            cursor_hmac_secret=SecretStr("test-only-cursor-hmac"),
            device_token_key=SecretStr("kelin-test-device-token-key-32bytes"),
        )
    )
    client = TestClient(application)
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_dev_enables_docs() -> None:
    application = create_app(Settings(app_env="dev"))
    client = TestClient(application)
    assert client.get("/openapi.json").status_code == 200
