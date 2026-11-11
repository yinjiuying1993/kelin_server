from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from app.contracts.fixtures import (
    BASE_SCENARIOS,
    ENVELOPE_ENTRY_ID,
    LIST_PAGE_SCENARIOS,
    FixtureMissingError,
    FixtureValidationError,
    assert_manifest_matches_export,
    assert_required_fixtures_exist,
    catalog_for_openapi,
    load_business_error,
    load_manifest,
    load_missing_required_field,
    load_success,
    parse_missing_required_field,
    parse_success,
)
from app.contracts.openapi import OpenApiExport, export_openapi
from app.core.config import Settings
from app.main import create_app
from scripts.check_fixtures import main as check_main

FIXTURES_ROOT = Path(__file__).resolve().parents[2] / "fixtures"


def _live_export() -> OpenApiExport:
    return export_openapi(create_app(Settings(app_env="test")))


def test_manifest_matches_live_app_export() -> None:
    exported = _live_export()
    manifest = load_manifest(FIXTURES_ROOT)
    assert_manifest_matches_export(manifest, exported)
    assert manifest["api_version"] == "v1"
    assert manifest["schema_version"] == 2
    assert "generated_at" in manifest
    assert "source_commit" in manifest
    endpoints = [
        entry.get("endpoint")
        for entry in manifest.get("entries", [])
        if isinstance(entry, dict) and entry.get("endpoint")
    ]
    assert endpoints == [
        "DELETE /api/v1/account",
        "GET /api/v1/ask/helped",
        "GET /api/v1/ask/questions",
        "POST /api/v1/ask/questions",
        "GET /api/v1/ask/questions/recent",
        "GET /api/v1/ask/questions/{question_id}",
        "POST /api/v1/ask/questions/{question_id}/rating",
        "POST /api/v1/ask/questions/{question_id}/turns",
        "GET /api/v1/bootstrap",
        "POST /api/v1/chat",
        "GET /api/v1/craft/redeem",
        "POST /api/v1/craft/self-test/turns",
        "GET /api/v1/craft/skills",
        "POST /api/v1/craft/skills",
        "DELETE /api/v1/craft/skills/{craft_id}",
        "GET /api/v1/craft/skills/{craft_id}",
        "PATCH /api/v1/craft/skills/{craft_id}",
        "POST /api/v1/craft/skills/{craft_id}/self-test",
        "POST /api/v1/craft/skills/{craft_id}/visibility",
        "POST /api/v1/extract",
        "GET /api/v1/friends",
        "POST /api/v1/friends",
        "DELETE /api/v1/friends/{friend_id}",
        "DELETE /api/v1/memories",
        "GET /api/v1/memories",
        "DELETE /api/v1/memories/{memory_id}",
        "PATCH /api/v1/memories/{memory_id}",
        "GET /api/v1/messages",
        "POST /api/v1/ops/craft-redeem",
        "GET /api/v1/postcards",
        "PATCH /api/v1/postcards/{postcard_id}/read",
        "POST /api/v1/recall",
        "PATCH /api/v1/spirit",
        "GET /api/v1/spirit-chats",
        "GET /api/v1/spirit-chats/by-spirit/{spirit_id}",
        "GET /api/v1/spirit-chats/{thread_id}/messages",
        "POST /api/v1/spirits",
        "GET /api/v1/spirits/me",
        "POST /api/v1/spirits/me/avatar",
        "POST /api/v1/spirits/me/avatar/default",
        "PUT /api/v1/spirits/me/nickname",
        "GET /api/v1/spirits/{spirit_id}/public",
        "POST /api/v1/synthesize",
        "POST /api/v1/transcribe",
        "GET /api/v1/visit-card",
        "PUT /api/v1/visit-card",
        "POST /api/v1/visit-card/uploads",
        "GET /api/v1/visit-cards",
        "GET /api/v1/visit-cards/{card_id}",
        "POST /api/v1/visit-cards/{card_id}/read",
        "POST /api/v1/visit-cards/{card_id}/reply",
    ]


