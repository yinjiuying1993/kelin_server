"""Dev-only Debug services. Spec §14.10. Current JWT owner only."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser
from app.debug.failures import DebugFailure, set_failure
from app.domain.cursor import utc_iso
from app.domain.debug import DEBUG_RESET_CONFIRM
from app.domain.quota import QuotaSnapshot, quota_usage
from app.providers.types import MemoryType
from app.repositories import bootstrap as bootstrap_repo
from app.repositories import debug as debug_repo
from app.repositories.feed import MemoryRow
from app.schemas.debug import (
    DebugFailureRequest,
    DebugMemoryCreateRequest,
    DebugReportEligibilityRequest,
    DebugResetRequest,
    DebugSpiritStateRequest,
    DebugSpiritTimeRequest,
)
from app.schemas.memory import MemoryPublic, MemoryStatus
from app.schemas.spirit import QuotaUsage, SpiritPublic
from app.services.bootstrap import spirit_public_from_bootstrap_row


@dataclass(frozen=True, slots=True)
class DebugSettlement:
    spirit_id: uuid.UUID
    snapshot_version: int
    spirit: SpiritPublic
    memories: tuple[MemoryPublic, ...]
    quotas: tuple[QuotaUsage, ...]


@dataclass(frozen=True, slots=True)
class DebugUsage:
    snapshot_version: int
    quotas: tuple[QuotaUsage, ...]


def _api_error(code: str, *, status_code: int) -> ApiError:
    return ApiError(code, public_error_message(code), status_code=status_code, retryable=False)


async def apply_spirit_state(
    session: AsyncSession,
    user: CurrentUser,
    body: DebugSpiritStateRequest,
    *,
    now: datetime,
) -> DebugSettlement:
    await debug_repo.assert_writable_transaction(session)
    spirit_id = await debug_repo.lock_owned_spirit_id(session, user.id)
    if spirit_id is None:
        raise _api_error("NOT_FOUND", status_code=404)
    fields: dict[str, Any] = {}
    for column in ("status", "hunger", "energy", "mood", "bond"):
        value = getattr(body, column)
        if value is not None:
            fields[column] = value
    if body.traits is not None:
        for column in ("closeness", "curiosity", "sharpness", "nocturnal", "stubborn"):
            value = getattr(body.traits, column)
            if value is not None:
                fields[column] = value
    version = await debug_repo.update_spirit_fields(session, owner_id=user.id, fields=fields)
    if version is None:
        raise _api_error("NOT_FOUND", status_code=404)
    return await _project(session, user, now=now)


async def apply_spirit_time(
    session: AsyncSession,
    user: CurrentUser,
    body: DebugSpiritTimeRequest,
    *,
    now: datetime,
) -> DebugSettlement:
    await debug_repo.assert_writable_transaction(session)
    spirit_id = await debug_repo.lock_owned_spirit_id(session, user.id)
    if spirit_id is None:
        raise _api_error("NOT_FOUND", status_code=404)
    fields: dict[str, Any] = {"last_interact_at": body.last_interact_at}
    if "away_until" in body.model_fields_set:
        fields["away_until"] = body.away_until
    if "study_until" in body.model_fields_set:
        fields["study_until"] = body.study_until
    if "hatched_at" in body.model_fields_set:
        fields["hatched_at"] = body.hatched_at
    version = await debug_repo.update_spirit_fields(session, owner_id=user.id, fields=fields)
    if version is None:
        raise _api_error("NOT_FOUND", status_code=404)
    return await _project(session, user, now=now)


async def create_memory(
    session: AsyncSession,
    user: CurrentUser,
    body: DebugMemoryCreateRequest,
    *,
    now: datetime,
) -> DebugSettlement:
    await debug_repo.assert_writable_transaction(session)
    spirit_id = await debug_repo.lock_owned_spirit_id(session, user.id)
    if spirit_id is None:
        raise _api_error("NOT_FOUND", status_code=404)
    row = await debug_repo.insert_memory(
        session,
        memory_id=uuid.uuid4(),
        spirit_id=spirit_id,
        memory_type=body.type,
        summary=body.summary,
        tags=list(body.tags),
        salience=body.salience,
        confidence=body.confidence,
        status=body.status,
        now=now,
    )
    await debug_repo.update_spirit_fields(
        session, owner_id=user.id, fields={"last_interact_at": now}
    )
    settlement = await _project(session, user, now=now)
    return DebugSettlement(
        spirit_id=settlement.spirit_id,
        snapshot_version=settlement.snapshot_version,
        spirit=settlement.spirit,
        memories=(_public_memory(row),),
        quotas=settlement.quotas,
    )


async def apply_report_eligibility(
    session: AsyncSession,
    user: CurrentUser,
    body: DebugReportEligibilityRequest,
    *,
    now: datetime,
) -> DebugSettlement:
    await debug_repo.assert_writable_transaction(session)
    spirit_id = await debug_repo.lock_owned_spirit_id(session, user.id)
    if spirit_id is None:
        raise _api_error("NOT_FOUND", status_code=404)
    hatched_at = now - timedelta(days=body.hatched_days_ago)
    version = await debug_repo.update_spirit_fields(
        session,
        owner_id=user.id,
        fields={
            "hatched_at": hatched_at,
            "ordinary_dialogue_rounds": body.ordinary_dialogue_rounds,
        },
    )
    if version is None:
        raise _api_error("NOT_FOUND", status_code=404)
    return await _project(session, user, now=now)


def inject_failure(user: CurrentUser, body: DebugFailureRequest) -> DebugFailure:
    return set_failure(
        user.id,
        capability=body.capability,
        mode=body.mode,
        remaining_calls=body.remaining_calls,
        latency_ms=body.latency_ms,
    )


async def load_usage(session: AsyncSession, user: CurrentUser, *, now: datetime) -> DebugUsage:
    rows = await bootstrap_repo.load_bootstrap_aggregate_rows(
        session, user.id, now=now, require_read_snapshot=False
    )
    if rows.spirit is None:
        raise _api_error("NOT_FOUND", status_code=404)
    return DebugUsage(
        snapshot_version=rows.spirit.version,
        quotas=_quotas(rows),
    )


async def reset_account(
    session: AsyncSession,
    user: CurrentUser,
    body: DebugResetRequest,
    *,
    now: datetime,
) -> DebugSettlement:
    if body.confirm != DEBUG_RESET_CONFIRM:
        raise _api_error("INVALID_INPUT", status_code=422)
    await debug_repo.assert_writable_transaction(session)
    spirit_id = await debug_repo.lock_owned_spirit_id(session, user.id)
    if spirit_id is None:
        raise _api_error("NOT_FOUND", status_code=404)
    version = await debug_repo.reset_spirit(session, owner_id=user.id, now=now)
    if version is None:
        raise _api_error("NOT_FOUND", status_code=404)
    from app.debug.failures import clear_failures

    clear_failures(user.id)
    return await _project(session, user, now=now)


async def _project(session: AsyncSession, user: CurrentUser, *, now: datetime) -> DebugSettlement:
    rows = await bootstrap_repo.load_bootstrap_aggregate_rows(
        session, user.id, now=now, require_read_snapshot=False
    )
    if rows.spirit is None:
        raise _api_error("NOT_FOUND", status_code=404)
    return DebugSettlement(
        spirit_id=rows.spirit.id,
        snapshot_version=rows.spirit.version,
        spirit=spirit_public_from_bootstrap_row(rows.spirit),
        memories=(),
        quotas=_quotas(rows),
    )


def _quotas(rows: bootstrap_repo.BootstrapAggregateRows) -> tuple[QuotaUsage, ...]:
    return tuple(
        quota_usage(
            QuotaSnapshot(
                capability=item.capability,
                used=item.used,
                reserved=0,
                limit=item.limit,
                usage_date=item.usage_date,
                timezone=item.timezone,
                version=1,
            )
        )
        for item in rows.quotas
    )


def _public_memory(row: MemoryRow) -> MemoryPublic:
    mapped: dict[str, MemoryType] = {
        "preference": "preference",
        "knowledge": "knowledge",
        "emotion": "emotion",
        "relation": "relation",
        "speech": "speech",
        "sight": "sight",
    }
    status: MemoryStatus
    if row.status == "active":
        status = "active"
    elif row.status == "sealed":
        status = "sealed"
    elif row.status == "deleted":
        status = "deleted"
    else:
        raise RuntimeError("invalid memory status")
    return MemoryPublic(
        id=row.id,
        type=mapped[row.type],
        summary=row.summary,
        tags=list(row.tags),
        salience=row.salience,
        confidence=row.confidence,
        status=status,
        version=row.version,
        created_at=utc_iso(row.created_at),
    )
