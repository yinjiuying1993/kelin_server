from __future__ import annotations

import inspect
from pathlib import Path

from app.repositories.spirit_state import _LOCK_SQL
from app.services.spirit_state import settle_spirit_state

APPLY_SOURCE = (
    Path(__file__).resolve().parents[2] / "app" / "repositories" / "spirit_state.py"
).read_text(encoding="utf-8")
SERVICE_SOURCE = (
    Path(__file__).resolve().parents[2] / "app" / "services" / "spirit_state.py"
).read_text(encoding="utf-8")


def test_settle_does_not_accept_client_status_or_delta() -> None:
    params = inspect.signature(settle_spirit_state).parameters
    assert set(params) == {"session", "user", "now"}
    assert "status" not in params
    assert "delta" not in params
    assert "patch" not in params
    assert params["now"].kind is inspect.Parameter.KEYWORD_ONLY


def test_lock_is_owner_row_for_update() -> None:
    assert "FOR UPDATE OF s" in _LOCK_SQL
    assert "s.user_id = :user_id" in _LOCK_SQL


def test_patch_bumps_only_matching_version_and_does_not_touch_last_interact() -> None:
    assert "AND version = :expected_version" in APPLY_SOURCE
    assert "version = version + 1" in APPLY_SOURCE
    assert "last_interact_at =" not in APPLY_SOURCE


def test_service_does_not_write_client_status() -> None:
    assert "body.status" not in SERVICE_SOURCE
    assert "request.status" not in SERVICE_SOURCE
    assert "desired_spirit_state_at" in SERVICE_SOURCE
    assert "expected_version=locked.version" in SERVICE_SOURCE
