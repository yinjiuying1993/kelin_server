"""P16-T09 static review: privacy, no client visit, scheduler stays out of API."""

from __future__ import annotations

import json
from inspect import getsource
from pathlib import Path

from app.contracts.openapi import export_openapi
from app.core.config import Settings
from app.core.logging import redact_event_dict
from app.main import AccessLogMiddleware, _lifespan, create_app
from app.providers.postcard import postcard_input_from_public
from app.schemas.social_api import PRIVATE_SOCIAL_FIELD_NAMES, PUBLIC_PROFILE_FIELD_NAMES

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
SOCIAL = ROOT / "app" / "api" / "v1" / "social.py"
_OPENAPI_SHA = "fed40c13c30d2203f20ed83c7ad208042c13f35e330d3491e407f22be0593230"
_SOCIAL_FIXTURE_DIRS = (
    "GET_api_v1_friends",
    "POST_api_v1_friends",
    "DELETE_api_v1_friends_{friend_id}",
    "GET_api_v1_postcards",
    "PATCH_api_v1_postcards_{postcard_id}_read",
)
_SCHEDULER_ENTRYPOINTS = (
    "scan_due_visit_plan_jobs",
    "process_due_visit_plans",
    "scan_due_visit_settle_jobs",
    "process_due_visit_settles",
    "handle_visit_plan",
    "handle_visit_settle",
)


def _walk_keys(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        found.update(str(key) for key in value)
        for item in value.values():
            found.update(_walk_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_walk_keys(item))
    return found


def test_t09_openapi_sha_and_no_client_visit() -> None:
    exported = export_openapi(create_app(Settings(app_env="test")))
    assert exported.sha256 == _OPENAPI_SHA
    paths = create_app(Settings(app_env="test")).openapi()["paths"]
    assert "/api/v1/friends" in paths
    assert "/api/v1/postcards" in paths
    assert not any("visit" in path for path in paths)
    boot = getsource(create_app) + getsource(_lifespan)
    for name in _SCHEDULER_ENTRYPOINTS:
        assert name not in boot
    social = SOCIAL.read_text(encoding="utf-8")
    assert "handle_visit_settle" not in social
    assert "handle_visit_plan" not in social
    assert "plan_visits" not in social


def test_t09_fixtures_and_provider_input_have_no_private_fields() -> None:
    for folder in _SOCIAL_FIXTURE_DIRS:
        for path in (FIXTURES / folder).glob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            blob = json.dumps(payload, ensure_ascii=False)
            keys = _walk_keys(payload)
            assert keys.isdisjoint(PRIVATE_SOCIAL_FIELD_NAMES), path.name
            assert "记忆" not in blob
            assert "对话" not in blob
            assert "latitude" not in blob
            assert "eyJ" not in blob
    dumped = postcard_input_from_public(
        role="visitor",
        title="甲居",
        stage="whelp",
        weather="cloudy",
        marks=["interview-v1"],
        counterpart_title="串门客",
        counterpart_stage="whelp",
        counterpart_weather="cloudy",
        counterpart_marks=[],
    ).model_dump(mode="json")
    assert set(dumped["subject"]) <= {"title", "stage", "weather", "public_marks"}
    assert _walk_keys(dumped).isdisjoint(PRIVATE_SOCIAL_FIELD_NAMES)
    assert PUBLIC_PROFILE_FIELD_NAMES == {"id", "title", "stage", "public_marks", "status"}
    access = getsource(AccessLogMiddleware)
    assert "user_id_hash" in access
    assert "prompt" not in access.lower()
    assert "postcard" not in access
    redacted = redact_event_dict({"latitude": "1.2", "route": "/api/v1/friends"})
    assert redacted["latitude"] == "[redacted]"
    assert redacted["route"] == "/api/v1/friends"
