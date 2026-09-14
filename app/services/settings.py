"""PATCH /spirit lock+expected_version+single bump. Spec §§5.5, 9.5."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser
from app.domain.settings import parse_hhmm, patch_spirit_request_hash
from app.domain.spirit_state import require_aware
from app.repositories import chat as chat_repo
from app.repositories import settings as settings_repo
from app.repositories.settings import SettingsProjection
from app.schemas.settings import PatchSpiritRequest, PreferencesPatch


def _api_error(
    code: str,
    *,
    status_code: int,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> ApiError:
    return ApiError(
        code,
        public_error_message(code),
        status_code=status_code,
        retryable=retryable,
        details=details,
    )


async def patch_spirit(
    session: AsyncSession,
    user: CurrentUser,
    request: PatchSpiritRequest,
    *,
    now: datetime,
) -> SettingsProjection:
    require_aware(now, field="now")
    await settings_repo.assert_writable_transaction(session)
    locked = await chat_repo.lock_spirit_for_owner(session, user.id)
    if locked is None:
        raise _api_error("NOT_FOUND", status_code=404)
    request_hash = patch_spirit_request_hash(request)
    claim = await settings_repo.claim_patch_idempotency(
        session, user.id, request.client_id, request_hash
    )
    if not claim.inserted:
        if claim.request_hash != request_hash:
            raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
        if claim.status == "in_progress":
            raise _api_error("IDEMPOTENCY_IN_PROGRESS", status_code=409, retryable=True)
        if claim.status == "conflict":
            raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
        result = await settings_repo.fetch_projection(session, user.id)
        if result is None:
            raise _api_error("INTERNAL_ERROR", status_code=500)
        return result
    if request.expected_version != locked.version:
        raise _api_error(
            "CONFLICT",
            status_code=409,
            details={"current_snapshot_version": locked.version},
        )
    pref_fields = _preference_fields(request.preferences)
    await settings_repo.update_preferences(
        session, owner_id=user.id, fields=pref_fields
    )
    bumped = await settings_repo.bump_spirit(
        session,
        owner_id=user.id,
        expected_version=request.expected_version,
        name=request.name,
    )
    if bumped is None:
        raise _api_error(
            "CONFLICT",
            status_code=409,
            details={"current_snapshot_version": locked.version},
        )
    result = await settings_repo.fetch_projection(session, user.id)
    if result is None:
        raise _api_error("INTERNAL_ERROR", status_code=500)
    await settings_repo.complete_patch_idempotency(
        session, user.id, request.client_id, result.spirit_id
    )
    return result


def _preference_fields(patch: PreferencesPatch | None) -> dict[str, Any]:
    if patch is None:
        return {}
    mapping = {
        "tts_on": patch.tts_on,
        "push_on": patch.push_on,
        "visit_on": patch.visit_on,
        "dnd_start": patch.dnd_start,
        "dnd_end": patch.dnd_end,
        "timezone": patch.timezone,
        "default_city": patch.default_city,
        "location_weather_on": patch.location_weather_on,
        "remote_search_on": patch.remote_search_on,
    }
    fields: dict[str, Any] = {}
    for column, value in mapping.items():
        if column not in patch.model_fields_set:
            continue
        if column in {"dnd_start", "dnd_end"} and value is not None:
            fields[column] = parse_hhmm(str(value))
            continue
        fields[column] = value
    return fields