def test_live_catalog_includes_post_spirits_not_other_api() -> None:
    exported = _live_export()
    entries = catalog_for_openapi(exported.document)
    assert [entry.id for entry in entries] == [
        ENVELOPE_ENTRY_ID,
        "DELETE_api_v1_account",
        "GET_api_v1_ask_helped",
        "GET_api_v1_ask_questions",
        "POST_api_v1_ask_questions",
        "GET_api_v1_ask_questions_recent",
        "GET_api_v1_ask_questions_{question_id}",
        "POST_api_v1_ask_questions_{question_id}_rating",
        "POST_api_v1_ask_questions_{question_id}_turns",
        "GET_api_v1_bootstrap",
        "POST_api_v1_chat",
        "GET_api_v1_craft_redeem",
        "POST_api_v1_craft_self-test_turns",
        "GET_api_v1_craft_skills",
        "POST_api_v1_craft_skills",
        "DELETE_api_v1_craft_skills_{craft_id}",
        "GET_api_v1_craft_skills_{craft_id}",
        "PATCH_api_v1_craft_skills_{craft_id}",
        "POST_api_v1_craft_skills_{craft_id}_self-test",
        "POST_api_v1_craft_skills_{craft_id}_visibility",
        "POST_api_v1_extract",
        "GET_api_v1_friends",
        "POST_api_v1_friends",
        "DELETE_api_v1_friends_{friend_id}",
        "DELETE_api_v1_memories",
        "GET_api_v1_memories",
        "DELETE_api_v1_memories_{memory_id}",
        "PATCH_api_v1_memories_{memory_id}",
        "GET_api_v1_messages",
        "POST_api_v1_ops_craft-redeem",
        "GET_api_v1_postcards",
        "PATCH_api_v1_postcards_{postcard_id}_read",
        "POST_api_v1_recall",
        "PATCH_api_v1_spirit",
        "GET_api_v1_spirit-chats",
        "GET_api_v1_spirit-chats_by-spirit_{spirit_id}",
        "GET_api_v1_spirit-chats_{thread_id}_messages",
        "POST_api_v1_spirits",
        "GET_api_v1_spirits_me",
        "POST_api_v1_spirits_me_avatar",
        "POST_api_v1_spirits_me_avatar_default",
        "PUT_api_v1_spirits_me_nickname",
        "GET_api_v1_spirits_{spirit_id}_public",
        "POST_api_v1_synthesize",
        "POST_api_v1_transcribe",
        "GET_api_v1_visit-card",
        "PUT_api_v1_visit-card",
        "POST_api_v1_visit-card_uploads",
        "GET_api_v1_visit-cards",
        "GET_api_v1_visit-cards_{card_id}",
        "POST_api_v1_visit-cards_{card_id}_read",
        "POST_api_v1_visit-cards_{card_id}_reply",
    ]
    by_id = {entry.id: entry for entry in entries}
    assert by_id["GET_api_v1_memories"].endpoint == "GET /api/v1/memories"
    assert by_id["GET_api_v1_memories"].scenarios == BASE_SCENARIOS + LIST_PAGE_SCENARIOS + (
        "filter_mismatch_cursor",
    )
    assert by_id["GET_api_v1_messages"].endpoint == "GET /api/v1/messages"
    assert by_id["GET_api_v1_messages"].scenarios == BASE_SCENARIOS + LIST_PAGE_SCENARIOS
    assert "POST_api_v1_onboarding_complete" not in by_id
    assert "POST_api_v1_devices" not in by_id
    assert "POST_api_v1_moderate-sight" not in by_id
    assert "POST_api_v1_storage_sight-upload-url" not in by_id
    assert by_id["POST_api_v1_spirits"].endpoint == "POST /api/v1/spirits"
    assert by_id["POST_api_v1_transcribe"].scenarios == BASE_SCENARIOS + (
        "empty_result",
        "invalid_mime",
        "too_large",
        "duration_exceeded",
    )
    assert by_id["POST_api_v1_synthesize"].scenarios == BASE_SCENARIOS + (
        "cache_hit",
        "quota_exceeded",
    )
    assert "POST_api_v1_pacts" not in by_id
    assert "GET_api_v1_report" not in by_id
    assert "POST_api_v1_report_line" not in by_id
    assert by_id["GET_api_v1_friends"].scenarios == BASE_SCENARIOS + LIST_PAGE_SCENARIOS
    assert by_id["POST_api_v1_friends"].scenarios == BASE_SCENARIOS + (
        "self_friend",
        "invalid_invite",
        "idempotent_replay",
    )
    assert by_id["DELETE_api_v1_friends_{friend_id}"].scenarios == BASE_SCENARIOS + (
        "not_found",
        "idempotency_conflict",
    )
    assert by_id["GET_api_v1_postcards"].scenarios == BASE_SCENARIOS + LIST_PAGE_SCENARIOS
    assert by_id["PATCH_api_v1_postcards_{postcard_id}_read"].scenarios == BASE_SCENARIOS + (
        "not_found",
        "read_replay",
    )
    assert by_id["GET_api_v1_craft_skills"].scenarios == BASE_SCENARIOS + LIST_PAGE_SCENARIOS
    assert by_id["POST_api_v1_craft_skills"].scenarios == BASE_SCENARIOS
    assert by_id["GET_api_v1_craft_redeem"].endpoint == "GET /api/v1/craft/redeem"
    assert by_id["GET_api_v1_ask_questions"].scenarios == BASE_SCENARIOS + LIST_PAGE_SCENARIOS
    assert by_id["POST_api_v1_ask_questions"].scenarios == BASE_SCENARIOS + ("unmatched",)
    assert by_id["GET_api_v1_ask_helped"].scenarios == BASE_SCENARIOS + ("empty",)
    assert by_id["POST_api_v1_recall"].endpoint == "POST /api/v1/recall"
    assert by_id["POST_api_v1_recall"].scenarios == BASE_SCENARIOS + (
        "idempotent_replay",
        "not_lost",
        "quota_full_food",
    )
    api_paths = [path for path in exported.document.get("paths", {}) if path.startswith("/api/")]
    assert api_paths == [
        "/api/v1/account",
        "/api/v1/ask/helped",
        "/api/v1/ask/questions",
        "/api/v1/ask/questions/recent",
        "/api/v1/ask/questions/{question_id}",
        "/api/v1/ask/questions/{question_id}/rating",
        "/api/v1/ask/questions/{question_id}/turns",
        "/api/v1/bootstrap",
        "/api/v1/chat",
        "/api/v1/craft/redeem",
        "/api/v1/craft/self-test/turns",
        "/api/v1/craft/skills",
        "/api/v1/craft/skills/{craft_id}",
        "/api/v1/craft/skills/{craft_id}/self-test",
        "/api/v1/craft/skills/{craft_id}/visibility",
        "/api/v1/extract",
        "/api/v1/friends",
        "/api/v1/friends/{friend_id}",
        "/api/v1/memories",
        "/api/v1/memories/{memory_id}",
        "/api/v1/messages",
        "/api/v1/ops/craft-redeem",
        "/api/v1/postcards",
        "/api/v1/postcards/{postcard_id}/read",
        "/api/v1/recall",
        "/api/v1/spirit",
        "/api/v1/spirit-chats",
        "/api/v1/spirit-chats/by-spirit/{spirit_id}",
        "/api/v1/spirit-chats/{thread_id}/messages",
        "/api/v1/spirits",
        "/api/v1/spirits/me",
        "/api/v1/spirits/me/avatar",
        "/api/v1/spirits/me/avatar/default",
        "/api/v1/spirits/me/nickname",
        "/api/v1/spirits/{spirit_id}/public",
        "/api/v1/synthesize",
        "/api/v1/transcribe",
        "/api/v1/visit-card",
        "/api/v1/visit-card/uploads",
        "/api/v1/visit-cards",
        "/api/v1/visit-cards/{card_id}",
        "/api/v1/visit-cards/{card_id}/read",
        "/api/v1/visit-cards/{card_id}/reply",
    ]


