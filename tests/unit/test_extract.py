from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.domain.extract import (
    MEMORY_CONFIDENCE_FLOOR,
    accepted_memories,
    accumulated_trait_deltas,
    clamp_trait,
    extract_output_hash,
)
from app.main import create_app
from app.providers.errors import ProviderError
from app.providers.extract_schema import require_extract_output
from app.providers.types import ExtractMemoryDraft, ExtractOutput, PersonalityDelta
from app.schemas.extract import ExtractRequest, ExtractResult
from fastapi.testclient import TestClient
from pydantic import ValidationError


def _valid_request() -> dict[str, Any]:
    return {
        "client_id": str(uuid4()),
        "conversation_window_id": str(uuid4()),
    }


def test_extract_request_forbids_message_ids() -> None:
    payload = _valid_request()
    payload["start_message_id"] = str(uuid4())
    try:
        ExtractRequest.model_validate(payload)
    except ValidationError:
        return
    raise AssertionError("extract request must forbid start_message_id")


def test_extract_request_forbids_end_message_id() -> None:
    payload = _valid_request()
    payload["end_message_id"] = str(uuid4())
    try:
        ExtractRequest.model_validate(payload)
    except ValidationError:
        return
    raise AssertionError("extract request must forbid end_message_id")


def test_low_confidence_memories_are_dropped() -> None:
    output = ExtractOutput(
        memories=[
            ExtractMemoryDraft(
                type="preference",
                summary="弱信号",
                salience=10,
                confidence=0.74,
                personality_delta=PersonalityDelta(dimension="closeness", value=2),
            ),
            ExtractMemoryDraft(
                type="preference",
                summary="用户希望被叫阿年",
                salience=90,
                confidence=MEMORY_CONFIDENCE_FLOOR,
                personality_delta=PersonalityDelta(dimension="closeness", value=1),
            ),
        ]
    )
    kept = accepted_memories(output)
    assert len(kept) == 1
    assert kept[0].summary == "用户希望被叫阿年"
    deltas = accumulated_trait_deltas(kept)
    assert deltas["closeness"] == 1
    assert clamp_trait(99, 2) == 100
    assert clamp_trait(1, -2) == 0


def test_extract_output_hash_is_stable() -> None:
    memories = [
        ExtractMemoryDraft(
            type="preference",
            summary="用户希望被叫阿年",
            tags=["称呼"],
            salience=90,
            confidence=0.96,
        )
    ]
    first = extract_output_hash(memories, [])
    second = extract_output_hash(memories, [])
    assert first == second
    assert len(first) == 64


def test_require_extract_output_rejects_extra() -> None:
    with pytest.raises(ProviderError):
        require_extract_output(
            {
                "memories": [
                    {
                        "type": "preference",
                        "summary": "x",
                        "tags": [],
                        "salience": 1,
                        "confidence": 0.9,
                        "extra": True,
                    }
                ]
            }
        )


def test_openapi_post_extract_locks_window_id_and_forbids_message_ids() -> None:
    spec = create_app(Settings(app_env="test")).openapi()
    path = spec["paths"]["/api/v1/extract"]["post"]
    request_schema = path["requestBody"]["content"]["application/json"]["schema"]
    required = set(_resolve_schema(spec, request_schema).get("required", []))
    assert required == {"client_id", "conversation_window_id"}
    properties = set(_resolve_schema(spec, request_schema).get("properties", {}))
    assert "start_message_id" not in properties
    assert "end_message_id" not in properties
    window = _resolve_property(spec, request_schema, "conversation_window_id")
    assert "message IDs" in (window.get("description") or "")
    responses = path["responses"]
    assert "200" in responses
    assert "401" in responses
    assert "409" in responses
    assert "422" in responses
    assert "503" in responses
    assert "504" in responses
    success_schema = responses["200"]["content"]["application/json"]["schema"]
    data_schema = _resolve_property(spec, success_schema, "data")
    resource = _resolve_property(spec, data_schema, "resource")
    names = _schema_property_names(spec, resource)
    assert "conversation_window_id" in names
    assert "status" in names
    assert "memories" in names
    assert "style_samples" in names
    type_schema = _resolve_property(spec, resource, "type")
    assert type_schema.get("const") == "extract" or "extract" in type_schema.get("enum", [])


def test_success_fixture_is_extracted_window() -> None:
    from pathlib import Path

    from app.contracts.fixtures import EXTRACT_ENTRY_ID, load_success

    root = Path(__file__).resolve().parents[2] / "fixtures"
    loaded = load_success(root, EXTRACT_ENTRY_ID)
    result = ExtractResult.model_validate(loaded.data)
    assert result.resource.type == "extract"
    assert result.resource.status == "extracted"
    assert 0 <= len(result.resource.memories) <= 2
    assert 0 <= len(result.resource.style_samples) <= 1
    assert result.patch.spirit is not None
    assert result.patch.spirit.closeness == 66


def test_post_extract_without_auth_is_unauthenticated() -> None:
    client = TestClient(create_app(Settings(app_env="test")))
    response = client.post("/api/v1/extract", json=_valid_request())
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
