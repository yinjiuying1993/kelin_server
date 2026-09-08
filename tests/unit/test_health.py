from io import StringIO
from unittest.mock import patch

import pytest
from app.core.config import Settings
from app.core.db_probe import (
    AsyncpgDatabaseProbe,
    DatabaseUnavailable,
    FailingDatabaseProbe,
    OkDatabaseProbe,
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
    route_paths = {getattr(route, "path", "") for route in application.routes}
    assert "/health/live" in spec_paths
    assert "/health/ready" in spec_paths
    assert not any(path.startswith("/api/") for path in spec_paths)
    assert not any(path.startswith("/api/") for path in route_paths)


def test_live_returns_ok_without_database() -> None:
    application = create_app(Settings(app_env="test"), db_probe=_BoomProbe())
    response = TestClient(application).get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "live"}


def test_ready_not_ready_when_url_unset() -> None:
    application = create_app(Settings(app_env="test"))
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


@pytest.mark.asyncio
async def test_asyncpg_probe_error_does_not_include_dsn() -> None:
    probe = AsyncpgDatabaseProbe(SecretStr("postgresql://probe:leak-me-now@127.0.0.1:1/kelin"))
    with pytest.raises(DatabaseUnavailable) as exc_info:
        await probe.ping()
    message = str(exc_info.value)
    assert "leak-me-now" not in message
    assert "postgresql://" not in message
