from io import StringIO
from unittest.mock import patch

import pytest
from app.core.config import Settings
from app.core.db_probe import (
    AsyncpgDatabaseProbe,
    DatabaseUnavailable,
    FailingDatabaseProbe,
    OkDatabaseProbe,
    asyncpg_connect_dsn,
)
from app.main import create_app
from fastapi.testclient import TestClient
from pydantic import SecretStr


class _BoomProbe:
    async def ping(self) -> None:
        raise AssertionError("live must not ping the database")


def test_health_paths_are_root_not_api_v1() -> None:
    application = create_app(Settings(app_env="test"))
    spec_paths = set(application.openapi().get("paths", {}))
    assert "/health/live" in spec_paths
    assert "/health/ready" in spec_paths
    assert "/api/v1/spirits" in spec_paths
    assert "/api/v1/bootstrap" in spec_paths
    assert "/api/v1/chat" in spec_paths
    assert "/api/v1/extract" in spec_paths
    assert "/api/v1/feed" in spec_paths
    assert "/api/v1/feeds/{feed_id}" in spec_paths
    assert "/api/v1/feeds/{feed_id}/complete" in spec_paths
    assert "/api/v1/feeds/{feed_id}/cancel" in spec_paths
    assert "/api/v1/messages" in spec_paths
    assert "/api/v1/memories" in spec_paths
    assert "/api/v1/memories/{memory_id}" in spec_paths
    assert "/api/v1/onboarding/complete" in spec_paths
    assert "/api/v1/storage/sight-upload-url" in spec_paths
    assert "/api/v1/transcribe" in spec_paths
    assert "/api/v1/synthesize" in spec_paths
    assert not any(path.startswith("/api/") for path in spec_paths if path.startswith("/health/"))


def test_live_returns_ok_without_database() -> None:
    application = create_app(Settings(app_env="test"), db_probe=_BoomProbe())
    response = TestClient(application).get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "live"}


def test_ready_not_ready_when_url_unset() -> None:
    application = create_app(Settings(app_env="test", database_url_api=None))
    response = TestClient(application).get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"] == "unavailable"
    assert body["checks"]["config"] == "ok"
    assert body["checks"]["migration"] == "not_applicable"


def test_ready_not_ready_when_probe_fails() -> None:
    application = create_app(Settings(app_env="test"), db_probe=FailingDatabaseProbe())
    response = TestClient(application).get("/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_ready_ok_with_stub_probe() -> None:
    application = create_app(Settings(app_env="test"), db_probe=OkDatabaseProbe())
    response = TestClient(application).get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {
            "config": "ok",
            "database": "ok",
            "migration": "not_applicable",
        },
    }


def test_ready_failure_logs_do_not_contain_secret() -> None:
    secret = "postgresql://probe:leak-me-now@127.0.0.1:5432/kelin"
    buf = StringIO()
    with patch("sys.stdout", buf):
        application = create_app(
            Settings(app_env="dev", database_url_api=SecretStr(secret)),
            db_probe=FailingDatabaseProbe(),
        )
        TestClient(application).get("/health/ready")
    text = buf.getvalue()
    assert "leak-me-now" not in text
    assert "postgresql://" not in text
    assert "database_unavailable" in text


def test_asyncpg_probe_dsn_strips_sqlalchemy_driver() -> None:
    dsn = asyncpg_connect_dsn(
        "postgresql+asyncpg://kelin_test:kelin_test@postgres-test:5432/kelin_test"
    )
    assert dsn == "postgresql://kelin_test:kelin_test@postgres-test:5432/kelin_test"
    assert asyncpg_connect_dsn("postgresql://kelin_test:x@127.0.0.1:5433/kelin_test").startswith(
        "postgresql://"
    )
    assert "+asyncpg" not in dsn


@pytest.mark.asyncio
async def test_asyncpg_probe_error_does_not_include_dsn() -> None:
    probe = AsyncpgDatabaseProbe(SecretStr("postgresql://probe:leak-me-now@127.0.0.1:1/kelin"))
    with pytest.raises(DatabaseUnavailable) as exc_info:
        await probe.ping()
    message = str(exc_info.value)
    assert "leak-me-now" not in message
    assert "postgresql://" not in message
