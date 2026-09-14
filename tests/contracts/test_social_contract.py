from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.core.config import Settings
from app.main import create_app
from app.schemas.social_api import (
    PRIVATE_SOCIAL_FIELD_NAMES,
    PUBLIC_PROFILE_FIELD_NAMES,
    PUBLIC_VISIT_CONTEXT_FIELD_NAMES,
    AddFriendRequest,
    NpcPublicProfile,
    PostcardReadRequest,
    PublicSpiritProfile,
    PublicVisitContext,
    RemoveFriendRequest,
    VisitPlanPublic,
    VisitPublic,
)
from fastapi.testclient import TestClient
from pydantic import ValidationError

_SOCIAL_PATHS = (
    "/api/v1/friends",
    "/api/v1/friends/{friend_id}",
    "/api/v1/postcards",
    "/api/v1/postcards/{postcard_id}/read",
)
_CLIENT_VISIT_PATH_NEEDLES = (
    "/api/v1/visits",
    "/api/v1/visit",
    "/api/v1/visit-plan",
    "/api/v1/visit-settle",
)


def test_openapi_locks_social_routes_and_forbids_client_visit() -> None:
    spec = create_app(Settings(app_env="test")).openapi()
    paths = spec["paths"]
    for path in _SOCIAL_PATHS:
        assert path in paths
    friends = paths["/api/v1/friends"]
    assert "get" in friends and "post" in friends
    assert "delete" in paths["/api/v1/friends/{friend_id}"]
    assert "get" in paths["/api/v1/postcards"]
    assert "patch" in paths["/api/v1/postcards/{postcard_id}/read"]
    for needle in _CLIENT_VISIT_PATH_NEEDLES:
        assert not any(needle == path or path.startswith(f"{needle}/") for path in paths)
    add_desc = (friends["post"].get("description") or "") + (friends["post"].get("summary") or "")
    assert "SELF_FRIEND_NOT_ALLOWED" in add_desc
    assert "invite_code" in add_desc
    add_req = _resolve_schema(
        spec, friends["post"]["requestBody"]["content"]["application/json"]["schema"]
    )
    assert set(add_req.get("required", [])) >= {"client_id", "invite_code"}
    assert add_req.get("additionalProperties") is False
    assert "host_spirit_id" not in add_req.get("properties", {})
    assert "npc_id" not in add_req.get("properties", {})
    get_desc = (friends["get"].get("description") or "") + (friends["get"].get("summary") or "")
    assert "INVALID_CURSOR" in get_desc
    read_desc = (
        paths["/api/v1/postcards/{postcard_id}/read"]["patch"].get("description") or ""
    ) + (paths["/api/v1/postcards/{postcard_id}/read"]["patch"].get("summary") or "")
    assert "NOT_FOUND" in read_desc
    friend_page = _resolve_property(
        spec,
        friends["get"]["responses"]["200"]["content"]["application/json"]["schema"],
        "data",
    )
    item = _resolve_schema(spec, friend_page["properties"]["items"]["items"])
    spirit = _resolve_schema(spec, item["properties"]["spirit"])
    assert set(spirit.get("properties", {})) == PUBLIC_PROFILE_FIELD_NAMES
    postcard_page = _resolve_property(
        spec,
        paths["/api/v1/postcards"]["get"]["responses"]["200"]["content"]["application/json"][
            "schema"
        ],
        "data",
    )
    postcard = _resolve_schema(spec, postcard_page["properties"]["items"]["items"])
    names = _schema_property_names(spec, postcard)
    leaked = names & PRIVATE_SOCIAL_FIELD_NAMES
    assert not leaked
    visit = _resolve_schema(spec, postcard["properties"]["visit"])
    assert "destination_index" in visit.get("properties", {})
    context = _resolve_schema(spec, visit["properties"]["public_context"])
    assert set(context.get("properties", {})) == PUBLIC_VISIT_CONTEXT_FIELD_NAMES


