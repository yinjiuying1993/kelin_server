from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.main import create_app
from app.schemas.spirit import CreateSpiritRequest, MutationResult
from fastapi.testclient import TestClient
from pydantic import ValidationError


def _valid_request() -> dict[str, Any]:
    return {
        "client_id": str(uuid4()),
        "egg": "warm",
        "name": "未名",
        "consents": {
            "ai_disclosure": {
                "document_version": "2026-09",
                "explicitly_accepted": True,
            },
            "data_notice": {"document_version": "2026-09", "displayed": True},
            "user_terms": {"document_version": "2026-09", "displayed": True},
        },
    }


def test_create_spirit_request_rejects_user_id() -> None:
    payload = _valid_request()
    payload["user_id"] = str(uuid4())
    with pytest.raises(ValidationError):
        CreateSpiritRequest.model_validate(payload)


def test_create_spirit_request_requires_explicit_ai_disclosure() -> None:
    payload = _valid_request()
    payload["consents"]["ai_disclosure"]["explicitly_accepted"] = False
    with pytest.raises(ValidationError):
        CreateSpiritRequest.model_validate(payload)


def test_create_spirit_request_does_not_treat_displayed_as_checkbox() -> None:
    payload = _valid_request()
    payload["consents"]["data_notice"]["explicitly_accepted"] = True
    with pytest.raises(ValidationError):
        CreateSpiritRequest.model_validate(payload)


def test_create_spirit_request_rejects_unknown_egg() -> None:
    payload = _valid_request()
    payload["egg"] = "hot"
    with pytest.raises(ValidationError):
        CreateSpiritRequest.model_validate(payload)


def test_openapi_post_spirits_locks_fields_errors_and_patch() -> None:
    spec = create_app(Settings(app_env="test")).openapi()
    path = spec["paths"]["/api/v1/spirits"]["post"]
    request_schema = path["requestBody"]["content"]["application/json"]["schema"]
    assert "user_id" not in _schema_property_names(spec, request_schema)
    egg = _resolve_property(spec, request_schema, "egg")
    assert set(egg.get("enum", [])) == {"warm", "cold", "wild"}
    ai = _resolve_property(
        spec,
        _resolve_property(spec, request_schema, "consents"),
        "ai_disclosure",
    )
    assert "explicitly_accepted" in _schema_property_names(spec, ai)
    notice = _resolve_property(
        spec,
        _resolve_property(spec, request_schema, "consents"),
        "data_notice",
    )
    notice_names = _schema_property_names(spec, notice)
    assert "displayed" in notice_names
    assert "explicitly_accepted" not in notice_names
    responses = path["responses"]
    assert "200" in responses
    assert "401" in responses
    assert "409" in responses
    assert "422" in responses
    success_schema = responses["200"]["content"]["application/json"]["schema"]
    success_names = _schema_property_names(spec, success_schema)
    assert "ok" in success_names
    assert "data" in success_names
    data_schema = _resolve_property(spec, success_schema, "data")
    data_names = _schema_property_names(spec, data_schema)
    assert "resource" in data_names
    assert "patch" in data_names
    patch = _resolve_property(spec, data_schema, "patch")
    patch_names = _schema_property_names(spec, patch)
    assert "snapshot_version" in patch_names
    assert "spirit" in patch_names
    assert "onboarding" in patch_names


def test_success_fixture_matches_mutation_result() -> None:
    from pathlib import Path

    from app.contracts.fixtures import SPIRITS_ENTRY_ID, load_success

    root = Path(__file__).resolve().parents[2] / "fixtures"
    loaded = load_success(root, SPIRITS_ENTRY_ID)
    result = MutationResult.model_validate(loaded.data)
    assert result.resource.type == "spirit"
    assert result.patch.snapshot_version == 1
    assert result.patch.spirit is not None
    assert result.patch.spirit.egg == "warm"
    assert result.patch.onboarding is not None
    assert result.patch.onboarding.step == 0


def test_post_spirits_without_auth_is_unauthenticated() -> None:
    client = TestClient(create_app(Settings(app_env="test")))
    response = client.post("/api/v1/spirits", json=_valid_request())
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
            return node
    return schema
