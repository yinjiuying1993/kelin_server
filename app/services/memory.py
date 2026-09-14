"""Load owner memory pages and settle mutations. Spec §§12.1–12.4, 15.1."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser
from app.domain.cursor import (
    CURSOR_VERSION,
    InvalidCursor,
    MessageCursor,
    decode_memory_cursor,
    encode_memory_cursor,
    memories_filter_hash,
    utc_iso,
)
from app.domain.memory_list import memory_types_for_filter
from app.domain.memory_mutation import (
    CLEAR_OPERATION,
    DELETE_OPERATION,
    PATCH_OPERATION,
    clear_request_hash,
    delete_request_hash,
    patch_request_hash,
)
from app.providers.types import MemoryType
from app.repositories import memory as memory_repo
from app.schemas.memory import (
    MemoryClearRequest,
    MemoryDeleteRequest,
    MemoryFilter,
    MemoryPage,
    MemoryPatchRequest,
    MemoryPublic,
    MemoryStatus,
    MemoryTombstone,
)


def _invalid_cursor() -> ApiError:
    return ApiError(
        "INVALID_CURSOR",
        public_error_message("INVALID_CURSOR"),
        status_code=422,
        retryable=False,
    )


async def load_memory_page(
    session: AsyncSession,
    user: CurrentUser,
    *,
    memory_filter: MemoryFilter,
    cursor: str | None,
    limit: int,
    secret: bytes,
) -> MemoryPage:
    filter_hash = memories_filter_hash(user.id, memory_filter)
    types = memory_types_for_filter(memory_filter)
    cursor_time = None
    cursor_id = None
    if cursor is None:
        snapshot_at = await memory_repo.fetch_transaction_now(session)
    else:
        try:
            decoded = decode_memory_cursor(
                cursor,
                secret=secret,
                expected_filter_hash=filter_hash,
            )
        except InvalidCursor as exc:
            raise _invalid_cursor() from exc
        snapshot_at = decoded.snapshot_at
        cursor_time = decoded.sort_time
        cursor_id = decoded.id
    spirit_id = await memory_repo.fetch_owned_spirit_id(session, user.id)
    if spirit_id is None:
        return MemoryPage(
            items=[],
            tombstones=[],
            next_cursor=None,
            has_more=False,
            snapshot_at=utc_iso(snapshot_at),
        )
    rows = await memory_repo.list_active_memories_keyset(
        session,
        spirit_id=spirit_id,
        snapshot_at=snapshot_at,
        fetch_limit=limit + 1,
        types=types,
        cursor_time=cursor_time,
        cursor_id=cursor_id,
    )
    tombstones = await memory_repo.list_deleted_tombstones(
        session,
        spirit_id=spirit_id,
        snapshot_at=snapshot_at,
        types=types,
    )
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    items = [_public_memory(row) for row in page_rows]
    next_cursor = None
    if has_more:
        last = page_rows[-1]
        next_cursor = encode_memory_cursor(
            MessageCursor(
                version=CURSOR_VERSION,
                sort_time=last.created_at,
                id=last.id,
                snapshot_at=snapshot_at,
                filter_hash=filter_hash,
            ),
            secret=secret,
        )
    return MemoryPage(
        items=items,
        tombstones=[_public_tombstone(row) for row in tombstones],
        next_cursor=next_cursor,
        has_more=has_more,
        snapshot_at=utc_iso(snapshot_at),
    )


def _public_memory(row: memory_repo.MemoryListRow) -> MemoryPublic:
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


def _public_tombstone(row: memory_repo.MemoryTombstoneRow) -> MemoryTombstone:
    return MemoryTombstone(id=row.id, deleted_at=utc_iso(row.deleted_at))


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


@dataclass(frozen=True, slots=True)
class MemoryMutationSettlement:
    snapshot_version: int
    kind: str
    memory: memory_repo.MemoryMutationRow | None
    tombstone: memory_repo.MemoryTombstoneRow | None
    clear_client_id: UUID | None


def _api_error(code: str, *, status_code: int, retryable: bool = False) -> ApiError:
    return ApiError(
        code,
        public_error_message(code),
        status_code=status_code,
        retryable=retryable,
    )


async def patch_owned_memory(
    session: AsyncSession,
    user: CurrentUser,
    memory_id: UUID,
    body: MemoryPatchRequest,
) -> MemoryMutationSettlement:
    spirit = await memory_repo.lock_owned_spirit(session, user.id)
    if spirit is None:
        raise _api_error("NOT_FOUND", status_code=404)
    request_hash = patch_request_hash(memory_id, body)
    claim = await memory_repo.claim_memory_idempotency(
        session, user.id, PATCH_OPERATION, body.client_id, request_hash
    )
    if not claim.inserted:
        return await _replay_patch(session, spirit, memory_id, body, claim)
    row = await memory_repo.fetch_memory_for_update(
        session, spirit_id=spirit.id, memory_id=memory_id
    )
    if row is None:
        raise _api_error("NOT_FOUND", status_code=404)
    if row.status != "active":
        raise _api_error("MEMORY_NOT_ACTIVE", status_code=409)
    if body.action == "correct":
        if body.summary is None:
            raise _api_error("INVALID_INPUT", status_code=422)
        updated = await memory_repo.correct_active_memory(
            session,
            spirit_id=spirit.id,
            memory_id=memory_id,
            expected_version=body.expected_version,
            summary=body.summary,
        )
        kind = "correct"
    else:
        updated = await memory_repo.seal_active_memory(
            session,
            spirit_id=spirit.id,
            memory_id=memory_id,
            expected_version=body.expected_version,
        )
        kind = "seal"
        if updated is not None:
            await memory_repo.deactivate_samples_for_source_message(
                session,
                spirit_id=spirit.id,
                source_message_id=updated.source_message_id,
            )
    if updated is None:
        raise _api_error("CONFLICT", status_code=409)
    snapshot = await memory_repo.bump_spirit_version(session, owner_id=user.id, spirit_id=spirit.id)
    await memory_repo.complete_memory_idempotency(
        session,
        user.id,
        PATCH_OPERATION,
        body.client_id,
        resource_type="memory",
        resource_id=updated.id,
    )
    return MemoryMutationSettlement(
        snapshot_version=snapshot,
        kind=kind,
        memory=updated,
        tombstone=None,
        clear_client_id=None,
    )


async def delete_owned_memory(
    session: AsyncSession,
    user: CurrentUser,
    memory_id: UUID,
    body: MemoryDeleteRequest,
) -> MemoryMutationSettlement:
    spirit = await memory_repo.lock_owned_spirit(session, user.id)
    if spirit is None:
        raise _api_error("NOT_FOUND", status_code=404)
    request_hash = delete_request_hash(memory_id, body)
    claim = await memory_repo.claim_memory_idempotency(
        session, user.id, DELETE_OPERATION, body.client_id, request_hash
    )
    if not claim.inserted:
        return await _replay_delete(session, spirit, memory_id, body, claim)
    row = await memory_repo.fetch_memory_for_update(
        session, spirit_id=spirit.id, memory_id=memory_id
    )
    if row is None:
        raise _api_error("NOT_FOUND", status_code=404)
    if row.status == "deleted":
        if row.deleted_at is None:
            raise RuntimeError("deleted memory is missing deleted_at")
        await memory_repo.complete_memory_idempotency(
            session,
            user.id,
            DELETE_OPERATION,
            body.client_id,
            resource_type="memory",
            resource_id=row.id,
        )
        return MemoryMutationSettlement(
            snapshot_version=spirit.version,
            kind="delete",
            memory=row,
            tombstone=memory_repo.MemoryTombstoneRow(id=row.id, deleted_at=row.deleted_at),
            clear_client_id=None,
        )
    deleted = await memory_repo.delete_current_memory(
        session,
        spirit_id=spirit.id,
        memory_id=memory_id,
        expected_version=body.expected_version,
    )
    if deleted is None:
        raise _api_error("CONFLICT", status_code=409)
    if deleted.deleted_at is None:
        raise RuntimeError("delete did not set deleted_at")
    await memory_repo.deactivate_samples_for_source_message(
        session,
        spirit_id=spirit.id,
        source_message_id=deleted.source_message_id,
    )
    snapshot = await memory_repo.bump_spirit_version(session, owner_id=user.id, spirit_id=spirit.id)
    await memory_repo.complete_memory_idempotency(
        session,
        user.id,
        DELETE_OPERATION,
        body.client_id,
        resource_type="memory",
        resource_id=deleted.id,
    )
    return MemoryMutationSettlement(
        snapshot_version=snapshot,
        kind="delete",
        memory=deleted,
        tombstone=memory_repo.MemoryTombstoneRow(id=deleted.id, deleted_at=deleted.deleted_at),
        clear_client_id=None,
    )


async def clear_owned_memories(
    session: AsyncSession,
    user: CurrentUser,
    body: MemoryClearRequest,
) -> MemoryMutationSettlement:
    spirit = await memory_repo.lock_owned_spirit(session, user.id)
    if spirit is None:
        raise _api_error("NOT_FOUND", status_code=404)
    request_hash = clear_request_hash(body)
    claim = await memory_repo.claim_memory_idempotency(
        session, user.id, CLEAR_OPERATION, body.client_id, request_hash
    )
    if not claim.inserted:
        return await _replay_clear(spirit, body, claim)
    await memory_repo.clear_undeleted_memories(session, spirit_id=spirit.id)
    await memory_repo.deactivate_all_samples(session, spirit_id=spirit.id)
    snapshot = await memory_repo.bump_spirit_version(session, owner_id=user.id, spirit_id=spirit.id)
    await memory_repo.complete_memory_idempotency(
        session,
        user.id,
        CLEAR_OPERATION,
        body.client_id,
        resource_type="memory_clear",
        resource_id=body.client_id,
    )
    return MemoryMutationSettlement(
        snapshot_version=snapshot,
        kind="clear",
        memory=None,
        tombstone=None,
        clear_client_id=body.client_id,
    )


async def _replay_patch(
    session: AsyncSession,
    spirit: memory_repo.SpiritLock,
    memory_id: UUID,
    body: MemoryPatchRequest,
    claim: memory_repo.IdempotencyClaim,
) -> MemoryMutationSettlement:
    _reject_stale_claim(claim, patch_request_hash(memory_id, body))
    target = claim.resource_id or memory_id
    row = await memory_repo.fetch_memory_for_update(session, spirit_id=spirit.id, memory_id=target)
    if row is None:
        raise _api_error("NOT_FOUND", status_code=404)
    return MemoryMutationSettlement(
        snapshot_version=spirit.version,
        kind=body.action,
        memory=row,
        tombstone=None,
        clear_client_id=None,
    )


async def _replay_delete(
    session: AsyncSession,
    spirit: memory_repo.SpiritLock,
    memory_id: UUID,
    body: MemoryDeleteRequest,
    claim: memory_repo.IdempotencyClaim,
) -> MemoryMutationSettlement:
    _reject_stale_claim(claim, delete_request_hash(memory_id, body))
    target = claim.resource_id or memory_id
    row = await memory_repo.fetch_memory_for_update(session, spirit_id=spirit.id, memory_id=target)
    if row is None or row.deleted_at is None:
        raise _api_error("NOT_FOUND", status_code=404)
    return MemoryMutationSettlement(
        snapshot_version=spirit.version,
        kind="delete",
        memory=row,
        tombstone=memory_repo.MemoryTombstoneRow(id=row.id, deleted_at=row.deleted_at),
        clear_client_id=None,
    )


async def _replay_clear(
    spirit: memory_repo.SpiritLock,
    body: MemoryClearRequest,
    claim: memory_repo.IdempotencyClaim,
) -> MemoryMutationSettlement:
    _reject_stale_claim(claim, clear_request_hash(body))
    return MemoryMutationSettlement(
        snapshot_version=spirit.version,
        kind="clear",
        memory=None,
        tombstone=None,
        clear_client_id=body.client_id,
    )


def _reject_stale_claim(claim: memory_repo.IdempotencyClaim, expected_hash: str) -> None:
    if claim.request_hash != expected_hash:
        raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
    if claim.status == "in_progress":
        raise _api_error("IDEMPOTENCY_IN_PROGRESS", status_code=409, retryable=True)
    if claim.status == "conflict":
        raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
    if claim.status != "completed":
        raise _api_error("IDEMPOTENCY_IN_PROGRESS", status_code=409, retryable=True)
