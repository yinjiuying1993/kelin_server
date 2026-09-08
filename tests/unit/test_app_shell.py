from app.core.config import Settings, get_settings
from app.main import create_app
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr


def test_create_app_returns_fastapi() -> None:
    application = create_app(Settings(app_env="test"))
    assert isinstance(application, FastAPI)


def test_empty_shell_has_no_api_v1_routes() -> None:
    application = create_app(Settings(app_env="test"))
    paths = {getattr(route, "path", "") for route in application.routes}
    assert not any(path.startswith("/api/") for path in paths)


def test_request_id_header_is_echoed_or_generated() -> None:
    application = create_app(Settings(app_env="test"))
    client = TestClient(application)
    response = client.get("/missing", headers={"X-Request-ID": "req-test-1"})
    assert response.status_code == 404
    assert response.headers["X-Request-ID"] == "req-test-1"


def test_prod_disables_docs() -> None:
    get_settings.cache_clear()
    application = create_app(
        Settings(
            app_env="prod",
            database_url_api=SecretStr("postgresql://probe:probe@127.0.0.1:1/kelin"),
        )
    )
    client = TestClient(application)
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_dev_enables_docs() -> None:
    application = create_app(Settings(app_env="dev"))
    client = TestClient(application)
    assert client.get("/openapi.json").status_code == 200
