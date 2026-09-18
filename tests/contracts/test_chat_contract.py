from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.core.config import Settings
from app.main import create_app
from app.schemas.chat import ChatRequest, ChatTurnResult
from fastapi.testclient import TestClient
from pydantic import ValidationError


def _valid_request(*, onboarding: bool = True) -> dict[str, Any]:
    return {
        "client_message_id": str(uuid4()),
        "content": "你好",
        "source": "text",
        "onboarding": onboarding,
        "context": {
            "timezone": "Asia/Shanghai",
            "local_hour": 21,
            "weather": "cloudy",
            "city": None,
        },
    }


def test_chat_request_requires_onboarding() -> None:
    payload = _valid_request()
    del payload["onboarding"]
    try:
        ChatRequest.model_validate(payload)
    except ValidationError as exc:
        missing = [
            ".".join(str(part) for part in error.get("loc", ()))
            for error in exc.errors()
            if error.get("type") == "missing"
        ]
        assert "onboarding" in missing
    else:
        raise AssertionError(
            "onboarding must be required so the server can distinguish hatch turns"
        )


def test_chat_request_accepts_onboarding_false() -> None:
    ChatRequest.model_validate(_valid_request(onboarding=False))


def test_chat_request_rejects_user_id() -> None:
    payload = _valid_request()
    payload["user_id"] = str(uuid4())
    try:
        ChatRequest.model_validate(payload)
    except ValidationError:
        return
    raise AssertionError("chat request must forbid user_id")


def test_openapi_post_chat_locks_onboarding_stub_errors_and_counts() -> None:
    spec = create_app(Settings(app_env="test")).openapi()
    path = spec["paths"]["/api/v1/chat"]["post"]
    request_schema = path["requestBody"]["content"]["application/json"]["schema"]
    required = set(_resolve_schema(spec, request_schema).get("required", []))
    assert "onboarding" in required
    assert "client_message_id" in required
    assert "content" in required
    assert "source" in required
    assert "context" in required
    onboarding = _resolve_property(spec, request_schema, "onboarding")
    assert onboarding.get("type") == "boolean"
    assert "ordinary_dialogue_rounds" in (onboarding.get("description") or "")
    source = _resolve_property(spec, request_schema, "source")
    assert set(source.get("enum", [])) == {"text", "voice", "onboarding"}
    responses = path["responses"]
    assert "200" in responses
    assert "401" in responses
    assert "409" in responses
    assert "422" in responses
    assert "429" in responses
    assert "503" in responses
    assert "504" in responses
    assert "MODEL_UNAVAILABLE" in responses["503"].get("description", "")
    assert "PROVIDER_TIMEOUT" in responses["504"].get("description", "")
    assert "QUOTA_EXCEEDED" in responses["429"].get("description", "")
    assert "RATE_LIMITED" in responses["429"].get("description", "")
    success_schema = responses["200"]["content"]["application/json"]["schema"]
    data_schema = _resolve_property(spec, success_schema, "data")
    resource = _resolve_property(spec, data_schema, "resource")
    resource_names = _schema_property_names(spec, resource)
    assert "onboarding" in resource_names
    assert "generation_source" in resource_names
    assert "user_message" in resource_names
    assert "spirit_message" in resource_names
    assert "conversation_window_id" in resource_names
    assert "should_extract" in resource_names
    assert "speech_audio" in resource_names
    assert "usage" in resource_names
    generation = _resolve_property(spec, resource, "generation_source")
    assert set(generation.get("enum", [])) == {"stub", "provider"}
    assert "live chat provider" in (generation.get("description") or "")
    window = _resolve_property(spec, resource, "conversation_window_id")
    window_description = window.get("description") or ""
    assert "open window" in window_description
    assert "message IDs" in window_description
    extract = _resolve_property(spec, resource, "should_extract")
    assert "ready" in (extract.get("description") or "")
    raw_speech = _raw_property(spec, resource, "speech_audio")
    speech_desc = raw_speech.get("description") or ""
    assert "source=voice" in speech_desc
    assert "null" in speech_desc.lower()
    resource_required = set(_resolve_schema(spec, resource).get("required", []))
    assert "speech_audio" in resource_required
    resource_onboarding = _resolve_property(spec, resource, "onboarding")
    assert "ordinary_dialogue_rounds" in (resource_onboarding.get("description") or "")
    type_schema = _resolve_property(spec, resource, "type")
    assert type_schema.get("const") == "chat_turn" or "chat_turn" in type_schema.get("enum", [])
    description = path.get("description") or path.get("summary") or ""
    assert "PROVIDER_TIMEOUT" in description
    assert "ordinary_dialogue_rounds" in description
    assert "onboarding_step" in description
    assert "speech_audio" in description
    assert "48s" in description


def test_success_fixture_is_stub_onboarding_turn() -> None:
    from pathlib import Path

    from app.contracts.fixtures import CHAT_ENTRY_ID, load_success

    root = Path(__file__).resolve().parents[2] / "fixtures"
    loaded = load_success(root, CHAT_ENTRY_ID)
    result = ChatTurnResult.model_validate(loaded.data)
    assert result.resource.type == "chat_turn"
    assert result.resource.onboarding is True
    assert result.resource.generation_source == "stub"
    assert result.resource.should_extract is False
    assert result.resource.speech_audio is None
    assert result.resource.usage.input_units == 0
    assert result.patch.onboarding is not None
    assert result.patch.onboarding.step == 1
    assert result.patch.spirit is not None
    assert result.patch.spirit.onboarding_step == 1
    assert result.patch.spirit.hatched_at is None


def test_post_chat_without_auth_is_unauthenticated() -> None:
    client = TestClient(create_app(Settings(app_env="test")))
    response = client.post("/api/v1/chat", json=_valid_request())
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


def _raw_property(spec: dict[str, Any], schema: dict[str, Any], name: str) -> dict[str, Any]:
    resolved = _resolve_schema(spec, schema)
    properties = resolved.get("properties", {})
    raw = properties.get(name)
    if isinstance(raw, dict):
        return raw
    raise AssertionError(f"schema property {name} not found")


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
