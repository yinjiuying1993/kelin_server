from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.core.config import Settings
from app.main import create_app
from app.schemas.pact import CreatePactRequest, PactAnswerRequest, PactAnswerResource
from fastapi.testclient import TestClient
from pydantic import ValidationError

_PACT_PATHS = (
    "/api/v1/pacts",
    "/api/v1/pact-session",
    "/api/v1/pact-answer",
    "/api/v1/pact-skip",
)


def _answer_body() -> dict[str, Any]:
    return {
        "client_id": str(uuid4()),
        "session_id": str(uuid4()),
        "expected_session_version": 1,
        "question_id": "interview-v1-q1",
        "text": "我想加入因为方向匹配。",
    }


def test_answer_request_forbids_score_mark_and_finalized() -> None:
    for extra in ("score", "mark", "finalized", "completeness"):
        payload = _answer_body()
        payload[extra] = 1 if extra != "finalized" else True
        try:
            PactAnswerRequest.model_validate(payload)
        except ValidationError:
            continue
        raise AssertionError(f"pact-answer must forbid client field {extra}")


def test_create_request_forbids_score() -> None:
    payload = {
        "client_id": str(uuid4()),
        "theme": "interview",
        "title": "面试准备",
        "score": 90,
    }
    try:
        CreatePactRequest.model_validate(payload)
    except ValidationError:
        return
    raise AssertionError("create pact must forbid score")


def test_finalized_resource_requires_score_and_pact_status() -> None:
    payload = {
        "type": "pact_answer",
        "session_id": str(uuid4()),
        "question_id": "interview-v1-q3",
        "feedback": {"summary": "本场结束。", "improvements": []},
        "answered_count": 3,
        "row_version": 4,
        "finalized": True,
        "score": None,
        "pact_status": None,
        "mistakes": [],
    }
    try:
        PactAnswerResource.model_validate(payload)
    except ValidationError:
        return
    raise AssertionError("finalized answers must require score and pact_status")


def test_openapi_locks_four_pact_routes_and_forbids_complete() -> None:
    spec = create_app(Settings(app_env="test")).openapi()
    paths = spec["paths"]
    for path in _PACT_PATHS:
        assert path in paths
        assert "post" in paths[path]
    pact_paths = [path for path in paths if "pact" in path]
    assert pact_paths
    assert not any("complete" in path for path in pact_paths)
    assert "/api/v1/pacts/{id}/complete" not in paths
    assert "/api/v1/pacts/{pact_id}/complete" not in paths
    complete_paths = [path for path in paths if "complete" in path]
    assert complete_paths == [
        "/api/v1/feeds/{feed_id}/complete",
        "/api/v1/onboarding/complete",
    ]
    pacts = paths["/api/v1/pacts"]["post"]
    pacts_desc = (pacts.get("description") or "") + (pacts.get("summary") or "")
    assert "PACT_ALREADY_ACTIVE" in pacts_desc
    assert "complete" in pacts_desc
    request_schema = _resolve_schema(
        spec, pacts["requestBody"]["content"]["application/json"]["schema"]
    )
    assert set(request_schema.get("required", [])) >= {"client_id", "theme", "title"}
    assert "score" not in request_schema.get("properties", {})
    assert request_schema.get("additionalProperties") is False
    answer = paths["/api/v1/pact-answer"]["post"]
    answer_desc = (answer.get("description") or "") + (answer.get("summary") or "")
    assert "finalized" in answer_desc
    assert "PACT_QUESTION_ALREADY_ANSWERED" in answer_desc
    assert "/pacts/{id}/complete" in answer_desc
    answer_req = _resolve_schema(
        spec, answer["requestBody"]["content"]["application/json"]["schema"]
    )
    assert set(answer_req.get("required", [])) == {
        "client_id",
        "session_id",
        "expected_session_version",
        "question_id",
        "text",
    }
    assert "score" not in answer_req.get("properties", {})
    assert "finalized" not in answer_req.get("properties", {})
    success = answer["responses"]["200"]["content"]["application/json"]["schema"]
    data = _resolve_property(spec, success, "data")
    resource = _resolve_property(spec, data, "resource")
    names = _schema_property_names(spec, resource)
    assert names >= {
        "finalized",
        "score",
        "pact_status",
        "mistakes",
        "answered_count",
        "row_version",
        "feedback",
        "question_id",
    }
    session = paths["/api/v1/pact-session"]["post"]
    session_desc = (session.get("description") or "") + (session.get("summary") or "")
    assert "row_version" in session_desc
    assert "3" in session_desc
    skip = paths["/api/v1/pact-skip"]["post"]
    skip_desc = (skip.get("description") or "") + (skip.get("summary") or "")
    assert "PACT_SESSION_ALREADY_SUBMITTED" in skip_desc


def test_pact_routes_without_auth_are_unauthenticated() -> None:
    client = TestClient(create_app(Settings(app_env="test")))
    bodies: dict[str, dict[str, Any]] = {
        "/api/v1/pacts": {
            "client_id": str(uuid4()),
            "theme": "interview",
            "title": "面试准备",
        },
        "/api/v1/pact-session": {
            "client_id": str(uuid4()),
            "pact_id": str(uuid4()),
        },
        "/api/v1/pact-answer": _answer_body(),
        "/api/v1/pact-skip": {
            "client_id": str(uuid4()),
            "pact_id": str(uuid4()),
            "session_date": "2026-09-08",
        },
    }
    for path, body in bodies.items():
        response = client.post(path, json=body)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHENTICATED"


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
