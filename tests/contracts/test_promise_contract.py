from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.core.config import Settings
from app.main import create_app
from app.schemas.feed import PromiseActionRequest, PromisePatchRequest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError


def _patch_request() -> dict[str, Any]:
    return {
        "client_id": str(uuid4()),
        "expected_version": 2,
        "text": "出门买菜",
        "remind_at": "2026-12-01T13:00:00Z",
    }


def test_promise_patch_requires_expected_version_and_limits_text() -> None:
    payload = _patch_request()
    del payload["expected_version"]
    try:
        TypeAdapter(PromisePatchRequest).validate_python(payload)
    except ValidationError as exc:
        missing = [
            ".".join(str(part) for part in error.get("loc", ()))
            for error in exc.errors()
            if error.get("type") == "missing"
        ]
        assert "expected_version" in missing
    else:
        raise AssertionError("expected_version must be required")
    TypeAdapter(PromisePatchRequest).validate_python({**_patch_request(), "text": "字" * 200})
    try:
        TypeAdapter(PromisePatchRequest).validate_python({**_patch_request(), "text": "字" * 201})
    except ValidationError:
        return
    raise AssertionError("PATCH text longer than 200 must fail")


def test_promise_action_requires_client_id_and_expected_version() -> None:
    TypeAdapter(PromiseActionRequest).validate_python(
        {"client_id": str(uuid4()), "expected_version": 2}
    )
    try:
        TypeAdapter(PromiseActionRequest).validate_python({"client_id": str(uuid4())})
    except ValidationError:
        pass
    else:
        raise AssertionError("expected_version must be required")
    try:
        TypeAdapter(PromiseActionRequest).validate_python(
            {"client_id": str(uuid4()), "expected_version": 2, "user_id": str(uuid4())}
        )
    except ValidationError:
        return
    raise AssertionError("user_id must be forbidden")


def test_openapi_promise_mutations_lock_expected_version_and_not_active() -> None:
    spec = create_app(Settings(app_env="test")).openapi()
    patch = spec["paths"]["/api/v1/feeds/{feed_id}"]["patch"]
    complete = spec["paths"]["/api/v1/feeds/{feed_id}/complete"]["post"]
    cancel = spec["paths"]["/api/v1/feeds/{feed_id}/cancel"]["post"]
    for operation in (patch, complete, cancel):
        responses = operation["responses"]
        assert "200" in responses
        assert "401" in responses
        assert "404" in responses
        assert "409" in responses
        assert "422" in responses
        assert "PROMISE_NOT_ACTIVE" in responses["409"].get("description", "")
        assert "CONFLICT" in responses["409"].get("description", "")
    patch_required = set(
        _resolve_schema(spec, patch["requestBody"]["content"]["application/json"]["schema"]).get(
            "required", []
        )
    )
    assert patch_required == {"client_id", "expected_version", "text", "remind_at"}
    action_required = set(
        _resolve_schema(spec, complete["requestBody"]["content"]["application/json"]["schema"]).get(
            "required", []
        )
    )
    assert action_required == {"client_id", "expected_version"}
    cancel_required = set(
        _resolve_schema(spec, cancel["requestBody"]["content"]["application/json"]["schema"]).get(
            "required", []
        )
    )
    assert cancel_required == {"client_id", "expected_version"}
    success_schema = patch["responses"]["200"]["content"]["application/json"]["schema"]
    data_schema = _resolve_property(spec, success_schema, "data")
    resource_names = _schema_property_names(spec, _resolve_property(spec, data_schema, "resource"))
    assert {"type", "id", "version", "kind", "status", "promise_status"} <= resource_names


def test_app_serves_promise_mutation_paths() -> None:
    client = TestClient(create_app(Settings(app_env="test")))
    feed_id = str(uuid4())
    assert client.patch(f"/api/v1/feeds/{feed_id}", json=_patch_request()).status_code == 401
    action = {"client_id": str(uuid4()), "expected_version": 2}
    assert client.post(f"/api/v1/feeds/{feed_id}/complete", json=action).status_code == 401
    assert client.post(f"/api/v1/feeds/{feed_id}/cancel", json=action).status_code == 401


def _schema_property_names(spec: dict[str, Any], schema: dict[str, Any]) -> set[str]:
    resolved = _resolve_schema(spec, schema)
    names = set(resolved.get("properties", {}))
    for item in resolved.get("allOf", []):
        if isinstance(item, dict):
            names |= _schema_property_names(spec, item)
    return names


def _resolve_property(spec: dict[str, Any], schema: dict[str, Any], name: str) -> dict[str, Any]:
    resolved = _resolve_schema(spec, schema)
    properties = resolved.get("properties", {})
    if name in properties and isinstance(properties[name], dict):
        return _resolve_schema(spec, properties[name])
    raise AssertionError(f"schema property {name} not found")


def _resolve_schema(spec: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        node: Any = spec
        for part in ref[2:].split("/"):
            node = node[part]
        if isinstance(node, dict):
            return _resolve_schema(spec, node)
    return schema
