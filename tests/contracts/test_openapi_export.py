from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from app.contracts.openapi import SOURCE_APP, export_openapi, openapi_sha256
from app.core.config import Settings
from app.main import create_app
from fastapi import FastAPI
from pydantic import SecretStr
from scripts.export_openapi import main as export_main


def _test_app() -> FastAPI:
    return create_app(Settings(app_env="test"))


def test_repeat_export_from_real_app_has_identical_sha() -> None:
    first = export_openapi(_test_app())
    second = export_openapi(_test_app())
    assert first.sha256 == second.sha256
    assert first.canonical_bytes == second.canonical_bytes
    assert len(first.sha256) == 64
    assert first.sha256 == hashlib.sha256(first.canonical_bytes).hexdigest()


def test_manifest_points_at_real_create_app() -> None:
    exported = export_openapi(_test_app())
    assert exported.manifest["source_app"] == SOURCE_APP
    assert SOURCE_APP == "app.main:create_app"
    assert exported.manifest["openapi_sha256"] == exported.sha256
    assert exported.manifest["api_version"] == "v1"
    assert exported.manifest["schema_version"] == 2
    assert exported.manifest["generated_at"].endswith("Z")
    assert "generated_at" not in exported.document


def test_exported_spec_is_current_app_only_no_invented_api_v1() -> None:
    application = _test_app()
    exported = export_openapi(application)
    live_paths = set(application.openapi().get("paths", {}))
    exported_paths = set(exported.document.get("paths", {}))
    assert exported_paths == live_paths
    assert "/health/live" in exported_paths
    assert "/health/ready" in exported_paths
    assert "/api/v1/spirits" in exported_paths
    assert "/api/v1/bootstrap" in exported_paths
    assert "/api/v1/chat" in exported_paths
    assert "/api/v1/extract" in exported_paths
    assert "/api/v1/feed" not in exported_paths
    assert "/api/v1/feeds/{feed_id}" not in exported_paths
    assert "/api/v1/messages" in exported_paths
    assert "/api/v1/memories" in exported_paths
    assert "/api/v1/memories/{memory_id}" in exported_paths
    assert "/api/v1/onboarding/complete" not in exported_paths
    assert "/api/v1/storage/sight-upload-url" not in exported_paths
    assert "/api/v1/devices" not in exported_paths
    assert "/api/v1/moderate-sight" not in exported_paths
    assert "/api/v1/transcribe" in exported_paths
    assert "/api/v1/synthesize" in exported_paths
    assert "/api/v1/pacts" not in exported_paths
    assert "/api/v1/report" not in exported_paths
    assert "/api/v1/friends" in exported_paths
    assert "/api/v1/friends/{friend_id}" in exported_paths
    assert "/api/v1/postcards" in exported_paths
    assert "/api/v1/postcards/{postcard_id}/read" in exported_paths
    assert "/api/v1/recall" in exported_paths
    assert exported.document["paths"]["/api/v1/spirits"]["post"]
    assert exported.document["paths"]["/api/v1/bootstrap"]["get"]
    assert exported.document["paths"]["/api/v1/chat"]["post"]
    assert exported.document["paths"]["/api/v1/extract"]["post"]
    assert exported.document["paths"]["/api/v1/messages"]["get"]
    assert exported.document["paths"]["/api/v1/memories"]["get"]
    assert exported.document["paths"]["/api/v1/memories"]["delete"]
    assert exported.document["paths"]["/api/v1/memories/{memory_id}"]["patch"]
    assert exported.document["paths"]["/api/v1/memories/{memory_id}"]["delete"]
    assert exported.document["paths"]["/api/v1/transcribe"]["post"]
    assert exported.document["paths"]["/api/v1/synthesize"]["post"]
    assert exported.document["paths"]["/api/v1/friends"]["get"]
    assert exported.document["paths"]["/api/v1/friends"]["post"]
    assert exported.document["paths"]["/api/v1/friends/{friend_id}"]["delete"]
    assert exported.document["paths"]["/api/v1/postcards"]["get"]
    assert exported.document["paths"]["/api/v1/postcards/{postcard_id}/read"]["patch"]
    extra_api = {
        path
        for path in exported_paths
        if path.startswith("/api/")
        and path
        not in {
            "/api/v1/account",
            "/api/v1/ask/helped",
            "/api/v1/ask/questions",
            "/api/v1/ask/questions/recent",
            "/api/v1/ask/questions/{question_id}",
            "/api/v1/ask/questions/{question_id}/rating",
            "/api/v1/ask/questions/{question_id}/turns",
            "/api/v1/spirits",
            "/api/v1/spirits/{spirit_id}/public",
            "/api/v1/bootstrap",
            "/api/v1/chat",
            "/api/v1/craft/redeem",
            "/api/v1/craft/self-test/turns",
            "/api/v1/craft/skills",
            "/api/v1/craft/skills/{craft_id}",
            "/api/v1/craft/skills/{craft_id}/self-test",
            "/api/v1/craft/skills/{craft_id}/visibility",
            "/api/v1/extract",
            "/api/v1/memories",
            "/api/v1/memories/{memory_id}",
            "/api/v1/messages",
            "/api/v1/ops/craft-redeem",
            "/api/v1/transcribe",
            "/api/v1/synthesize",
            "/api/v1/visit-card",
            "/api/v1/visit-card/uploads",
            "/api/v1/visit-cards",
            "/api/v1/visit-cards/{card_id}",
            "/api/v1/visit-cards/{card_id}/read",
            "/api/v1/visit-cards/{card_id}/reply",
            "/api/v1/friends",
            "/api/v1/friends/{friend_id}",
            "/api/v1/postcards",
            "/api/v1/postcards/{postcard_id}/read",
            "/api/v1/spirit",
            "/api/v1/account",
            "/api/v1/recall",
        }
    }
    assert extra_api == set()
    assert not any("search" in path for path in exported_paths)


