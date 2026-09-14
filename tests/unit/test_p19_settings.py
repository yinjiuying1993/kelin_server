"""P19 settings contract, linking has no REST, OpenAPI surface."""

from __future__ import annotations

from inspect import getsource
from pathlib import Path

from app.contracts.openapi import export_openapi
from app.core.config import Settings
from app.main import create_app
from app.schemas.settings import PatchSpiritRequest
from pydantic import ValidationError
from pytest import raises

ROOT = Path(__file__).resolve().parents[2]


def test_patch_request_rejects_empty_and_illegal_values() -> None:
    client = "00000000-0000-4000-8000-0000000000c1"
    with raises(ValidationError):
        PatchSpiritRequest.model_validate({"client_id": client, "expected_version": 1})
    with raises(ValidationError):
        PatchSpiritRequest.model_validate(
            {
                "client_id": client,
                "expected_version": 1,
                "preferences": {"timezone": "Not/AZone"},
            }
        )
    with raises(ValidationError):
        PatchSpiritRequest.model_validate(
            {
                "client_id": client,
                "expected_version": 1,
                "preferences": {"dnd_start": "25:00"},
            }
        )
    with raises(ValidationError):
        PatchSpiritRequest.model_validate(
            {"client_id": client, "expected_version": 1, "name": "一二三四五六七八九十一二三四五六七八九十一"}
        )
    ok = PatchSpiritRequest.model_validate(
        {
            "client_id": client,
            "expected_version": 1,
            "name": "雾生",
            "preferences": {"tts_on": True, "dnd_end": "08:00"},
        }
    )
    assert ok.name == "雾生"
    assert ok.preferences is not None
    assert ok.preferences.tts_on is True


def test_openapi_has_settings_and_account_but_no_linking_rest() -> None:
    paths = export_openapi(create_app(Settings(app_env="test"))).document["paths"]
    assert "patch" in paths["/api/v1/spirit"]
    assert "delete" in paths["/api/v1/account"]
    joined = " ".join(paths)
    assert "/link" not in joined
    assert "identity" not in joined.lower()
    assert "magic-link" not in joined
    assert "/bind" not in joined


def test_account_definer_is_api_only() -> None:
    sql = (
        ROOT
        / "app"
        / "db"
        / "migrations"
        / "versions"
        / "20260908_0019_account_deletion.py"
    ).read_text(encoding="utf-8")
    assert "SET search_path = pg_catalog, public, private" in sql
    assert "GRANT EXECUTE ON FUNCTION private.mark_account_deleting(uuid, text) TO kelin_api" in sql
    assert "mark_account_deleting(uuid, text) TO kelin_worker" not in sql
    assert "GRANT EXECUTE ON FUNCTION private.account_residue_exists(uuid) TO kelin_worker" in sql
    assert "account_residue_exists(uuid) TO kelin_api" not in sql
    assert "GRANT EXECUTE ON FUNCTION public.delete_auth_user(uuid) TO kelin_worker" in sql
    assert "delete_auth_user(uuid) TO kelin_api" not in sql
    assert "DELETE FROM auth.users" in sql
    repo = getsource(__import__("app.repositories.account", fromlist=["accept_account_deletion"]))
    assert "private.mark_account_deleting(:client_id, :owner_hash)" in repo
