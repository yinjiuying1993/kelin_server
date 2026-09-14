from __future__ import annotations

from io import StringIO
from typing import Annotated
from unittest.mock import patch
from uuid import UUID, uuid4

from app.core.config import Settings
from app.core.security import CurrentUser, require_current_user
from app.main import create_app
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from pydantic import BaseModel


class _ProbeBody(BaseModel):
    name: str


def _app_with_probes() -> FastAPI:
    application = create_app(Settings(app_env="test"))

    @application.get("/api/v1/probe")
    def probe() -> dict[str, bool]:
        return {"pong": True}

    @application.post("/api/v1/probe-input")
    def probe_input(body: _ProbeBody) -> dict[str, str]:
        return {"name": body.name}

    @application.get("/api/v1/probe-boom")
    def probe_boom() -> None:
        raise RuntimeError("SELECT password FROM users JOIN pg_catalog")

    @application.get("/api/v1/me-probe")
    async def me_probe(
        user: Annotated[CurrentUser, Depends(require_current_user)],
    ) -> dict[str, str]:
        return {"id": str(user.id)}

    return application


def _assert_envelope_ids(response: Response) -> str:
    header = response.headers["X-Request-ID"]
    body = response.json()
    assert body["request_id"] == header
    UUID(header)
    assert body["server_time"].endswith("Z")
    assert "ok" in body
    assert "data" in body
    assert "error" in body
    return header


def test_success_envelope_matches_request_id_header() -> None:
    client = TestClient(_app_with_probes())
    request_id = str(uuid4())
    response = client.get("/api/v1/probe", headers={"X-Request-ID": request_id})
    assert response.status_code == 200
    header = _assert_envelope_ids(response)
    assert header == request_id
    body = response.json()
    assert body["ok"] is True
    assert body["data"] == {"pong": True}
    assert body["error"] is None


def test_invalid_request_id_is_replaced_with_uuid() -> None:
    client = TestClient(_app_with_probes())
    response = client.get("/api/v1/probe", headers={"X-Request-ID": "req-test-1"})
    header = _assert_envelope_ids(response)
    assert header != "req-test-1"
    assert response.json()["ok"] is True


def test_pydantic_error_is_invalid_input_envelope() -> None:
    client = TestClient(_app_with_probes())
    response = client.post("/api/v1/probe-input", json={"name": 1})
    assert response.status_code == 422
    _assert_envelope_ids(response)
    body = response.json()
    assert body["ok"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "INVALID_INPUT"
    assert body["error"]["details"]["fields"]
    dumped = response.text
    assert "Traceback" not in dumped
    assert "ctx" not in dumped


def test_internal_error_is_sanitized_500_envelope() -> None:
    buf = StringIO()
    with patch("sys.stdout", buf):
        client = TestClient(_app_with_probes(), raise_server_exceptions=False)
        response = client.get("/api/v1/probe-boom")
    assert response.status_code == 500
    _assert_envelope_ids(response)
    dumped = response.text
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["message"] == "internal error"
    assert "Traceback" not in dumped
    assert "SELECT" not in dumped
    assert "password" not in dumped
    assert "pg_catalog" not in dumped
    assert "File " not in dumped
    assert "unhandled_error" in buf.getvalue()


def test_missing_route_is_not_found_envelope() -> None:
    client = TestClient(create_app(Settings(app_env="test")))
    response = client.get("/missing")
    assert response.status_code == 404
    _assert_envelope_ids(response)
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_health_is_not_wrapped_in_envelope() -> None:
    client = TestClient(create_app(Settings(app_env="test")))
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "live"}


def test_missing_bearer_on_api_route_is_unauthenticated_envelope() -> None:
    client = TestClient(_app_with_probes())
    response = client.get("/api/v1/me-probe")
    assert response.status_code == 401
    _assert_envelope_ids(response)
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
    assert "eyJ" not in response.text
