"""Recall a visiting spirit home. Spec §§8.7, 11.5."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser
from app.domain.cursor import utc_iso
from app.domain.quota import QuotaSnapshot, quota_usage
from app.domain.recall import (
    RECALL_MUTATION_EVENT,
    recall_request_hash,
)
from app.domain.user_actions.types import SpiritStatus
from app.repositories import bootstrap as bootstrap_repo
from app.repositories import recall as recall_repo
from app.repositories.memory_rows import IdempotencyClaim
from app.schemas.memory import MemoryPublic
from app.schemas.recall import RecallMethod, RecallRequest
from app.schemas.room import RoomPublic
from app.schemas.spirit import MutationEvent, QuotaUsage, SpiritPublic
from app.services.bootstrap import room_public_from_aggregate, spirit_public_from_bootstrap_row
from app.services.central.lifecycle import settle_then_resolve
from app.services.central import publish_user_action
from app.services.memory.event_memory import load_event_memory


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


async def _event_memories(
    session: AsyncSession,
    spirit_id: uuid.UUID,
    event_id: uuid.UUID,
) -> tuple[MemoryPublic, ...]:
    record = await load_event_memory(session, spirit_id, event_id)
    if record is None:
        return ()
    return (
        MemoryPublic(
            id=record.id,
            type=record.type,  # type: ignore[arg-type]
            summary=record.summary,
            tags=list(record.tags),
            salience=record.salience,
            confidence=record.confidence,
            status=record.status,  # type: ignore[arg-type]
            version=record.version,
            created_at=utc_iso(record.created_at),
        ),
    )


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
    await settle_then_resolve(session, user, now=now)
    locked = await recall_repo.lock_owned_spirit(session, user.id)
    if locked is None:
        raise _api_error("NOT_FOUND", status_code=404)
    request_hash = recall_request_hash(body)
    claim = await recall_repo.claim_recall_idempotency(
        session, user.id, body.client_id, request_hash
    )
    if not claim.inserted:
        return await _replay(session, user, body, claim, request_hash, now=now)
    if body.method != "food":
        raise _api_error("INVALID_INPUT", status_code=422)
    if locked.status != "away":
        raise _api_error("SPIRIT_NOT_AWAY", status_code=409)
    await _forward_recall(
        session,
        user,
        spirit_id=locked.id,
        event_id=body.client_id,
        status="away",
        timezone=locked.timezone,
        snapshot_version=locked.version,
        client_payload_hash=request_hash,
        now=now,
    )
    await recall_repo.finish_recall_home(
        session,
        owner_id=user.id,
        spirit_id=locked.id,
        now=now,
    )
    await recall_repo.complete_recall_idempotency(session, user.id, body.client_id, body.client_id)
    return await _project(
        session,
        user,
        client_id=body.client_id,
        method=body.method,
        now=now,
        memories=await _event_memories(session, locked.id, body.client_id),
        events=(
            MutationEvent(
                id=body.client_id,
                type=RECALL_MUTATION_EVENT,
                occurred_at=utc_iso(now),
            ),
        ),
    )


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
    spirit_id = await recall_repo.lock_owned_spirit(session, user.id)
    memories: tuple[MemoryPublic, ...] = ()
    if spirit_id is not None:
        memories = await _event_memories(session, spirit_id.id, body.client_id)
    return await _project(
        session,
        user,
        client_id=body.client_id,
        method=body.method,
        now=now,
        memories=memories,
        events=(
            MutationEvent(
                id=body.client_id,
                type=RECALL_MUTATION_EVENT,
                occurred_at=utc_iso(now),
            ),
        ),
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


async def _forward_recall(
    session: AsyncSession,
    user: CurrentUser,
    *,
    spirit_id: uuid.UUID,
    event_id: uuid.UUID,
    status: SpiritStatus,
    timezone: str,
    snapshot_version: int,
    client_payload_hash: str,
    now: datetime,
) -> None:
    from datetime import UTC
    from zoneinfo import ZoneInfo

    moment = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    decision = await publish_user_action(
        session,
        user,
        spirit_id=spirit_id,
        action_type="recall",
        event_type="recall.completed",
        event_id=event_id,
        payload={"method": "food"},
        status=status,
        active_promise=False,
        knowledge_moderated=False,
        pact_accepted=False,
        usage_date=moment.astimezone(ZoneInfo(timezone)).date(),
        timezone=timezone,
        now=moment,
        client_payload_hash=client_payload_hash,
        snapshot_version=snapshot_version,
    )
    if not decision.accepted:
        raise _api_error(decision.rejection_code or "INVALID_INPUT", status_code=422)