def test_success_fixture_loads() -> None:
    loaded = load_success(FIXTURES_ROOT)
    assert loaded.data == {}
    assert loaded.request_id.endswith("0001")


def test_business_error_fixture_maps_known_code() -> None:
    loaded = load_business_error(FIXTURES_ROOT)
    assert loaded.code == "MODEL_UNAVAILABLE"
    assert loaded.retryable is True
    assert loaded.message == "它这会儿说不出话"


def test_missing_required_field_fixture_is_rejected() -> None:
    loaded = load_missing_required_field(FIXTURES_ROOT)
    assert "request_id" in loaded.missing_fields


def test_wrong_type_is_not_accepted_as_missing_field() -> None:
    payload: dict[str, Any] = {
        "ok": ["not-a-bool"],
        "data": {},
        "error": None,
        "request_id": "00000000-0000-4000-8000-000000000009",
        "server_time": "2026-09-08T00:00:00Z",
    }
    try:
        parse_missing_required_field(payload)
    except FixtureValidationError as exc:
        assert "wrong type" in str(exc)
    else:
        raise AssertionError("type errors must not count as missing_required_field")


def test_missing_fixture_file_fails_clearly(tmp_path: Path) -> None:
    copied = tmp_path / "fixtures"
    shutil.copytree(FIXTURES_ROOT, copied)
    target = copied / "envelope" / "success.json"
    target.unlink()
    try:
        load_success(copied)
    except FixtureMissingError as exc:
        assert exc.path == target
        assert "missing fixture" in str(exc)
        assert "success.json" in str(exc)
    else:
        raise AssertionError("missing fixture must fail clearly")
    try:
        assert_required_fixtures_exist(copied)
    except FixtureMissingError as exc:
        assert "success.json" in str(exc)
    else:
        raise AssertionError("required fixture scan must fail when a file is absent")


