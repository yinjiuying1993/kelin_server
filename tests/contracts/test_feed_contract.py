from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.core.config import Settings
from app.main import create_app
from app.schemas.feed import FeedCreateRequest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError


def _food_request() -> dict[str, Any]:
    return {"client_id": str(uuid4()), "kind": "food", "payload": {}}


def test_feed_request_requires_client_id() -> None:
    payload = {"kind": "food", "payload": {}}
    try:
        TypeAdapter(FeedCreateRequest).validate_python(payload)
    except ValidationError as exc:
        missing = [
            ".".join(str(part) for part in error.get("loc", ()))
            for error in exc.errors()
            if error.get("type") == "missing"
        ]
        assert any(name.endswith("client_id") or name == "client_id" for name in missing)
    else:
        raise AssertionError("client_id must be required")


def test_feed_request_rejects_unknown_kind_and_user_id() -> None:
    payload = _food_request()
    payload["kind"] = "toy"
    try:
        TypeAdapter(FeedCreateRequest).validate_python(payload)
    except ValidationError:
        pass
    else:
        raise AssertionError("unknown kind must fail")
    payload = _food_request()
    payload["user_id"] = str(uuid4())
    try:
        TypeAdapter(FeedCreateRequest).validate_python(payload)
    except ValidationError:
        return
    raise AssertionError("user_id must be forbidden")


def test_feed_request_locks_five_kinds() -> None:
    TypeAdapter(FeedCreateRequest).validate_python(_food_request())
    TypeAdapter(FeedCreateRequest).validate_python(
        {"client_id": str(uuid4()), "kind": "knowledge", "payload": {"text": "圆周率"}}
    )
    TypeAdapter(FeedCreateRequest).validate_python(
        {
            "client_id": str(uuid4()),
            "kind": "emotion",
            "payload": {"emotion": "calm"},
        }
    )
    TypeAdapter(FeedCreateRequest).validate_python(
        {
            "client_id": str(uuid4()),
            "kind": "promise",
            "payload": {"text": "明天运动", "remind_at": "2026-09-08T12:00:00Z"},
        }
    )
    TypeAdapter(FeedCreateRequest).validate_python(
        {"client_id": str(uuid4()), "kind": "sight", "payload": {"source": "photo"}}
    )
    TypeAdapter(FeedCreateRequest).validate_python(
        {
            "client_id": str(uuid4()),
            "kind": "sight",
            "payload": {
                "source": "location",
                "label": "外滩",
                "city": "上海",
                "category": "landmark",
            },
        }
    )


def test_knowledge_http_payload_allows_10000_and_rejects_10001() -> None:
    client_id = str(uuid4())
    TypeAdapter(FeedCreateRequest).validate_python(
        {"client_id": client_id, "kind": "knowledge", "payload": {"text": "字" * 10000}}
    )
    try:
        TypeAdapter(FeedCreateRequest).validate_python(
            {"client_id": client_id, "kind": "knowledge", "payload": {"text": "字" * 10001}}
        )
    except ValidationError:
        return
    raise AssertionError("knowledge text longer than 10000 must fail")


def test_openapi_post_feed_locks_discriminator_quota_and_patch() -> None:
    spec = create_app(Settings(app_env="test")).openapi()
    path = spec["paths"]["/api/v1/feed"]["post"]
    responses = path["responses"]
    assert "200" in responses
    assert "401" in responses
    assert "404" in responses
    assert "422" in responses
    assert "429" in responses
    assert "QUOTA_EXCEEDED" in responses["429"].get("description", "")
    assert "INVALID_INPUT" in responses["422"].get("description", "")
    request_schema = path["requestBody"]["content"]["application/json"]["schema"]
    resolved = _resolve_schema(spec, request_schema)
    kinds = _discriminator_kinds(spec, resolved)
    assert kinds == {"food", "knowledge", "emotion", "promise", "sight"}
    success_schema = responses["200"]["content"]["application/json"]["schema"]
    data_schema = _resolve_property(spec, success_schema, "data")
    names = _schema_property_names(spec, data_schema)
    assert names == {"resource", "patch", "quotas", "events"}
    resource_names = _schema_property_names(spec, _resolve_property(spec, data_schema, "resource"))
    assert resource_names == {"type", "id", "version", "kind", "status", "promise_status"}
    patch_names = _schema_property_names(spec, _resolve_property(spec, data_schema, "patch"))
    assert "room" in patch_names
    assert "spirit" in patch_names
    assert "memories_upsert" in patch_names
    room = _resolve_property(spec, _resolve_property(spec, data_schema, "patch"), "room")
    assert "weather" in _schema_property_names(spec, room)


def test_app_serves_post_feed() -> None:
    client = TestClient(create_app(Settings(app_env="test")))
    response = client.post("/api/v1/feed", json=_food_request())
    assert response.status_code == 401


def _schema_property_names(spec: dict[str, Any], schema: dict[str, Any]) -> set[str]:
    resolved = _resolve_schema(spec, schema)
    names = set(resolved.get("properties", {}))
    for item in resolved.get("allOf", []):
        if isinstance(item, dict):
            names |= _schema_property_names(spec, item)
    one_of = resolved.get("oneOf") or resolved.get("anyOf")
    if isinstance(one_of, list):
        for item in one_of:
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


def _discriminator_kinds(spec: dict[str, Any], schema: dict[str, Any]) -> set[str]:
    mapping = schema.get("discriminator", {}).get("mapping")
    if isinstance(mapping, dict) and mapping:
        return set(mapping)
    kinds: set[str] = set()
    for key in ("oneOf", "anyOf"):
        options = schema.get(key)
        if not isinstance(options, list):
            continue
        for option in options:
            if not isinstance(option, dict):
                continue
            resolved = _resolve_schema(spec, option)
            kind = resolved.get("properties", {}).get("kind", {})
            enum = _resolve_schema(spec, kind).get("enum") if isinstance(kind, dict) else None
            if isinstance(enum, list):
                kinds.update(str(item) for item in enum)
            const = kind.get("const") if isinstance(kind, dict) else None
            if const is not None:
                kinds.add(str(const))
    return kinds


def _resolve_schema(spec: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        node: Any = spec
        for part in ref[2:].split("/"):
            node = node[part]
        if isinstance(node, dict):
            return _resolve_schema(spec, node)
    return schema
