from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.core.config import Settings
from app.main import create_app
from app.schemas.onboarding import CompleteOnboardingRequest, OnboardingCompleteResult
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


def test_openapi_post_onboarding_complete_locks_fields_and_errors() -> None:
    spec = create_app(Settings(app_env="test")).openapi()
    path = spec["paths"]["/api/v1/onboarding/complete"]["post"]
    request_schema = path["requestBody"]["content"]["application/json"]["schema"]
    required = set(_resolve_schema(spec, request_schema).get("required", []))
    assert required == {"client_id", "expected_spirit_version"}
    responses = path["responses"]
    assert "200" in responses
    assert "401" in responses
    assert "409" in responses
    assert "422" in responses
    description_409 = responses["409"].get("description", "")
    assert "ONBOARDING_INCOMPLETE" in description_409
    assert "ONBOARDING_ALREADY_COMPLETED" in description_409
    success_schema = responses["200"]["content"]["application/json"]["schema"]
    data_schema = _resolve_property(spec, success_schema, "data")
    patch = _resolve_property(spec, data_schema, "patch")
    patch_names = _schema_property_names(spec, patch)
    assert "onboarding" in patch_names
    assert "spirit" in patch_names
    assert "room" in patch_names
    assert "report" in patch_names
    room = _resolve_property(spec, patch, "room")
    assert "weather" in _schema_property_names(spec, room)
    report = _resolve_property(spec, patch, "report")
    assert "eligibility" in _schema_property_names(spec, report)
    description = path.get("description") or path.get("summary") or ""
    assert "hatched_at" in description
    assert "ONBOARDING_INCOMPLETE" in description


def test_success_fixture_keeps_ordinary_rounds_at_zero() -> None:
    from pathlib import Path

    from app.contracts.fixtures import COMPLETE_ENTRY_ID, load_success

    root = Path(__file__).resolve().parents[2] / "fixtures"
    loaded = load_success(root, COMPLETE_ENTRY_ID)
    result = OnboardingCompleteResult.model_validate(loaded.data)
    assert result.resource.type == "spirit"
    assert result.patch.onboarding is not None
    assert result.patch.onboarding.required is False
    assert result.patch.onboarding.step == 5
    assert result.patch.spirit is not None
    assert result.patch.spirit.onboarding_step == 5
    assert result.patch.spirit.hatched_at is not None
    assert result.patch.room is not None
    assert result.patch.room.weather == "cloudy"
    assert result.patch.room.layers == ["spirit"]
    assert result.patch.spirit.status == "home"
    assert result.patch.spirit.stage == "whelp"
    assert result.patch.spirit.scholar_marks == []
    assert result.patch.snapshot_version == result.patch.spirit.version
    assert result.patch.report is not None
    assert result.patch.report.eligibility.completed_dialogue_rounds == 0


def test_post_onboarding_complete_without_auth_is_unauthenticated() -> None:
    client = TestClient(create_app(Settings(app_env="test")))
    response = client.post("/api/v1/onboarding/complete", json=_valid_request())
    assert response.status_code == 401
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "UNAUTHENTICATED"


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
    for item in resolved.get("allOf", []):
        if isinstance(item, dict):
            nested = _resolve_schema(spec, item)
            props = nested.get("properties", {})
            if name in props and isinstance(props[name], dict):
                return _resolve_schema(spec, props[name])
    raise AssertionError(f"schema property {name} not found")


def _resolve_schema(spec: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        node: Any = spec
        for part in ref[2:].split("/"):
            node = node[part]
        if isinstance(node, dict):
            return _resolve_schema(spec, node)
    for key in ("anyOf", "oneOf"):
        options = schema.get(key)
        if isinstance(options, list):
            for option in options:
                if isinstance(option, dict) and option.get("type") != "null":
                    return _resolve_schema(spec, option)
    return schema
