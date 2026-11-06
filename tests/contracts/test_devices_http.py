"""POST /devices is no longer a public settlement entry."""

from __future__ import annotations

from uuid import uuid4

from app.core.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


def test_devices_post_does_not_settle() -> None:
    client = TestClient(create_app(Settings(app_env="test")))
    response = client.post(
        "/api/v1/devices",
        json={
            "client_id": str(uuid4()),
            "installation_id": str(uuid4()),
            "apns_token": "d" * 64,
            "environment": "sandbox",
            "enabled": True,
        },
    )
    assert response.status_code == 404
    body = response.json()
    assert body["ok"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "NOT_FOUND"