def test_social_routes_without_auth_are_unauthenticated() -> None:
    client = TestClient(create_app(Settings(app_env="test")))
    friend_id = str(uuid4())
    postcard_id = str(uuid4())
    calls: tuple[tuple[str, str, dict[str, Any] | None], ...] = (
        ("get", "/api/v1/friends", None),
        (
            "post",
            "/api/v1/friends",
            {"client_id": str(uuid4()), "invite_code": "ABCD2345"},
        ),
        (
            "delete",
            f"/api/v1/friends/{friend_id}",
            {"client_id": str(uuid4())},
        ),
        ("get", "/api/v1/postcards", None),
        (
            "patch",
            f"/api/v1/postcards/{postcard_id}/read",
            {"client_id": str(uuid4())},
        ),
    )
    for method, path, body in calls:
        kwargs: dict[str, Any] = {}
        if body is not None:
            kwargs["json"] = body
        response = client.request(method.upper(), path, **kwargs)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_add_friend_normalizes_invite_code() -> None:
    body = AddFriendRequest.model_validate({"client_id": str(uuid4()), "invite_code": " abcd2345 "})
    assert body.invite_code == "ABCD2345"
    try:
        AddFriendRequest.model_validate({"client_id": str(uuid4()), "invite_code": "IIII2345"})
    except ValidationError:
        return
    raise AssertionError("malformed invite_code must be rejected")


def test_public_models_reject_private_fields() -> None:
    profile = {
        "id": str(uuid4()),
        "title": "阴天收集者",
        "stage": "formed",
        "public_marks": ["interview-v1"],
        "status": "home",
    }
    PublicSpiritProfile.model_validate(profile)
    NpcPublicProfile.model_validate({"npc_id": "fog", "title": "雾里的那只", "public_marks": []})
    for leaked in ("user_id", "latitude", "prompt", "bond"):
        payload = dict(profile)
        payload[leaked] = "nope" if leaked != "bond" else 1
        try:
            PublicSpiritProfile.model_validate(payload)
        except ValidationError:
            continue
        raise AssertionError(f"public spirit profile must forbid {leaked}")
    try:
        PublicVisitContext.model_validate(
            {
                "title": "阴天收集者",
                "stage": "formed",
                "weather": "cloudy",
                "public_marks": [],
                "user_id": str(uuid4()),
            }
        )
    except ValidationError:
        return
    raise AssertionError("visit context must forbid user_id")


def test_visit_plan_allows_zero_one_two_destinations() -> None:
    plan_id = uuid4()
    VisitPlanPublic.model_validate({"plan_id": str(plan_id), "visits": []})
    one = _visit(plan_id, 1, host=True)
    VisitPlanPublic.model_validate({"plan_id": str(plan_id), "visits": [one]})
    two = [_visit(plan_id, 1, host=True), _visit(plan_id, 2, host=False)]
    VisitPlanPublic.model_validate({"plan_id": str(plan_id), "visits": two})
    try:
        VisitPlanPublic.model_validate(
            {
                "plan_id": str(plan_id),
                "visits": [
                    _visit(plan_id, 1, host=True),
                    _visit(plan_id, 2, host=False),
                    _visit(plan_id, 1, host=True),
                ],
            }
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("visit plan must reject more than two destinations")
    try:
        VisitPublic.model_validate(_visit(plan_id, 1, host=True, both=True))
    except ValidationError:
        return
    raise AssertionError("visit must not accept host and npc together")


def test_remove_and_read_requests_only_take_client_id() -> None:
    RemoveFriendRequest.model_validate({"client_id": str(uuid4())})
    PostcardReadRequest.model_validate({"client_id": str(uuid4())})
    try:
        RemoveFriendRequest.model_validate(
            {"client_id": str(uuid4()), "host_spirit_id": str(uuid4())}
        )
    except ValidationError:
        return
    raise AssertionError("remove friend must forbid host_spirit_id")


def _visit(
    plan_id: Any,
    index: int,
    *,
    host: bool,
    both: bool = False,
) -> dict[str, Any]:
    profile = {
        "id": str(uuid4()),
        "title": "阴天收集者",
        "stage": "formed",
        "public_marks": [],
        "status": "home",
    }
    npc = {"npc_id": "fog", "title": "雾里的那只", "public_marks": []}
    payload: dict[str, Any] = {
        "type": "visit",
        "id": str(uuid4()),
        "plan_id": str(plan_id),
        "destination_index": index,
        "status": "settled",
        "host": profile if host or both else None,
        "npc": npc if (not host) or both else None,
        "public_context": {
            "title": "阴天收集者",
            "stage": "formed",
            "weather": "cloudy",
            "public_marks": [],
        },
    }
    return payload


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
