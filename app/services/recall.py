"""Recall lost spirits with food or owned active sight. Spec §§8.7, 11.5."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser
from app.domain.cursor import utc_iso
from app.domain.feed import DAILY_LIMITS
from app.domain.quota import QuotaSnapshot, quota_usage
from app.domain.recall import (
    RECALL_GROWTH_EVENT,
    RECALL_MUTATION_EVENT,
    RECALL_RELATION_CONFIDENCE,
    RECALL_RELATION_SALIENCE,
    RECALL_RELATION_SUMMARY,
    recall_growth_payload,
    recall_relation_memory_id,
    recall_request_hash,
)
from app.providers.types import MemoryType
from app.repositories import bootstrap as bootstrap_repo
from app.repositories import recall as recall_repo
from app.repositories.feed import IdempotencyClaim, MemoryRow
from app.schemas.bootstrap import RoomPublic
from app.schemas.memory import MemoryPublic, MemoryStatus
from app.schemas.recall import RecallMethod, RecallRequest, RecallSightRequest
from app.schemas.spirit import MutationEvent, QuotaUsage, SpiritPublic
from app.services.bootstrap import room_public_from_aggregate, spirit_public_from_bootstrap_row
from app.services.evolution import maybe_advance_stage
from app.services.growth import record_and_apply
from app.services.quota import consume as consume_quota


@dataclass(frozen=True, slots=True)
class RecallSettlement:
    client_id: uuid.UUID
    method: RecallMethod
    snapshot_version: int
    spirit: SpiritPublic
    room: RoomPublic
    memories: tuple[MemoryPublic, ...]
    quotas: tuple[QuotaUsage, ...]
    events: tuple[MutationEvent, ...]


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


async def recall_spirit(
    session: AsyncSession,
    user: CurrentUser,
    body: RecallRequest,
    *,
    now: datetime,
) -> RecallSettlement:
    await recall_repo.assert_writable_transaction(session)
    locked = await recall_repo.lock_owned_spirit(session, user.id)
    if locked is None:
        raise _api_error("NOT_FOUND", status_code=404)
    request_hash = recall_request_hash(body)
    claim = await recall_repo.claim_recall_idempotency(
        session, user.id, body.client_id, request_hash
    )
    if not claim.inserted:
        return await _replay(session, user, body, claim, request_hash, now=now)
    if locked.status != "lost":
        raise _api_error("SPIRIT_NOT_LOST", status_code=409)
    if isinstance(body, RecallSightRequest):
        await _require_active_owned_sight(session, spirit_id=locked.id, memory_id=body.memory_id)
    if body.method == "food":
        await _try_consume_food(session, user.id, now=now, timezone=locked.timezone)
    memory = await recall_repo.insert_relation_memory(
        session,
        memory_id=recall_relation_memory_id(owner_id=user.id, client_id=body.client_id),
        spirit_id=locked.id,
        summary=RECALL_RELATION_SUMMARY,
        salience=RECALL_RELATION_SALIENCE,
        confidence=RECALL_RELATION_CONFIDENCE,
    )
    recorded = await record_and_apply(
        session,
        owner_id=user.id,
        spirit_id=locked.id,
        event_type=RECALL_GROWTH_EVENT,
        source_id=body.client_id,
        payload=recall_growth_payload(),
        now=now,
        touch_interact=True,
    )
    await maybe_advance_stage(session, owner_id=user.id, spirit_id=locked.id, now=now)
    marked = await recall_repo.mark_returned_home(session, owner_id=user.id, spirit_id=locked.id)
    if not marked:
        raise _api_error("SPIRIT_NOT_LOST", status_code=409)
    await recall_repo.complete_recall_idempotency(session, user.id, body.client_id, body.client_id)
    return await _project(
        session,
        user,
        client_id=body.client_id,
        method=body.method,
        now=now,
        memories=(_public_memory(memory),),
        events=(
            MutationEvent(
                id=recorded.event_id,
                type=RECALL_MUTATION_EVENT,
                occurred_at=utc_iso(now),
            ),
        ),
    )


async def _require_active_owned_sight(
    session: AsyncSession, *, spirit_id: uuid.UUID, memory_id: uuid.UUID
) -> None:
    row = await recall_repo.fetch_memory(session, memory_id)
    if row is None or row.spirit_id != spirit_id or row.type != "sight" or row.status != "active":
        raise _api_error("RECALL_SOURCE_INVALID", status_code=422)


async def _try_consume_food(
    session: AsyncSession,
    owner_id: uuid.UUID,
    *,
    now: datetime,
    timezone: str,
) -> None:
    try:
        await consume_quota(
            session,
            owner_id,
            "food",
            now=now,
            timezone=timezone,
            limit_value=DAILY_LIMITS["food"],
        )
    except ApiError as exc:
        if exc.code != "QUOTA_EXCEEDED":
            raise


async def _replay(
    session: AsyncSession,
    user: CurrentUser,
    body: RecallRequest,
    claim: IdempotencyClaim,
    request_hash: str,
    *,
    now: datetime,
) -> RecallSettlement:
    if claim.request_hash != request_hash:
        raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
    if claim.status == "in_progress":
        raise _api_error("IDEMPOTENCY_IN_PROGRESS", status_code=409, retryable=True)
    if claim.status == "conflict":
        raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
    if claim.status != "completed":
        raise _api_error("IDEMPOTENCY_IN_PROGRESS", status_code=409, retryable=True)
    memory = await recall_repo.fetch_memory_row(
        session, recall_relation_memory_id(owner_id=user.id, client_id=body.client_id)
    )
    memories: tuple[MemoryPublic, ...] = ()
    if memory is not None:
        memories = (_public_memory(memory),)
    event_id = await recall_repo.fetch_growth_event_id(
        session, source_id=body.client_id, event_type=RECALL_GROWTH_EVENT
    )
    events: tuple[MutationEvent, ...] = ()
    if event_id is not None:
        events = (MutationEvent(id=event_id, type=RECALL_MUTATION_EVENT, occurred_at=utc_iso(now)),)
    return await _project(
        session,
        user,
        client_id=body.client_id,
        method=body.method,
        now=now,
        memories=memories,
        events=events,
    )


async def _project(
    session: AsyncSession,
    user: CurrentUser,
    *,
    client_id: uuid.UUID,
    method: RecallMethod,
    now: datetime,
    memories: tuple[MemoryPublic, ...],
    events: tuple[MutationEvent, ...],
) -> RecallSettlement:
    rows = await bootstrap_repo.load_bootstrap_aggregate_rows(
        session, user.id, now=now, require_read_snapshot=False
    )
    if rows.spirit is None:
        raise _api_error("NOT_FOUND", status_code=404)
    return RecallSettlement(
        client_id=client_id,
        method=method,
        snapshot_version=rows.spirit.version,
        spirit=spirit_public_from_bootstrap_row(rows.spirit),
        room=room_public_from_aggregate(rows, now=now),
        memories=memories,
        quotas=tuple(
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
        ),
        events=events,
    )


def _public_memory(row: MemoryRow) -> MemoryPublic:
    return MemoryPublic(
        id=row.id,
        type=_memory_type(row.type),
        summary=row.summary,
        tags=list(row.tags),
        salience=row.salience,
        confidence=row.confidence,
        status=_memory_status(row.status),
        version=row.version,
        created_at=utc_iso(row.created_at),
    )


def _memory_type(value: str) -> MemoryType:
    mapping: dict[str, MemoryType] = {
        "preference": "preference",
        "knowledge": "knowledge",
        "emotion": "emotion",
        "relation": "relation",
        "speech": "speech",
        "sight": "sight",
    }
    mapped = mapping.get(value)
    if mapped is None:
        raise RuntimeError("invalid memory type")
    return mapped


def _memory_status(value: str) -> MemoryStatus:
    if value == "active":
        return "active"
    if value == "sealed":
        return "sealed"
    if value == "deleted":
        return "deleted"
    raise RuntimeError("invalid memory status")
