from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.core.config import Settings
from app.main import create_app
from app.schemas.moderate import ModerateSightRequest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError


def _request() -> dict[str, Any]:
    return {
        "client_id": str(uuid4()),
        "feed_id": str(uuid4()),
        "upload_session_id": str(uuid4()),
    }


def test_moderate_request_requires_upload_session() -> None:
    payload = _request()
    del payload["upload_session_id"]
    try:
        TypeAdapter(ModerateSightRequest).validate_python(payload)
    except ValidationError as exc:
        missing = [
            ".".join(str(part) for part in error.get("loc", ()))
            for error in exc.errors()
            if error.get("type") == "missing"
        ]
        assert "upload_session_id" in missing
    else:
        raise AssertionError("upload_session_id must be required")


def test_openapi_moderate_sight_locks_unready_and_timeout() -> None:
    spec = create_app(Settings(app_env="test")).openapi()
    path = spec["paths"]["/api/v1/moderate-sight"]["post"]
    responses = path["responses"]
    assert "200" in responses
    assert "409" in responses
    assert "504" in responses
    assert "UPLOAD_NOT_READY" in responses["409"].get("description", "")
    assert "PROVIDER_TIMEOUT" in responses["504"].get("description", "")
    client = TestClient(create_app(Settings(app_env="test")))
    response = client.post("/api/v1/moderate-sight", json=_request())
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
