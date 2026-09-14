"""Settle feeds and promise mutations. Spec §§8.4, 8.4.1, 11.1, 11.2."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, public_error_message
from app.core.logging import get_logger, hash_user_id
from app.core.security import CurrentUser
from app.domain.cursor import utc_iso
from app.domain.feed import (
    DAILY_LIMITS,
    EMOTION_EFFECTS,
    FEED_CANCEL_OPERATION,
    FEED_COMPLETE_OPERATION,
    FEED_EVENT_TYPE,
    FEED_PATCH_OPERATION,
    FOOD_ENERGY_DELTA,
    FOOD_HUNGER_DELTA,
    GROWTH_EVENT_TYPE,
    GROWTH_PROMISE_COMPLETED,
    KNOWLEDGE_CONFIDENCE,
    KNOWLEDGE_SALIENCE,
    PROMISE_COMPLETE_BOND_DELTA,
    PROMISE_COMPLETED_EVENT_TYPE,
    SETTLEABLE_KINDS,
    feed_request_hash,
    knowledge_summary,
    promise_mutation_hash,
    remind_at_is_future,
)
from app.domain.location_sight import (
    SIGHT_CONFIDENCE,
    SIGHT_SALIENCE,
    location_privacy_reason,
    location_sight_summary,
)
from app.domain.quota import QuotaSnapshot, quota_usage
from app.providers.types import MemoryType
from app.repositories import bootstrap as bootstrap_repo
from app.repositories import feed as feed_repo
from app.schemas.bootstrap import RoomPublic
from app.schemas.feed import (
    FeedCreatePromise,
    FeedCreateRequest,
    FeedCreateSight,
    PromiseActionRequest,
    PromisePatchRequest,
)
from app.schemas.jsonb import PromisePayload, SightLocationPayload, SightPhotoPayload
from app.schemas.memory import MemoryPublic, MemoryStatus
from app.schemas.spirit import MutationEvent, QuotaUsage, SpiritPublic
from app.services.bootstrap import room_public_from_aggregate, spirit_public_from_bootstrap_row
from app.services.evolution import maybe_advance_stage
from app.services.growth import record_and_apply
from app.services.quota import consume as consume_quota

_LOGGER = get_logger(component="feed")


@dataclass(frozen=True, slots=True)
class FeedSettlement:
    feed: feed_repo.FeedRow
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


async def _record_growth(
    session: AsyncSession,
    user: CurrentUser,
    *,
    spirit_id: uuid.UUID,
    event_type: str,
    source_id: uuid.UUID,
    payload: dict[str, int],
    now: datetime,
    touch_interact: bool,
) -> uuid.UUID:
    recorded = await record_and_apply(
        session,
        owner_id=user.id,
        spirit_id=spirit_id,
        event_type=event_type,
        source_id=source_id,
        payload=payload,
        now=now,
        touch_interact=touch_interact,
    )
    await maybe_advance_stage(session, owner_id=user.id, spirit_id=spirit_id, now=now)
    return recorded.event_id


async def settle_feed(
    session: AsyncSession,
    user: CurrentUser,
    body: FeedCreateRequest,
    *,
    now: datetime,
) -> FeedSettlement:
    await feed_repo.assert_writable_transaction(session)
    if body.kind not in SETTLEABLE_KINDS:
        raise _api_error("INVALID_INPUT", status_code=422)
    payload = body.payload.model_dump(mode="json")
    if body.kind == "knowledge":
        summary = knowledge_summary(str(payload.get("text", "")))
        if not summary:
            raise _api_error("INVALID_INPUT", status_code=422)
    else:
        summary = None
    if isinstance(body, FeedCreatePromise) and not remind_at_is_future(body.payload.remind_at, now):
        raise _api_error("INVALID_INPUT", status_code=422)
    request_hash = feed_request_hash(kind=body.kind, payload=payload)
    locked = await feed_repo.lock_owned_spirit(session, user.id)
    if locked is None:
        raise _api_error("NOT_FOUND", status_code=404)
    claim = await feed_repo.claim_feed_idempotency(session, user.id, body.client_id, request_hash)
    if not claim.inserted:
        return await _replay_create(session, user, claim, request_hash, body.client_id, now=now)
    if isinstance(body, FeedCreatePromise):
        return await _insert_promise(
            session,
            user,
            locked=locked,
            client_id=body.client_id,
            payload=payload,
            remind_at=body.payload.remind_at,
            now=now,
        )
    if isinstance(body, FeedCreateSight) and isinstance(body.payload, SightLocationPayload):
        return await _settle_location_sight(
            session,
            user,
            locked=locked,
            client_id=body.client_id,
            payload=payload,
            label=body.payload.label,
            city=body.payload.city,
            category=body.payload.category,
            now=now,
        )
    if isinstance(body, FeedCreateSight) and isinstance(body.payload, SightPhotoPayload):
        return await _settle_photo_pending(
            session,
            user,
            locked=locked,
            client_id=body.client_id,
            payload=payload,
            now=now,
        )
    if isinstance(body, FeedCreateSight):
        raise _api_error("INVALID_INPUT", status_code=422)
    limit_value = DAILY_LIMITS.get(body.kind)
    if limit_value is None:
        raise _api_error("INVALID_INPUT", status_code=422)
    await consume_quota(
        session,
        user.id,
        body.kind,
        now=now,
        timezone=locked.timezone,
        limit_value=limit_value,
    )
    hunger_delta = 0
    energy_delta = 0
    mood_delta = 0
    if body.kind == "food":
        hunger_delta = FOOD_HUNGER_DELTA
        energy_delta = FOOD_ENERGY_DELTA
    elif body.kind == "emotion":
        emotion = str(payload["emotion"])
        mood_delta = EMOTION_EFFECTS[emotion].mood_delta
    feed_id = uuid.uuid4()
    feed = await feed_repo.insert_accepted_feed(
        session,
        feed_id=feed_id,
        owner_id=user.id,
        spirit_id=locked.id,
        client_id=body.client_id,
        kind=body.kind,
        payload=payload,
        now=now,
    )
    event_id = await _record_growth(
        session,
        user,
        spirit_id=locked.id,
        event_type=GROWTH_EVENT_TYPE,
        source_id=feed.id,
        payload={
            key: value
            for key, value in {
                "hunger_delta": hunger_delta,
                "energy_delta": energy_delta,
                "mood_delta": mood_delta,
            }.items()
            if value
        },
        now=now,
        touch_interact=True,
    )
    memory = None
    if body.kind == "knowledge" and summary is not None:
        memory = await feed_repo.insert_knowledge_memory(
            session,
            memory_id=uuid.uuid4(),
            spirit_id=locked.id,
            feed_id=feed.id,
            summary=summary,
            salience=KNOWLEDGE_SALIENCE,
            confidence=KNOWLEDGE_CONFIDENCE,
        )
    await feed_repo.complete_feed_idempotency(session, user.id, body.client_id, feed.id)
    memories: tuple[MemoryPublic, ...] = ()
    if memory is not None:
        memories = (_public_memory(memory),)
    return await _settlement_from_existing(
        session,
        user,
        feed,
        now=now,
        memories=memories,
        events=(MutationEvent(id=event_id, type=FEED_EVENT_TYPE, occurred_at=utc_iso(now)),),
    )


async def patch_promise(
    session: AsyncSession,
    user: CurrentUser,
    feed_id: uuid.UUID,
    body: PromisePatchRequest,
    *,
    now: datetime,
) -> FeedSettlement:
    await feed_repo.assert_writable_transaction(session)
    if not remind_at_is_future(body.remind_at, now):
        raise _api_error("INVALID_INPUT", status_code=422)
    payload = PromisePayload(text=body.text, remind_at=body.remind_at).model_dump(mode="json")
    request_hash = promise_mutation_hash(
        action="patch",
        feed_id=feed_id,
        expected_version=body.expected_version,
        text=body.text,
        remind_at=str(payload["remind_at"]),
    )
    locked = await feed_repo.lock_owned_spirit(session, user.id)
    if locked is None:
        raise _api_error("NOT_FOUND", status_code=404)
    claim = await feed_repo.claim_feed_idempotency(
        session,
        user.id,
        body.client_id,
        request_hash,
        operation=FEED_PATCH_OPERATION,
    )
    if not claim.inserted:
        return await _replay_mutation(
            session,
            user,
            claim,
            request_hash,
            body.client_id,
            feed_id,
            now=now,
        )
    feed = await feed_repo.lock_owned_feed(session, owner_id=user.id, feed_id=feed_id)
    if feed is None:
        raise _api_error("NOT_FOUND", status_code=404)
    _require_active_promise(feed)
    if body.expected_version != locked.version:
        raise _api_error("CONFLICT", status_code=409, details={"snapshot_version": locked.version})
    updated = await feed_repo.update_active_promise(
        session,
        owner_id=user.id,
        feed_id=feed_id,
        payload=payload,
        remind_at=body.remind_at,
    )
    if updated is None:
        raise _api_error("PROMISE_NOT_ACTIVE", status_code=409)
    await feed_repo.apply_spirit_deltas(
        session,
        owner_id=user.id,
        spirit_id=locked.id,
        hunger_delta=0,
        energy_delta=0,
        mood_delta=0,
        now=now,
        bond_delta=0,
        touch_interact=False,
    )
    await feed_repo.complete_feed_idempotency(
        session,
        user.id,
        body.client_id,
        updated.id,
        operation=FEED_PATCH_OPERATION,
    )
    return await _settlement_from_existing(session, user, updated, now=now, events=())


async def complete_promise(
    session: AsyncSession,
    user: CurrentUser,
    feed_id: uuid.UUID,
    body: PromiseActionRequest,
    *,
    now: datetime,
) -> FeedSettlement:
    await feed_repo.assert_writable_transaction(session)
    request_hash = promise_mutation_hash(
        action="complete", feed_id=feed_id, expected_version=body.expected_version
    )
    locked = await feed_repo.lock_owned_spirit(session, user.id)
    if locked is None:
        raise _api_error("NOT_FOUND", status_code=404)
    claim = await feed_repo.claim_feed_idempotency(
        session,
        user.id,
        body.client_id,
        request_hash,
        operation=FEED_COMPLETE_OPERATION,
    )
    if not claim.inserted:
        return await _replay_mutation(
            session,
            user,
            claim,
            request_hash,
            body.client_id,
            feed_id,
            now=now,
            event_type=GROWTH_PROMISE_COMPLETED,
            event_name=PROMISE_COMPLETED_EVENT_TYPE,
        )
    feed = await feed_repo.lock_owned_feed(session, owner_id=user.id, feed_id=feed_id)
    if feed is None:
        raise _api_error("NOT_FOUND", status_code=404)
    _require_active_promise(feed)
    if body.expected_version != locked.version:
        raise _api_error("CONFLICT", status_code=409, details={"snapshot_version": locked.version})
    updated = await feed_repo.complete_active_promise(
        session, owner_id=user.id, feed_id=feed_id, now=now
    )
    if updated is None:
        raise _api_error("PROMISE_NOT_ACTIVE", status_code=409)
    event_id = await _record_growth(
        session,
        user,
        spirit_id=locked.id,
        event_type=GROWTH_PROMISE_COMPLETED,
        source_id=updated.id,
        payload={"bond_delta": PROMISE_COMPLETE_BOND_DELTA},
        now=now,
        touch_interact=True,
    )
    await feed_repo.complete_feed_idempotency(
        session,
        user.id,
        body.client_id,
        updated.id,
        operation=FEED_COMPLETE_OPERATION,
    )
    return await _settlement_from_existing(
        session,
        user,
        updated,
        now=now,
        events=(
            MutationEvent(
                id=event_id,
                type=PROMISE_COMPLETED_EVENT_TYPE,
                occurred_at=utc_iso(now),
            ),
        ),
    )


async def cancel_promise(
    session: AsyncSession,
    user: CurrentUser,
    feed_id: uuid.UUID,
    body: PromiseActionRequest,
    *,
    now: datetime,
) -> FeedSettlement:
    await feed_repo.assert_writable_transaction(session)
    request_hash = promise_mutation_hash(
        action="cancel", feed_id=feed_id, expected_version=body.expected_version
    )
    locked = await feed_repo.lock_owned_spirit(session, user.id)
    if locked is None:
        raise _api_error("NOT_FOUND", status_code=404)
    claim = await feed_repo.claim_feed_idempotency(
        session,
        user.id,
        body.client_id,
        request_hash,
        operation=FEED_CANCEL_OPERATION,
    )
    if not claim.inserted:
        return await _replay_mutation(
            session,
            user,
            claim,
            request_hash,
            body.client_id,
            feed_id,
            now=now,
        )
    feed = await feed_repo.lock_owned_feed(session, owner_id=user.id, feed_id=feed_id)
    if feed is None:
        raise _api_error("NOT_FOUND", status_code=404)
    _require_active_promise(feed)
    if body.expected_version != locked.version:
        raise _api_error("CONFLICT", status_code=409, details={"snapshot_version": locked.version})
    updated = await feed_repo.cancel_active_promise(session, owner_id=user.id, feed_id=feed_id)
    if updated is None:
        raise _api_error("PROMISE_NOT_ACTIVE", status_code=409)
    await feed_repo.apply_spirit_deltas(
        session,
        owner_id=user.id,
        spirit_id=locked.id,
        hunger_delta=0,
        energy_delta=0,
        mood_delta=0,
        now=now,
        bond_delta=0,
        touch_interact=False,
    )
    await feed_repo.complete_feed_idempotency(
        session,
        user.id,
        body.client_id,
        updated.id,
        operation=FEED_CANCEL_OPERATION,
    )
    return await _settlement_from_existing(session, user, updated, now=now, events=())


async def _settle_photo_pending(
    session: AsyncSession,
    user: CurrentUser,
    *,
    locked: feed_repo.LockedFeedSpirit,
    client_id: uuid.UUID,
    payload: dict[str, Any],
    now: datetime,
) -> FeedSettlement:
    await consume_quota(
        session,
        user.id,
        "sight",
        now=now,
        timezone=locked.timezone,
        limit_value=DAILY_LIMITS["sight"],
    )
    feed = await feed_repo.insert_accepted_feed(
        session,
        feed_id=uuid.uuid4(),
        owner_id=user.id,
        spirit_id=locked.id,
        client_id=client_id,
        kind="sight",
        payload=payload,
        now=now,
        status="pending",
        effect_applied_at=None,
    )
    await feed_repo.apply_spirit_deltas(
        session,
        owner_id=user.id,
        spirit_id=locked.id,
        hunger_delta=0,
        energy_delta=0,
        mood_delta=0,
        now=now,
        bond_delta=0,
        touch_interact=True,
    )
    await feed_repo.complete_feed_idempotency(session, user.id, client_id, feed.id)
    return await _settlement_from_existing(session, user, feed, now=now, events=())


async def _settle_location_sight(
    session: AsyncSession,
    user: CurrentUser,
    *,
    locked: feed_repo.LockedFeedSpirit,
    client_id: uuid.UUID,
    payload: dict[str, Any],
    label: str,
    city: str,
    category: str | None,
    now: datetime,
) -> FeedSettlement:
    await consume_quota(
        session,
        user.id,
        "sight",
        now=now,
        timezone=locked.timezone,
        limit_value=DAILY_LIMITS["sight"],
    )
    reason = location_privacy_reason(label, city)
    rejected = reason is not None
    feed = await feed_repo.insert_accepted_feed(
        session,
        feed_id=uuid.uuid4(),
        owner_id=user.id,
        spirit_id=locked.id,
        client_id=client_id,
        kind="sight",
        payload=payload,
        now=now,
        status="rejected" if rejected else "accepted",
        rejection_code="MODERATION_REJECTED" if rejected else None,
    )
    if rejected:
        await feed_repo.apply_spirit_deltas(
            session,
            owner_id=user.id,
            spirit_id=locked.id,
            hunger_delta=0,
            energy_delta=0,
            mood_delta=0,
            now=now,
            bond_delta=0,
            touch_interact=False,
        )
    _LOGGER.info(
        "location_sight_decision",
        outcome="rejected" if rejected else "accepted",
        reason=reason or "allow",
        user_id_hash=hash_user_id(user.id),
        source="location",
    )
    memories: tuple[MemoryPublic, ...] = ()
    events: tuple[MutationEvent, ...] = ()
    if not rejected:
        tags = [category] if category is not None else []
        memory = await feed_repo.insert_knowledge_memory(
            session,
            memory_id=uuid.uuid4(),
            spirit_id=locked.id,
            feed_id=feed.id,
            summary=location_sight_summary(label=label, city=city),
            salience=SIGHT_SALIENCE,
            confidence=SIGHT_CONFIDENCE,
            memory_type="sight",
            tags=tags,
        )
        memories = (_public_memory(memory),)
        event_id = await _record_growth(
            session,
            user,
            spirit_id=locked.id,
            event_type=GROWTH_EVENT_TYPE,
            source_id=feed.id,
            payload={},
            now=now,
            touch_interact=True,
        )
        events = (MutationEvent(id=event_id, type=FEED_EVENT_TYPE, occurred_at=utc_iso(now)),)
    await feed_repo.complete_feed_idempotency(session, user.id, client_id, feed.id)
    return await _settlement_from_existing(
        session, user, feed, now=now, memories=memories, events=events
    )


async def feed_settlement_for(
    session: AsyncSession,
    user: CurrentUser,
    feed: feed_repo.FeedRow,
    *,
    now: datetime,
    memories: tuple[MemoryPublic, ...] = (),
    events: tuple[MutationEvent, ...] = (),
) -> FeedSettlement:
    return await _settlement_from_existing(
        session, user, feed, now=now, memories=memories, events=events
    )


async def _insert_promise(
    session: AsyncSession,
    user: CurrentUser,
    *,
    locked: feed_repo.LockedFeedSpirit,
    client_id: uuid.UUID,
    payload: dict[str, Any],
    remind_at: datetime,
    now: datetime,
) -> FeedSettlement:
    existing = await feed_repo.fetch_active_promise(session, spirit_id=locked.id)
    if existing is not None:
        raise _api_error("CONFLICT", status_code=409)
    feed = await feed_repo.insert_accepted_feed(
        session,
        feed_id=uuid.uuid4(),
        owner_id=user.id,
        spirit_id=locked.id,
        client_id=client_id,
        kind="promise",
        payload=payload,
        now=now,
        effect_applied_at=None,
        promise_status="active",
        remind_at=remind_at,
    )
    event_id = await _record_growth(
        session,
        user,
        spirit_id=locked.id,
        event_type=GROWTH_EVENT_TYPE,
        source_id=feed.id,
        payload={},
        now=now,
        touch_interact=True,
    )
    await feed_repo.complete_feed_idempotency(session, user.id, client_id, feed.id)
    return await _settlement_from_existing(
        session,
        user,
        feed,
        now=now,
        events=(MutationEvent(id=event_id, type=FEED_EVENT_TYPE, occurred_at=utc_iso(now)),),
    )


async def _replay_create(
    session: AsyncSession,
    user: CurrentUser,
    claim: feed_repo.IdempotencyClaim,
    request_hash: str,
    client_id: uuid.UUID,
    *,
    now: datetime,
) -> FeedSettlement:
    _reject_stale_claim(claim, request_hash)
    feed = await _load_claimed_feed(session, user, claim, client_id)
    if feed.status in {"rejected", "pending", "processing"}:
        return await _settlement_from_existing(session, user, feed, now=now, events=())
    event = await _ensure_event(
        session,
        feed,
        now=now,
        event_type=GROWTH_EVENT_TYPE,
        event_name=FEED_EVENT_TYPE,
    )
    return await _settlement_from_existing(session, user, feed, now=now, events=(event,))


async def _replay_mutation(
    session: AsyncSession,
    user: CurrentUser,
    claim: feed_repo.IdempotencyClaim,
    request_hash: str,
    client_id: uuid.UUID,
    feed_id: uuid.UUID,
    *,
    now: datetime,
    event_type: str | None = None,
    event_name: str | None = None,
) -> FeedSettlement:
    _reject_stale_claim(claim, request_hash)
    feed = await _load_claimed_feed(session, user, claim, client_id, feed_id=feed_id)
    events: tuple[MutationEvent, ...] = ()
    if event_type is not None and event_name is not None:
        event = await _ensure_event(
            session, feed, now=now, event_type=event_type, event_name=event_name
        )
        events = (event,)
    return await _settlement_from_existing(session, user, feed, now=now, events=events)


async def _load_claimed_feed(
    session: AsyncSession,
    user: CurrentUser,
    claim: feed_repo.IdempotencyClaim,
    client_id: uuid.UUID,
    *,
    feed_id: uuid.UUID | None = None,
) -> feed_repo.FeedRow:
    feed = None
    if claim.resource_id is not None:
        feed = await feed_repo.fetch_feed_by_id(
            session, owner_id=user.id, feed_id=claim.resource_id
        )
    if feed is None and feed_id is not None:
        feed = await feed_repo.fetch_feed_by_id(session, owner_id=user.id, feed_id=feed_id)
    if feed is None:
        feed = await feed_repo.fetch_feed_by_client_id(
            session, owner_id=user.id, client_id=client_id
        )
    if feed is None:
        raise _api_error("NOT_FOUND", status_code=404)
    return feed


async def _ensure_event(
    session: AsyncSession,
    feed: feed_repo.FeedRow,
    *,
    now: datetime,
    event_type: str,
    event_name: str,
) -> MutationEvent:
    event_id = await feed_repo.fetch_growth_event_id(
        session, feed_id=feed.id, event_type=event_type
    )
    if event_id is None:
        raise _api_error("NOT_FOUND", status_code=404)
    occurred = feed.completed_at or feed.effect_applied_at or feed.created_at or now
    return MutationEvent(id=event_id, type=event_name, occurred_at=utc_iso(occurred))


def _reject_stale_claim(claim: feed_repo.IdempotencyClaim, expected_hash: str) -> None:
    if claim.request_hash != expected_hash:
        raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
    if claim.status == "in_progress":
        raise _api_error("IDEMPOTENCY_IN_PROGRESS", status_code=409, retryable=True)
    if claim.status == "conflict":
        raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
    if claim.status != "completed":
        raise _api_error("IDEMPOTENCY_IN_PROGRESS", status_code=409, retryable=True)


def _require_active_promise(feed: feed_repo.FeedRow) -> None:
    if feed.kind != "promise" or feed.promise_status != "active":
        raise _api_error("PROMISE_NOT_ACTIVE", status_code=409)


async def _settlement_from_existing(
    session: AsyncSession,
    user: CurrentUser,
    feed: feed_repo.FeedRow,
    *,
    now: datetime,
    memories: tuple[MemoryPublic, ...] = (),
    events: tuple[MutationEvent, ...] = (),
) -> FeedSettlement:
    rows = await bootstrap_repo.load_bootstrap_aggregate_rows(
        session, user.id, now=now, require_read_snapshot=False
    )
    if rows.spirit is None:
        raise _api_error("NOT_FOUND", status_code=404)
    loaded_memories = memories
    if not loaded_memories and feed.kind in {"knowledge", "sight"} and feed.status == "accepted":
        memory = await feed_repo.fetch_memory_by_source_feed(
            session, spirit_id=rows.spirit.id, feed_id=feed.id
        )
        if memory is not None:
            loaded_memories = (_public_memory(memory),)
    return FeedSettlement(
        feed=feed,
        snapshot_version=rows.spirit.version,
        spirit=spirit_public_from_bootstrap_row(rows.spirit),
        room=room_public_from_aggregate(rows, now=now),
        memories=loaded_memories,
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


def _public_memory(row: feed_repo.MemoryRow) -> MemoryPublic:
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