def test_openapi_api_path_without_fixture_fails_clearly(tmp_path: Path) -> None:
    copied = tmp_path / "fixtures"
    shutil.copytree(FIXTURES_ROOT, copied)
    document: dict[str, Any] = {
        "paths": {
            "/health/live": {"get": {"responses": {"200": {}}}},
            "/api/v1/reports": {"get": {"responses": {"200": {}}}},
        }
    }
    entries = catalog_for_openapi(document)
    assert any(entry.endpoint == "GET /api/v1/reports" for entry in entries)
    try:
        assert_required_fixtures_exist(copied, entries)
    except FixtureMissingError as exc:
        assert "GET_api_v1_reports" in str(exc.path)
        assert "missing fixture" in str(exc)
    else:
        raise AssertionError("unimplemented API fixtures must fail as missing, not be invented")


def test_success_payload_cannot_include_jwt() -> None:
    try:
        parse_success(
            {
                "ok": True,
                "data": {"token": "eyJhbGciOiJSUzI1NiJ9.fake.sig"},
                "error": None,
                "request_id": "00000000-0000-4000-8000-000000000003",
                "server_time": "2026-09-08T00:00:00Z",
            }
        )
    except FixtureValidationError as exc:
        assert "sensitive" in str(exc)
    else:
        raise AssertionError("JWT-like values must be rejected")


def test_cli_validates_committed_fixtures() -> None:
    assert check_main(["--root", str(FIXTURES_ROOT)]) == 0


def test_cli_exits_nonzero_when_fixture_missing(tmp_path: Path) -> None:
    copied = tmp_path / "fixtures"
    shutil.copytree(FIXTURES_ROOT, copied)
    (copied / "envelope" / "business_error.json").unlink()
    assert check_main(["--root", str(copied)]) == 1


def test_cli_write_manifest_roundtrip(tmp_path: Path) -> None:
    copied = tmp_path / "fixtures"
    shutil.copytree(FIXTURES_ROOT, copied)
    (copied / "manifest.json").unlink()
    assert check_main(["--root", str(copied), "--write-manifest"]) == 0
    written = json.loads((copied / "manifest.json").read_text(encoding="utf-8"))
    assert written["openapi_sha256"] == _live_export().sha256
