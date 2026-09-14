from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.core.config import Settings
from app.domain.speech import m4a_fixture_bytes
from app.main import create_app
from app.schemas.speech import SynthesizeRequest, TranscribeForm, TranscribeResult
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError


def _synthesize() -> dict[str, Any]:
    return {
        "client_id": str(uuid4()),
        "message_id": str(uuid4()),
        "voice_profile": "default",
    }


def test_synthesize_request_forbids_text_and_non_default_voice() -> None:
    payload = _synthesize()
    payload["text"] = "绕过正文"
    try:
        TypeAdapter(SynthesizeRequest).validate_python(payload)
    except ValidationError:
        pass
    else:
        raise AssertionError("clients must not send tts text")
    payload = _synthesize()
    payload["voice_profile"] = "warm"
    try:
        TypeAdapter(SynthesizeRequest).validate_python(payload)
    except ValidationError:
        return
    raise AssertionError("voice_profile must be default only")


def test_transcribe_form_requires_client_id() -> None:
    try:
        TypeAdapter(TranscribeForm).validate_python({"audio": "clip.m4a"})
    except ValidationError as exc:
        missing = [
            ".".join(str(part) for part in error.get("loc", ()))
            for error in exc.errors()
            if error.get("type") == "missing"
        ]
        assert "client_id" in missing
    else:
        raise AssertionError("client_id must be required")


def test_openapi_speech_locks_multipart_quota_empty_and_cache() -> None:
    spec = create_app(Settings(app_env="test")).openapi()
    transcribe = spec["paths"]["/api/v1/transcribe"]["post"]
    synthesize = spec["paths"]["/api/v1/synthesize"]["post"]
    transcribe_body = transcribe["requestBody"]["content"]
    assert "multipart/form-data" in transcribe_body
    assert "application/json" not in transcribe_body
    form_schema = _resolve_schema(spec, transcribe_body["multipart/form-data"]["schema"])
    assert set(form_schema.get("required", [])) == {"client_id", "audio"}
    assert set(form_schema.get("properties", {})) == {"client_id", "audio"}
    transcribe_text = (transcribe.get("description") or "") + (transcribe.get("summary") or "")
    audio_desc = str(_resolve_property(spec, form_schema, "audio"))
    locked = transcribe_text + audio_desc
    assert "5 MiB" in locked
    assert "30" in locked
    assert "ASR_EMPTY_RESULT" in locked
    responses = transcribe["responses"]
    assert "200" in responses
    assert "401" in responses
    assert "422" in responses
    assert "429" in responses
    assert "503" in responses
    assert "504" in responses
    assert "ASR_EMPTY_RESULT" in responses["422"].get("description", "")
    assert "QUOTA_EXCEEDED" in responses["429"].get("description", "")
    assert "MODEL_UNAVAILABLE" in responses["503"].get("description", "")
    assert "PROVIDER_TIMEOUT" in responses["504"].get("description", "")
    success = responses["200"]["content"]["application/json"]["schema"]
    data = _resolve_property(spec, success, "data")
    resource = _resolve_property(spec, data, "resource")
    names = _schema_property_names(spec, resource)
    assert names >= {"text", "duration_ms", "language", "provider_request_id", "type"}
    synth_schema = synthesize["requestBody"]["content"]["application/json"]["schema"]
    resolved_synth = _resolve_schema(spec, synth_schema)
    assert set(resolved_synth.get("required", [])) >= {"client_id", "message_id"}
    assert "text" not in resolved_synth.get("properties", {})
    voice = _resolve_property(spec, synth_schema, "voice_profile")
    assert voice.get("const") == "default" or set(voice.get("enum", [])) == {"default"}
    synth_desc = (synthesize.get("description") or "") + (synthesize.get("summary") or "")
    assert "24h" in synth_desc or "24" in synth_desc
    assert "200" in synth_desc
    synth_responses = synthesize["responses"]
    assert "404" in synth_responses
    assert "NOT_FOUND" in synth_responses["404"].get("description", "")
    assert "QUOTA_EXCEEDED" in synth_responses["429"].get("description", "")
    synth_data = _resolve_property(
        spec, synth_responses["200"]["content"]["application/json"]["schema"], "data"
    )
    synth_resource = _resolve_property(spec, synth_data, "resource")
    synth_names = _schema_property_names(spec, synth_resource)
    assert synth_names >= {"audio_url", "mime", "duration_ms", "expires_at", "cache_hit"}


def test_success_fixture_returns_asr_quota() -> None:
    from pathlib import Path

    from app.contracts.fixtures import TRANSCRIBE_ENTRY_ID, load_success

    root = Path(__file__).resolve().parents[2] / "fixtures"
    loaded = load_success(root, TRANSCRIBE_ENTRY_ID)
    result = TranscribeResult.model_validate(loaded.data)
    assert result.resource.text == "你好"
    assert result.quotas[0].capability == "asr"
    assert result.quotas[0].limit == 60


def test_speech_without_auth_is_unauthenticated() -> None:
    client = TestClient(create_app(Settings(app_env="test")))
    clip = m4a_fixture_bytes(duration_ms=900)
    transcribe = client.post(
        "/api/v1/transcribe",
        data={"client_id": str(uuid4())},
        files={"audio": ("clip.m4a", clip, "audio/mp4")},
    )
    assert transcribe.status_code == 401
    assert transcribe.json()["error"]["code"] == "UNAUTHENTICATED"
    synthesize = client.post("/api/v1/synthesize", json=_synthesize())
    assert synthesize.status_code == 401
    assert synthesize.json()["error"]["code"] == "UNAUTHENTICATED"


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
