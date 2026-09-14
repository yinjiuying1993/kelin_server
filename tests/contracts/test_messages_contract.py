from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.main import create_app
from app.schemas.messages import MessagePage, MessagePublic
from fastapi.testclient import TestClient
from pydantic import ValidationError


def test_openapi_get_messages_locks_cursor_page_and_public_fields() -> None:
    spec = create_app(Settings(app_env="test")).openapi()
    path = spec["paths"]["/api/v1/messages"]["get"]
    params = {item["name"]: item for item in path.get("parameters", []) if "name" in item}
    assert "cursor" in params
    assert "limit" in params
    cursor = params["cursor"]
    assert cursor.get("required") is not True
    limit = params["limit"]
    limit_schema = _resolve_schema(spec, limit.get("schema", {}))
    assert limit_schema.get("minimum") == 1
    assert limit_schema.get("maximum") == 50
    assert limit_schema.get("default") == 30
    responses = path["responses"]
    assert "200" in responses
    assert "401" in responses
    assert "422" in responses
    assert "503" in responses
    assert "INVALID_CURSOR" in responses["422"].get("description", "")
    assert "DEPENDENCY_UNAVAILABLE" in responses["503"].get("description", "")
    success_schema = responses["200"]["content"]["application/json"]["schema"]
    data_schema = _resolve_property(spec, success_schema, "data")
    names = _schema_property_names(spec, data_schema)
    assert names == {"items", "next_cursor", "has_more", "snapshot_at"}
    items = _resolve_property(spec, data_schema, "items")
    item_schema = _resolve_schema(spec, items.get("items", items))
    item_names = _schema_property_names(spec, item_schema)
    assert item_names == {
        "id",
        "client_id",
        "role",
        "content",
        "source",
        "status",
        "reply_to_message_id",
        "source_refs",
        "created_at",
    }
    assert "provider_request_id" not in item_names
    role = _resolve_property(spec, item_schema, "role")
    assert set(role.get("enum", [])) == {"user", "spirit"}
    source = _resolve_property(spec, item_schema, "source")
    assert set(source.get("enum", [])) == {"text", "voice", "onboarding"}
    status = _resolve_property(spec, item_schema, "status")
    assert set(status.get("enum", [])) == {"accepted", "generated", "failed"}
    description = path.get("description") or path.get("summary") or ""
    assert "open window" in description
    assert "message IDs" in description


def test_success_fixture_is_empty_page() -> None:
    from pathlib import Path

    from app.contracts.fixtures import MESSAGES_ENTRY_ID, load_success

    root = Path(__file__).resolve().parents[2] / "fixtures"
    loaded = load_success(root, MESSAGES_ENTRY_ID)
    page = MessagePage.model_validate(loaded.data)
    assert page.items == []
    assert page.has_more is False
    assert page.next_cursor is None


def test_message_page_rejects_has_more_without_cursor() -> None:
    try:
        MessagePage.model_validate(
            {
                "items": [],
                "next_cursor": None,
                "has_more": True,
                "snapshot_at": "2026-09-08T00:00:00Z",
            }
        )
    except ValidationError:
        return
    raise AssertionError("has_more true requires next_cursor")


def test_message_public_rejects_system_role() -> None:
    payload: dict[str, Any] = {
        "id": "00000000-0000-4000-8000-000000000070",
        "client_id": "00000000-0000-4000-8000-000000000071",
        "role": "system",
        "content": "secret",
        "source": "text",
        "status": "accepted",
        "reply_to_message_id": None,
        "source_refs": [],
        "created_at": "2026-09-08T00:00:00Z",
    }
    try:
        MessagePublic.model_validate(payload)
    except ValidationError:
        return
    raise AssertionError("system role must not be a public history item")


def test_get_messages_without_auth_is_unauthenticated() -> None:
    client = TestClient(create_app(Settings(app_env="test")))
    response = client.get("/api/v1/messages")
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
