from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.core.config import Settings
from app.main import create_app
from app.schemas.storage import SightUploadUrlRequest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError


def _request() -> dict[str, Any]:
    return {
        "client_id": str(uuid4()),
        "feed_id": str(uuid4()),
        "mime_type": "image/jpeg",
        "size_bytes": 1024,
        "sha256": "a" * 64,
    }


def test_upload_url_request_rejects_client_path_and_png() -> None:
    payload = _request()
    payload["object_path"] = "sight-temp/custom.jpg"
    try:
        TypeAdapter(SightUploadUrlRequest).validate_python(payload)
    except ValidationError:
        pass
    else:
        raise AssertionError("client object_path must be forbidden")
    payload = _request()
    payload["mime_type"] = "image/png"
    try:
        TypeAdapter(SightUploadUrlRequest).validate_python(payload)
    except ValidationError:
        return
    raise AssertionError("png must be rejected")


def test_upload_url_request_requires_sha256_and_size_bounds() -> None:
    payload = _request()
    del payload["sha256"]
    try:
        TypeAdapter(SightUploadUrlRequest).validate_python(payload)
    except ValidationError as exc:
        missing = [
            ".".join(str(part) for part in error.get("loc", ()))
            for error in exc.errors()
            if error.get("type") == "missing"
        ]
        assert "sha256" in missing
    else:
        raise AssertionError("sha256 must be required")
    try:
        TypeAdapter(SightUploadUrlRequest).validate_python({**_request(), "size_bytes": 0})
    except ValidationError:
        pass
    else:
        raise AssertionError("size 0 must fail")
    try:
        TypeAdapter(SightUploadUrlRequest).validate_python({**_request(), "size_bytes": 5242881})
    except ValidationError:
        return
    raise AssertionError("size above 5 MiB must fail")


def test_openapi_sight_upload_url_locks_put_and_expired() -> None:
    spec = create_app(Settings(app_env="test")).openapi()
    path = spec["paths"]["/api/v1/storage/sight-upload-url"]["post"]
    responses = path["responses"]
    assert "200" in responses
    assert "401" in responses
    assert "410" in responses
    assert "UPLOAD_EXPIRED" in responses["410"].get("description", "")
    request_schema = path["requestBody"]["content"]["application/json"]["schema"]
    blob = str(request_schema)
    assert "object_path" not in blob
    assert "bucket" not in blob
    client = TestClient(create_app(Settings(app_env="test")))
    response = client.post("/api/v1/storage/sight-upload-url", json=_request())
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
