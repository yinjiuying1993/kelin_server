"""P13-T01 producer contract: clients never submit growth deltas. Spec §8.5."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.contracts.openapi import export_openapi
from app.core.config import Settings
from app.domain.growth import CLIENT_DELTA_FIELD_NAMES, GROWTH_PAYLOAD_FIELDS
from app.main import create_app
from app.schemas.chat import ChatRequest
from app.schemas.extract import ExtractRequest
from app.schemas.feed import FeedCreateRequest, PromiseActionRequest, PromisePatchRequest
from app.schemas.memory import MemoryClearRequest, MemoryDeleteRequest, MemoryPatchRequest
from app.schemas.moderate import ModerateSightRequest
from app.schemas.onboarding import CompleteOnboardingRequest
from app.schemas.pact import (
    CreatePactRequest,
    PactAnswerRequest,
    PactSessionRequest,
    PactSkipRequest,
)
from app.schemas.recall import RecallRequest
from app.schemas.social_api import AddFriendRequest, PostcardReadRequest, RemoveFriendRequest
from app.schemas.speech import SynthesizeRequest
from app.schemas.spirit import CreateSpiritRequest
from app.schemas.storage import SightUploadUrlRequest
from pydantic import TypeAdapter

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
_CLIENT_GROWTH_FIELDS = GROWTH_PAYLOAD_FIELDS | {
    "hunger",
    "energy",
    "mood",
    "bond",
    "closeness",
    "curiosity",
    "sharpness",
    "nocturnal",
    "stubborn",
    "stage",
    "scholar_marks",
}
_REQUEST_MODELS = (
    ChatRequest,
    ExtractRequest,
    FeedCreateRequest,
    PromiseActionRequest,
    PromisePatchRequest,
    ModerateSightRequest,
    SightUploadUrlRequest,
    SynthesizeRequest,
    MemoryPatchRequest,
    MemoryDeleteRequest,
    MemoryClearRequest,
    CreateSpiritRequest,
    CompleteOnboardingRequest,
    CreatePactRequest,
    PactSessionRequest,
    PactAnswerRequest,
    PactSkipRequest,
    AddFriendRequest,
    RemoveFriendRequest,
    PostcardReadRequest,
    RecallRequest,
)


def _schema_property_names(schema: dict[str, Any], components: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    pending: list[dict[str, Any]] = [schema]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)
        ref = current.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            name = ref.rsplit("/", 1)[-1]
            resolved = components.get("schemas", {}).get(name)
            if isinstance(resolved, dict):
                pending.append(resolved)
            continue
        properties = current.get("properties")
        if isinstance(properties, dict):
            for key, child in properties.items():
                names.add(str(key))
                if isinstance(child, dict):
                    pending.append(child)
        for bucket in ("oneOf", "anyOf", "allOf"):
            items = current.get(bucket)
            if isinstance(items, list):
                pending.extend(item for item in items if isinstance(item, dict))
        items = current.get("items")
        if isinstance(items, dict):
            pending.append(items)
    return names


def test_mutation_request_models_have_no_client_delta() -> None:
    for model in _REQUEST_MODELS:
        schema = TypeAdapter(model).json_schema()
        names = _schema_property_names(schema, {"schemas": schema.get("$defs", {})})
        leaked = names & _CLIENT_GROWTH_FIELDS
        assert not leaked, f"{getattr(model, '__name__', model)} must not accept {sorted(leaked)}"


def test_openapi_request_bodies_have_no_client_delta() -> None:
    exported = export_openapi(create_app(Settings(app_env="test")))
    components = exported.document.get("components", {})
    leaked: list[str] = []
    for path, item in exported.document.get("paths", {}).items():
        if not isinstance(item, dict):
            continue
        for method, operation in item.items():
            if method not in {"post", "patch", "put", "delete"} or not isinstance(operation, dict):
                continue
            body = operation.get("requestBody")
            if not isinstance(body, dict):
                continue
            content = body.get("content", {}).get("application/json", {})
            schema = content.get("schema")
            if not isinstance(schema, dict):
                continue
            names = _schema_property_names(schema, components)
            bad = names & _CLIENT_GROWTH_FIELDS
            if bad:
                leaked.append(f"{method.upper()} {path}: {sorted(bad)}")
    assert leaked == []


def test_t01_does_not_change_openapi_sha() -> None:
    exported = export_openapi(create_app(Settings(app_env="test")))
    manifest = json.loads((_FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    assert exported.sha256 == manifest["openapi_sha256"]
    assert exported.sha256 == "fed40c13c30d2203f20ed83c7ad208042c13f35e330d3491e407f22be0593230"


def test_client_delta_field_names_cover_payload_keys() -> None:
    assert GROWTH_PAYLOAD_FIELDS <= CLIENT_DELTA_FIELD_NAMES
