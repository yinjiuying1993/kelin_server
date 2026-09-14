from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.main import create_app
from app.schemas.memory import (
    CLEAR_ALL_MEMORIES,
    MemoryClearRequest,
    MemoryDeleteRequest,
    MemoryPage,
    MemoryPatchRequest,
    MemoryTombstone,
)
from fastapi.testclient import TestClient
from pydantic import ValidationError


def test_openapi_get_memories_locks_filter_cursor_and_tombstones() -> None:
    spec = create_app(Settings(app_env="test")).openapi()
    path = spec["paths"]["/api/v1/memories"]["get"]
    params = {item["name"]: item for item in path.get("parameters", []) if "name" in item}
    assert "filter" in params
    assert params["filter"].get("required") is True
    filter_schema = _resolve_schema(spec, params["filter"].get("schema", {}))
    assert set(filter_schema.get("enum", [])) == {
        "all",
        "relationship",
        "knowledge",
        "speech",
        "sight",
    }
    description = (params["filter"].get("description") or "") + (path.get("description") or "")
    assert "preference" in description
    assert "filter_hash" in description
    assert "cursor" in params
    assert params["cursor"].get("required") is not True
    limit_schema = _resolve_schema(spec, params["limit"].get("schema", {}))
    assert limit_schema.get("minimum") == 1
    assert limit_schema.get("maximum") == 50
    assert limit_schema.get("default") == 30
    responses = path["responses"]
    assert "200" in responses
    assert "401" in responses
    assert "422" in responses
    assert "503" in responses
    assert "INVALID_CURSOR" in responses["422"].get("description", "")
    success_schema = responses["200"]["content"]["application/json"]["schema"]
    data_schema = _resolve_property(spec, success_schema, "data")
    names = _schema_property_names(spec, data_schema)
    assert names == {"items", "tombstones", "next_cursor", "has_more", "snapshot_at"}
    item_names = _schema_property_names(
        spec, _resolve_schema(spec, _resolve_property(spec, data_schema, "items").get("items", {}))
    )
    assert item_names == {
        "id",
        "type",
        "summary",
        "tags",
        "salience",
        "confidence",
        "status",
        "version",
        "created_at",
    }
    tombstone_names = _schema_property_names(
        spec,
        _resolve_schema(spec, _resolve_property(spec, data_schema, "tombstones").get("items", {})),
    )
    assert tombstone_names == {"id", "deleted_at"}
    assert "summary" not in tombstone_names


def test_openapi_memory_mutations_lock_actions_confirm_and_errors() -> None:
    spec = create_app(Settings(app_env="test")).openapi()
    patch = spec["paths"]["/api/v1/memories/{memory_id}"]["patch"]
    request_schema = patch["requestBody"]["content"]["application/json"]["schema"]
    required = set(_resolve_schema(spec, request_schema).get("required", []))
    assert required == {"client_id", "expected_version", "action"}
    action = _resolve_property(spec, request_schema, "action")
    assert set(action.get("enum", [])) == {"correct", "seal"}
    summary = _resolve_property(spec, request_schema, "summary")
    assert summary.get("minLength") == 1
    assert summary.get("maxLength") == 500
    assert "salience" not in _schema_property_names(spec, request_schema)
    patch_responses = patch["responses"]
    assert "404" in patch_responses
    assert "409" in patch_responses
    assert "MEMORY_NOT_ACTIVE" in patch_responses["409"].get("description", "")

    delete_one = spec["paths"]["/api/v1/memories/{memory_id}"]["delete"]
    delete_required = set(
        _resolve_schema(
            spec, delete_one["requestBody"]["content"]["application/json"]["schema"]
        ).get("required", [])
    )
    assert delete_required == {"client_id", "expected_version"}

    clear = spec["paths"]["/api/v1/memories"]["delete"]
    clear_schema = clear["requestBody"]["content"]["application/json"]["schema"]
    confirm = _resolve_property(spec, clear_schema, "confirm")
    assert confirm.get("const") == CLEAR_ALL_MEMORIES or set(confirm.get("enum", [])) == {
        CLEAR_ALL_MEMORIES
    }
    assert "CLEAR_ALL_MEMORIES" in (clear.get("description") or "")


def test_memory_page_rejects_deleted_items_and_cursor_mismatch() -> None:
    with pytest.raises(ValidationError):
        MemoryPage.model_validate(
            {
                "items": [],
                "tombstones": [],
                "next_cursor": None,
                "has_more": True,
                "snapshot_at": "2026-09-08T00:00:00Z",
            }
        )
    with pytest.raises(ValidationError):
        MemoryPage.model_validate(
            {
                "items": [
                    {
                        "id": str(uuid4()),
                        "type": "preference",
                        "summary": "用户希望被叫阿年",
                        "tags": [],
                        "salience": 90,
                        "confidence": 0.9,
                        "status": "deleted",
                        "version": 1,
                        "created_at": "2026-09-08T00:00:00Z",
                    }
                ],
                "tombstones": [],
                "next_cursor": None,
                "has_more": False,
                "snapshot_at": "2026-09-08T00:00:00Z",
            }
        )


def test_tombstone_forbids_summary() -> None:
    with pytest.raises(ValidationError):
        MemoryTombstone.model_validate(
            {
                "id": str(uuid4()),
                "deleted_at": "2026-09-08T00:00:00Z",
                "summary": "用户希望被叫阿年",
            }
        )


def test_patch_request_correct_and_seal_rules() -> None:
    MemoryPatchRequest.model_validate(
        {
            "client_id": str(uuid4()),
            "expected_version": 2,
            "action": "correct",
            "summary": "请叫我阿年",
        }
    )
    MemoryPatchRequest.model_validate(
        {"client_id": str(uuid4()), "expected_version": 2, "action": "seal"}
    )
    with pytest.raises(ValidationError):
        MemoryPatchRequest.model_validate(
            {"client_id": str(uuid4()), "expected_version": 2, "action": "correct"}
        )
    with pytest.raises(ValidationError):
        MemoryPatchRequest.model_validate(
            {
                "client_id": str(uuid4()),
                "expected_version": 2,
                "action": "seal",
                "summary": "请叫我阿年",
            }
        )
    with pytest.raises(ValidationError):
        MemoryPatchRequest.model_validate(
            {
                "client_id": str(uuid4()),
                "expected_version": 2,
                "action": "correct",
                "summary": "请叫我阿年",
                "salience": 1,
            }
        )


def test_clear_request_requires_exact_confirm() -> None:
    MemoryClearRequest.model_validate({"client_id": str(uuid4()), "confirm": CLEAR_ALL_MEMORIES})
    with pytest.raises(ValidationError):
        MemoryClearRequest.model_validate({"client_id": str(uuid4()), "confirm": "CLEAR_ALL"})
    MemoryDeleteRequest.model_validate({"client_id": str(uuid4()), "expected_version": 1})


def test_memory_routes_without_auth_are_unauthenticated() -> None:
    client = TestClient(create_app(Settings(app_env="test")))
    memory_id = str(uuid4())
    cases = (
        client.get("/api/v1/memories?filter=all"),
        client.patch(
            f"/api/v1/memories/{memory_id}",
            json={"client_id": str(uuid4()), "expected_version": 1, "action": "seal"},
        ),
        client.request(
            "DELETE",
            f"/api/v1/memories/{memory_id}",
            json={"client_id": str(uuid4()), "expected_version": 1},
        ),
        client.request(
            "DELETE",
            "/api/v1/memories",
            json={"client_id": str(uuid4()), "confirm": CLEAR_ALL_MEMORIES},
        ),
    )
    for response in cases:
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
