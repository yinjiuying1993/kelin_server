from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.core.config import Settings
from app.main import create_app
from app.schemas.onboarding import CompleteOnboardingRequest
from fastapi.testclient import TestClient
from pydantic import ValidationError


def _valid_request() -> dict[str, Any]:
    return {
        "client_id": str(uuid4()),
        "expected_spirit_version": 1,
    }


def test_complete_request_requires_expected_spirit_version() -> None:
    payload = _valid_request()
    del payload["expected_spirit_version"]
    try:
        CompleteOnboardingRequest.model_validate(payload)
    except ValidationError as exc:
        missing = [
            ".".join(str(part) for part in error.get("loc", ()))
            for error in exc.errors()
            if error.get("type") == "missing"
        ]
        assert "expected_spirit_version" in missing
    else:
        raise AssertionError("expected_spirit_version must be required")


def test_complete_request_rejects_user_id() -> None:
    payload = _valid_request()
    payload["user_id"] = str(uuid4())
    try:
        CompleteOnboardingRequest.model_validate(payload)
    except ValidationError:
        return
    raise AssertionError("complete request must forbid user_id")


def test_openapi_hides_onboarding_complete() -> None:
    spec = create_app(Settings(app_env="test")).openapi()
    assert "/api/v1/onboarding/complete" not in spec["paths"]


def test_post_onboarding_complete_without_auth_is_unauthenticated() -> None:
    client = TestClient(create_app(Settings(app_env="test")))
    response = client.post("/api/v1/onboarding/complete", json=_valid_request())
    assert response.status_code == 404
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "NOT_FOUND"
