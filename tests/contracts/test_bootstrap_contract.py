from __future__ import annotations

from typing import Any

import pytest
from app.core.config import Settings
from app.main import create_app
from app.schemas.bootstrap import BootstrapSnapshot
from app.schemas.report import ReportSnapshot
from fastapi.testclient import TestClient
from pydantic import ValidationError


def test_openapi_get_bootstrap_locks_p0_fields() -> None:
    spec = create_app(Settings(app_env="test")).openapi()
    path = spec["paths"]["/api/v1/bootstrap"]["get"]
    assert "requestBody" not in path
    responses = path["responses"]
    assert "200" in responses
    assert "401" in responses
    assert "503" in responses
    success_schema = responses["200"]["content"]["application/json"]["schema"]
    data_schema = _resolve_property(spec, success_schema, "data")
    names = _schema_property_names(spec, data_schema)
    assert names == {
        "schema_version",
        "snapshot_version",
        "api_version",
        "spirit",
        "room",
        "onboarding",
        "latest_memories",
        "unread_postcards",
        "active_pact",
        "report",
        "quotas",
        "feature_flags",
    }
    assert "server_time" not in names
    room = _resolve_property(spec, data_schema, "room")
    assert "unread_footprint_count" in _schema_property_names(spec, room)
    assert "pending_sight" in _schema_property_names(spec, room)
    onboarding = _resolve_property(spec, data_schema, "onboarding")
    assert "step" in _schema_property_names(spec, onboarding)
    report = _resolve_property(spec, data_schema, "report")
    report_names = _schema_property_names(spec, report)
    assert {"status", "report_id", "eligibility", "card"} <= report_names
    flags = _resolve_property(spec, data_schema, "feature_flags")
    assert _schema_property_names(spec, flags) == {"remote_search", "image_feed"}
    quotas = _resolve_property(spec, data_schema, "quotas")
    assert quotas.get("type") == "array" or "items" in quotas


def test_success_fixture_matches_bootstrap_snapshot() -> None:
    from pathlib import Path

    from app.contracts.fixtures import BOOTSTRAP_ENTRY_ID, load_success

    root = Path(__file__).resolve().parents[2] / "fixtures"
    loaded = load_success(root, BOOTSTRAP_ENTRY_ID)
    snapshot = BootstrapSnapshot.model_validate(loaded.data)
    assert snapshot.schema_version == 2
    assert snapshot.api_version == "v1"
    assert snapshot.spirit is None
    assert snapshot.room is None
    assert snapshot.onboarding.required is True
    assert snapshot.onboarding.step == 0
    assert snapshot.latest_memories == []
    assert snapshot.unread_postcards == []
    assert snapshot.active_pact is None
    report = ReportSnapshot.model_validate(snapshot.report.model_dump(mode="json"))
    assert report.status == "locked"
    assert report.card is None
    assert snapshot.feature_flags.remote_search is True
    assert snapshot.feature_flags.image_feed is True
    assert "server_time" not in snapshot.model_dump()


def test_snapshot_rejects_room_without_spirit() -> None:
    payload = _new_user_snapshot()
    payload["room"] = {
        "weather": "cloudy",
        "layers": [],
        "letter": None,
        "pending_sight": None,
        "unread_footprint_count": 0,
        "updated_at": "2026-09-08T00:00:00Z",
    }
    with pytest.raises(ValidationError):
        BootstrapSnapshot.model_validate(payload)


def test_locked_report_rejects_report_id() -> None:
    payload = _new_user_snapshot()
    payload["report"]["report_id"] = "00000000-0000-4000-8000-000000000030"
    with pytest.raises(ValidationError):
        BootstrapSnapshot.model_validate(payload)


def test_get_bootstrap_without_auth_is_unauthenticated() -> None:
    client = TestClient(create_app(Settings(app_env="test")))
    response = client.get("/api/v1/bootstrap")
    assert response.status_code == 401
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "UNAUTHENTICATED"


def _new_user_snapshot() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "snapshot_version": 0,
        "api_version": "v1",
        "spirit": None,
        "room": None,
        "onboarding": {
            "required": True,
            "step": 0,
            "total_steps": 5,
            "completed_at": None,
        },
        "latest_memories": [],
        "unread_postcards": [],
        "active_pact": None,
        "report": {
            "status": "locked",
            "report_id": None,
            "eligibility": {
                "is_eligible": False,
                "eligible_at": "2026-09-14T03:46:00Z",
                "days_remaining": 7,
                "required_dialogue_rounds": 10,
                "completed_dialogue_rounds": 0,
                "dialogue_rounds_remaining": 10,
            },
            "card": None,
        },
        "quotas": [],
        "feature_flags": {"remote_search": True, "image_feed": True},
    }


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