def test_canonical_json_strips_volatile_servers() -> None:
    application = _test_app()
    raw = deepcopy(application.openapi())
    raw["servers"] = [{"url": "http://127.0.0.1:1"}]
    without_servers = dict(raw)
    without_servers.pop("servers")
    assert openapi_sha256(raw) == openapi_sha256(without_servers)


def test_cli_writes_canonical_json_and_manifest(tmp_path: Path) -> None:
    assert export_main(["--out-dir", str(tmp_path)]) == 0
    canonical = tmp_path / "openapi.canonical.json"
    manifest_path = tmp_path / "openapi.manifest.json"
    document = json.loads(canonical.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source_app"] == SOURCE_APP
    assert manifest["openapi_sha256"] == hashlib.sha256(canonical.read_bytes()).hexdigest()
    assert "/health/live" in document["paths"]
    first = export_openapi(_test_app())
    second_bytes = canonical.read_bytes()
    assert first.canonical_bytes == second_bytes


def test_prod_app_openapi_method_has_no_debug_and_same_health_paths() -> None:
    application = create_app(
        Settings(
            app_env="prod",
            database_url_api=SecretStr("postgresql://probe:probe@127.0.0.1:1/kelin"),
            supabase_jwt_issuer="https://example.supabase.co/auth/v1",
            supabase_jwt_audience="authenticated",
            supabase_jwks_url="https://example.supabase.co/auth/v1/.well-known/jwks.json",
            cursor_hmac_secret=SecretStr("test-only-cursor-hmac"),
            device_token_key=SecretStr("kelin-test-device-token-key-32bytes"),
        )
    )
    exported = export_openapi(application)
    paths = set(exported.document.get("paths", {}))
    assert "/health/live" in paths
    assert "/api/v1/spirits" in paths
    assert "/api/v1/bootstrap" in paths
    assert "/api/v1/chat" in paths
    assert "/api/v1/extract" in paths
    assert "/api/v1/feed" not in paths
    assert "/api/v1/messages" in paths
    assert "/api/v1/memories" in paths
    assert "/api/v1/memories/{memory_id}" in paths
    assert "/api/v1/onboarding/complete" not in paths
    assert "/api/v1/storage/sight-upload-url" not in paths
    assert "/api/v1/devices" not in paths
    assert "/api/v1/moderate-sight" not in paths
    assert "/api/v1/transcribe" in paths
    assert "/api/v1/synthesize" in paths
    assert "/api/v1/pacts" not in paths
    assert "/api/v1/report" not in paths
    assert "/api/v1/friends" in paths
    assert "/api/v1/friends/{friend_id}" in paths
    assert "/api/v1/postcards" in paths
    assert "/api/v1/postcards/{postcard_id}/read" in paths
    assert "/api/v1/recall" in paths
    assert not any("debug" in path for path in paths)
    extra_api = {
        path
        for path in paths
        if path.startswith("/api/")
        and path
        not in {
            "/api/v1/account",
            "/api/v1/ask/helped",
            "/api/v1/ask/questions",
            "/api/v1/ask/questions/recent",
            "/api/v1/ask/questions/{question_id}",
            "/api/v1/ask/questions/{question_id}/rating",
            "/api/v1/ask/questions/{question_id}/turns",
            "/api/v1/spirits",
            "/api/v1/spirits/{spirit_id}/public",
            "/api/v1/bootstrap",
            "/api/v1/chat",
            "/api/v1/craft/redeem",
            "/api/v1/craft/self-test/turns",
            "/api/v1/craft/skills",
            "/api/v1/craft/skills/{craft_id}",
            "/api/v1/craft/skills/{craft_id}/self-test",
            "/api/v1/craft/skills/{craft_id}/visibility",
            "/api/v1/extract",
            "/api/v1/memories",
            "/api/v1/memories/{memory_id}",
            "/api/v1/messages",
            "/api/v1/ops/craft-redeem",
            "/api/v1/transcribe",
            "/api/v1/synthesize",
            "/api/v1/visit-card",
            "/api/v1/visit-card/uploads",
            "/api/v1/visit-cards",
            "/api/v1/visit-cards/{card_id}",
            "/api/v1/visit-cards/{card_id}/read",
            "/api/v1/visit-cards/{card_id}/reply",
            "/api/v1/friends",
            "/api/v1/friends/{friend_id}",
            "/api/v1/postcards",
            "/api/v1/postcards/{postcard_id}/read",
            "/api/v1/spirit",
            "/api/v1/account",
            "/api/v1/recall",
        }
    }
    assert extra_api == set()
    assert not any("search" in path for path in paths)
