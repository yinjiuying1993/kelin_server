"""POST /moderate-sight. Spec §§8.4, 11.4, 16.2, 16.5.

Owner is re-checked from JWT/claim. Storage get/delete uses an already
owner-checked key and never lists the bucket. Provider calls happen with
no row lock. Stub adapters must not be presented as live sight.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, public_error_message
from app.core.logging import get_logger, hash_user_id
from app.core.security import CurrentUser
from app.db.session import claimed_transaction
from app.domain.cursor import utc_iso
from app.domain.feed import FEED_EVENT_TYPE
from app.domain.location_sight import SIGHT_CONFIDENCE, SIGHT_SALIENCE
from app.domain.sight_moderate import (
    SAFETY_ALLOW,
    SAFETY_BLOCK,
    SIGHT_MODERATE_OPERATION,
    SightPropKind,
    allowed_prop,
    moderate_request_hash,
)
from app.domain.sight_upload import (
    InvalidSightUpload,
    assert_server_object_path,
    inspect_object_bytes,
    object_mismatch_reason,
)
from app.integrations.storage import (
    PrivateSightStorage,
    StorageListDenied,
    StorageObjectMissing,
)
from app.providers.contract import http_status_for_provider_code
from app.providers.errors import ProviderCancelled, ProviderError
from app.providers.factory import build_provider
from app.providers.protocol import BailianProvider
from app.providers.sight_schema import require_safety_output, require_vision_output
from app.providers.types import SafetyInput, VisionInput
from app.repositories import feed as feed_repo
from app.repositories import sight_upload as upload_repo
from app.schemas.memory import MemoryPublic
from app.schemas.moderate import ModerateSightRequest
from app.schemas.spirit import MutationEvent
from app.services.evolution import maybe_advance_stage
from app.services.feed import FeedSettlement, _public_memory, feed_settlement_for
from app.services.growth import record_and_apply

_LOGGER = get_logger(component="sight_moderate")


@dataclass(frozen=True, slots=True)
class ModerateSettlement:
    settlement: FeedSettlement
    prop: SightPropKind | None


@dataclass(frozen=True, slots=True)
class _Snapshot:
    feed: feed_repo.FeedRow
    upload: upload_repo.SightUploadRow
    spirit_id: uuid.UUID


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


def _payload_source(payload: object) -> str | None:
    if isinstance(payload, dict):
        source = payload.get("source")
        return str(source) if source is not None else None
    return None


def _assert_no_list(storage: PrivateSightStorage, bucket: str) -> None:
    try:
        storage.service_list(bucket, "")
    except StorageListDenied:
        return
    raise RuntimeError("moderate must not list the bucket")


def _prop_from_memory(memory: MemoryPublic | None) -> SightPropKind | None:
    if memory is None or not memory.tags:
        return None
    return allowed_prop(memory.tags[0])


async def moderate_sight(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    body: ModerateSightRequest,
    *,
    now: datetime,
    storage: PrivateSightStorage,
    provider: BailianProvider | None = None,
    settings: Settings | None = None,
) -> ModerateSettlement:
    request_hash = moderate_request_hash(
        feed_id=body.feed_id, upload_session_id=body.upload_session_id
    )
    async with claimed_transaction(factory, user) as session:
        snapshot, replay = await _claim_or_replay(
            session, user, body, request_hash=request_hash, now=now
        )
        if replay is not None:
            return replay
    assert snapshot is not None
    _assert_no_list(storage, snapshot.upload.bucket)
    try:
        body_bytes = storage.service_get(snapshot.upload.bucket, snapshot.upload.object_path)
    except StorageObjectMissing as exc:
        raise _api_error("UPLOAD_NOT_READY", status_code=409) from exc
    mismatch = object_mismatch_reason(
        inspect_object_bytes(body_bytes),
        expected_size=snapshot.upload.expected_size,
        expected_sha256=snapshot.upload.expected_sha256,
    )
    if mismatch is not None:
        deleted = storage.service_delete(snapshot.upload.bucket, snapshot.upload.object_path)
        _LOGGER.info(
            "sight_object_verify",
            outcome=mismatch,
            object_deleted=deleted,
            user_id_hash=hash_user_id(user.id),
            upload_session_id=str(snapshot.upload.id),
        )
        return await _finalize(
            factory,
            user,
            snapshot,
            now=now,
            accepted=False,
            object_deleted=deleted,
            request_hash=request_hash,
            body=body,
        )
    async with claimed_transaction(factory, user) as session:
        processing = await feed_repo.mark_sight_processing(
            session, owner_id=user.id, feed_id=snapshot.feed.id
        )
        verifying = await upload_repo.mark_verifying(
            session, owner_id=user.id, upload_id=snapshot.upload.id
        )
        if processing is None or verifying is None:
            current = await feed_repo.lock_owned_feed(
                session, owner_id=user.id, feed_id=snapshot.feed.id
            )
            if current is not None and current.status in {"accepted", "rejected"}:
                loaded = await feed_settlement_for(session, user, current, now=now)
                return ModerateSettlement(
                    settlement=loaded,
                    prop=_prop_from_memory(loaded.memories[0] if loaded.memories else None),
                )
            raise _api_error("CONFLICT", status_code=409)
    adapter = provider if provider is not None else build_provider(settings or get_settings())
    try:
        safety = require_safety_output(
            await adapter.moderate(
                SafetyInput(
                    sha256=snapshot.upload.expected_sha256,
                    size_bytes=snapshot.upload.expected_size,
                    body=body_bytes,
                )
            )
        )
        if safety.decision == SAFETY_BLOCK:
            deleted = storage.service_delete(snapshot.upload.bucket, snapshot.upload.object_path)
            return await _finalize(
                factory,
                user,
                snapshot,
                now=now,
                accepted=False,
                object_deleted=deleted,
                request_hash=request_hash,
                body=body,
            )
        if safety.decision != SAFETY_ALLOW:
            raise ProviderError("MODEL_UNAVAILABLE")
        vision = require_vision_output(
            await adapter.vision(
                VisionInput(
                    sha256=snapshot.upload.expected_sha256,
                    size_bytes=snapshot.upload.expected_size,
                    body=body_bytes,
                )
            )
        )
    except ProviderCancelled as exc:
        raise _api_error("MODEL_UNAVAILABLE", status_code=503, retryable=True) from exc
    except ProviderError as exc:
        status_code, retryable = http_status_for_provider_code(exc.code)
        raise _api_error(exc.code, status_code=status_code, retryable=retryable) from exc
    prop = allowed_prop(vision.prop)
    if prop is None:
        deleted = storage.service_delete(snapshot.upload.bucket, snapshot.upload.object_path)
        return await _finalize(
            factory,
            user,
            snapshot,
            now=now,
            accepted=False,
            object_deleted=deleted,
            request_hash=request_hash,
            body=body,
        )
    deleted = storage.service_delete(snapshot.upload.bucket, snapshot.upload.object_path)
    return await _finalize(
        factory,
        user,
        snapshot,
        now=now,
        accepted=True,
        object_deleted=deleted,
        request_hash=request_hash,
        body=body,
        summary=vision.summary,
        prop=prop,
    )


async def _claim_or_replay(
    session: AsyncSession,
    user: CurrentUser,
    body: ModerateSightRequest,
    *,
    request_hash: str,
    now: datetime,
) -> tuple[_Snapshot | None, ModerateSettlement | None]:
    await feed_repo.assert_writable_transaction(session)
    locked = await feed_repo.lock_owned_spirit(session, user.id)
    if locked is None:
        raise _api_error("NOT_FOUND", status_code=404)
    feed = await feed_repo.lock_owned_feed(session, owner_id=user.id, feed_id=body.feed_id)
    if feed is None or feed.kind != "sight" or _payload_source(feed.payload) != "photo":
        raise _api_error("NOT_FOUND", status_code=404)
    upload = await upload_repo.lock_owned_upload(
        session, owner_id=user.id, upload_id=body.upload_session_id
    )
    if upload is None or upload.feed_id != feed.id:
        raise _api_error("NOT_FOUND", status_code=404)
    try:
        assert_server_object_path(
            upload.object_path, user_id=user.id, feed_id=feed.id, upload_id=upload.id
        )
    except InvalidSightUpload as exc:
        raise _api_error("NOT_FOUND", status_code=404) from exc
    claim = await feed_repo.claim_feed_idempotency(
        session,
        user.id,
        body.client_id,
        request_hash,
        operation=SIGHT_MODERATE_OPERATION,
    )
    if feed.status in {"accepted", "rejected"} and feed.effect_applied_at is not None:
        loaded = await feed_settlement_for(session, user, feed, now=now)
        events = loaded.events
        if feed.status == "accepted" and not events:
            event_id = await feed_repo.fetch_growth_event_id(
                session, feed_id=feed.id, event_type="feed_accepted"
            )
            if event_id is not None:
                occurred = feed.effect_applied_at or now
                loaded = await feed_settlement_for(
                    session,
                    user,
                    feed,
                    now=now,
                    events=(
                        MutationEvent(
                            id=event_id, type=FEED_EVENT_TYPE, occurred_at=utc_iso(occurred)
                        ),
                    ),
                )
        if not claim.inserted:
            _reject_stale_hash(claim, request_hash)
        return None, ModerateSettlement(
            settlement=loaded,
            prop=_prop_from_memory(loaded.memories[0] if loaded.memories else None),
        )
    if upload.status == "expired" or upload.expires_at <= now:
        raise _api_error("UPLOAD_EXPIRED", status_code=410)
    if not claim.inserted:
        _reject_stale_hash(claim, request_hash)
    return _Snapshot(feed=feed, upload=upload, spirit_id=locked.id), None


def _reject_stale_hash(claim: feed_repo.IdempotencyClaim, expected_hash: str) -> None:
    if claim.request_hash != expected_hash:
        raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)


async def _finalize(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    snapshot: _Snapshot,
    *,
    now: datetime,
    accepted: bool,
    object_deleted: bool,
    request_hash: str,
    body: ModerateSightRequest,
    summary: str | None = None,
    prop: SightPropKind | None = None,
) -> ModerateSettlement:
    del request_hash
    async with claimed_transaction(factory, user) as session:
        locked = await feed_repo.lock_owned_spirit(session, user.id)
        if locked is None:
            raise _api_error("NOT_FOUND", status_code=404)
        settled_feed = await feed_repo.settle_sight_feed(
            session,
            owner_id=user.id,
            feed_id=snapshot.feed.id,
            status="accepted" if accepted else "rejected",
            rejection_code=None if accepted else "MODERATION_REJECTED",
            now=now,
        )
        current = settled_feed
        if current is None:
            current = await feed_repo.lock_owned_feed(
                session, owner_id=user.id, feed_id=snapshot.feed.id
            )
            if current is None:
                raise _api_error("NOT_FOUND", status_code=404)
            loaded = await feed_settlement_for(session, user, current, now=now)
            return ModerateSettlement(
                settlement=loaded,
                prop=_prop_from_memory(loaded.memories[0] if loaded.memories else None),
            )
        await upload_repo.mark_terminal(
            session,
            owner_id=user.id,
            upload_id=snapshot.upload.id,
            status="consumed" if accepted else "rejected",
            now=now,
            object_deleted=object_deleted,
        )
        await upload_repo.insert_cleanup_outbox(
            session, owner_id=user.id, upload_id=snapshot.upload.id
        )
        memories: tuple[MemoryPublic, ...] = ()
        events: tuple[MutationEvent, ...] = ()
        if accepted:
            if summary is None or prop is None:
                raise _api_error("INTERNAL_ERROR", status_code=500)
            memory = await feed_repo.insert_knowledge_memory(
                session,
                memory_id=uuid.uuid4(),
                spirit_id=locked.id,
                feed_id=snapshot.feed.id,
                summary=summary,
                salience=SIGHT_SALIENCE,
                confidence=SIGHT_CONFIDENCE,
                memory_type="sight",
                tags=[prop],
            )
            memories = (_public_memory(memory),)
            recorded = await record_and_apply(
                session,
                owner_id=user.id,
                spirit_id=locked.id,
                event_type="feed_accepted",
                source_id=snapshot.feed.id,
                payload={},
                now=now,
                touch_interact=True,
            )
            await maybe_advance_stage(session, owner_id=user.id, spirit_id=locked.id, now=now)
            events = (
                MutationEvent(id=recorded.event_id, type=FEED_EVENT_TYPE, occurred_at=utc_iso(now)),
            )
        await feed_repo.complete_feed_idempotency(
            session,
            user.id,
            body.client_id,
            snapshot.feed.id,
            operation=SIGHT_MODERATE_OPERATION,
            resource_type="feed",
        )
        _LOGGER.info(
            "sight_moderate_settled",
            outcome="accepted" if accepted else "rejected",
            object_deleted=object_deleted,
            user_id_hash=hash_user_id(user.id),
            upload_session_id=str(snapshot.upload.id),
            object_path=snapshot.upload.object_path,
        )
        loaded = await feed_settlement_for(
            session, user, current, now=now, memories=memories, events=events
        )
        return ModerateSettlement(settlement=loaded, prop=prop if accepted else None)
