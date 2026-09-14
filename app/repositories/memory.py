"""Owner-filtered memory pages. Spec §§12.1, 15.1. Keyset only."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import Text

_ITEM_SELECT = (
    "SELECT m.id, m.type, m.summary, m.tags, m.salience, m.confidence, "
    "m.status, m.version, m.created_at "
    "FROM public.memories m "
    "WHERE m.spirit_id = :spirit_id AND m.status = 'active' "
    "AND m.created_at <= :snapshot_at"
)
_TOMBSTONE_SELECT = (
    "SELECT m.id, m.deleted_at "
    "FROM public.memories m "
    "WHERE m.spirit_id = :spirit_id AND m.status = 'deleted' "
    "AND m.deleted_at IS NOT NULL AND m.deleted_at <= :snapshot_at"
)
_TYPE_FILTER = " AND m.type = ANY(:types)"
_ORDER_LIMIT = " ORDER BY m.created_at DESC, m.id DESC LIMIT :fetch_limit"
_TOMBSTONE_ORDER_LIMIT = " ORDER BY m.deleted_at DESC, m.id DESC LIMIT :fetch_limit"
_KEYSET = (
    " AND (m.created_at < :cursor_time OR (m.created_at = :cursor_time AND m.id < :cursor_id))"
)
TOMBSTONE_FETCH_LIMIT = 100


@dataclass(frozen=True, slots=True)
class MemoryListRow:
    id: uuid.UUID
    type: str
    summary: str
    tags: list[str]
    salience: int
    confidence: float
    status: str
    version: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MemoryTombstoneRow:
    id: uuid.UUID
    deleted_at: datetime


@dataclass(frozen=True, slots=True)
class MemoryMutationRow:
    id: uuid.UUID
    spirit_id: uuid.UUID
    type: str
    summary: str
    tags: list[str]
    salience: int
    confidence: float
    status: str
    version: int
    created_at: datetime
    source_message_id: uuid.UUID | None
    deleted_at: datetime | None
    sealed_at: datetime | None


@dataclass(frozen=True, slots=True)
class SpiritLock:
    id: uuid.UUID
    version: int


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    inserted: bool
    request_hash: str
    status: str
    resource_id: uuid.UUID | None


_MUTATION_RETURNING = (
    "id, spirit_id, type, summary, tags, salience, confidence, status, version, "
    "created_at, source_message_id, deleted_at, sealed_at"
)


async def fetch_transaction_now(session: AsyncSession) -> datetime:
    value = await session.scalar(text("SELECT now()"))
    if not isinstance(value, datetime):
        raise RuntimeError("database now() did not return a timestamp")
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def fetch_owned_spirit_id(session: AsyncSession, owner_id: uuid.UUID) -> uuid.UUID | None:
    value = await session.scalar(
        text("SELECT id FROM public.spirits WHERE user_id = :user_id"),
        {"user_id": owner_id},
    )
    if value is None:
        return None
    return uuid.UUID(str(value))


async def list_active_memories_keyset(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    snapshot_at: datetime,
    fetch_limit: int,
    types: tuple[str, ...] | None,
    cursor_time: datetime | None = None,
    cursor_id: uuid.UUID | None = None,
) -> list[MemoryListRow]:
    params: dict[str, Any] = {
        "spirit_id": spirit_id,
        "snapshot_at": snapshot_at,
        "fetch_limit": fetch_limit,
    }
    sql = _ITEM_SELECT
    if types is not None:
        sql += _TYPE_FILTER
        params["types"] = list(types)
    if cursor_time is not None and cursor_id is not None:
        sql += _KEYSET
        params["cursor_time"] = cursor_time
        params["cursor_id"] = cursor_id
    sql += _ORDER_LIMIT
    statement = text(sql)
    if types is not None:
        statement = statement.bindparams(bindparam("types", type_=ARRAY(Text())))
    rows = (await session.execute(statement, params)).all()
    return [_list_row(row) for row in rows]


async def list_deleted_tombstones(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    snapshot_at: datetime,
    types: tuple[str, ...] | None,
) -> list[MemoryTombstoneRow]:
    params: dict[str, Any] = {
        "spirit_id": spirit_id,
        "snapshot_at": snapshot_at,
        "fetch_limit": TOMBSTONE_FETCH_LIMIT,
    }
    sql = _TOMBSTONE_SELECT
    if types is not None:
        sql += _TYPE_FILTER
        params["types"] = list(types)
    sql += _TOMBSTONE_ORDER_LIMIT
    statement = text(sql)
    if types is not None:
        statement = statement.bindparams(bindparam("types", type_=ARRAY(Text())))
    rows = (await session.execute(statement, params)).all()
    return [_tombstone_row(row) for row in rows]


async def lock_owned_spirit(session: AsyncSession, owner_id: uuid.UUID) -> SpiritLock | None:
    row = (
        await session.execute(
            text("SELECT id, version FROM public.spirits WHERE user_id = :user_id FOR UPDATE"),
            {"user_id": owner_id},
        )
    ).first()
    if row is None:
        return None
    return SpiritLock(id=row.id, version=int(row.version))


async def bump_spirit_version(
    session: AsyncSession, *, owner_id: uuid.UUID, spirit_id: uuid.UUID
) -> int:
    value = await session.scalar(
        text(
            "UPDATE public.spirits SET version = version + 1 "
            "WHERE id = :id AND user_id = :user_id RETURNING version"
        ),
        {"id": spirit_id, "user_id": owner_id},
    )
    if value is None:
        raise RuntimeError("spirit version bump did not return a row")
    return int(value)


async def fetch_memory_for_update(
    session: AsyncSession, *, spirit_id: uuid.UUID, memory_id: uuid.UUID
) -> MemoryMutationRow | None:
    row = (
        await session.execute(
            text(
                f"SELECT {_MUTATION_RETURNING} FROM public.memories "
                "WHERE id = :id AND spirit_id = :spirit_id FOR UPDATE"
            ),
            {"id": memory_id, "spirit_id": spirit_id},
        )
    ).first()
    if row is None:
        return None
    return _mutation_row(row)


async def correct_active_memory(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    memory_id: uuid.UUID,
    expected_version: int,
    summary: str,
) -> MemoryMutationRow | None:
    row = (
        await session.execute(
            text(
                "UPDATE public.memories SET summary = :summary, version = version + 1, "
                "updated_at = now() "
                "WHERE id = :id AND spirit_id = :spirit_id "
                "AND status = 'active' AND version = :expected_version "
                f"RETURNING {_MUTATION_RETURNING}"
            ),
            {
                "id": memory_id,
                "spirit_id": spirit_id,
                "expected_version": expected_version,
                "summary": summary,
            },
        )
    ).first()
    if row is None:
        return None
    return _mutation_row(row)


async def seal_active_memory(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    memory_id: uuid.UUID,
    expected_version: int,
) -> MemoryMutationRow | None:
    row = (
        await session.execute(
            text(
                "UPDATE public.memories SET status = 'sealed', sealed_at = now(), "
                "version = version + 1, updated_at = now() "
                "WHERE id = :id AND spirit_id = :spirit_id "
                "AND status = 'active' AND version = :expected_version "
                f"RETURNING {_MUTATION_RETURNING}"
            ),
            {
                "id": memory_id,
                "spirit_id": spirit_id,
                "expected_version": expected_version,
            },
        )
    ).first()
    if row is None:
        return None
    return _mutation_row(row)


async def delete_current_memory(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    memory_id: uuid.UUID,
    expected_version: int,
) -> MemoryMutationRow | None:
    row = (
        await session.execute(
            text(
                "UPDATE public.memories SET status = 'deleted', deleted_at = now(), "
                "version = version + 1, updated_at = now() "
                "WHERE id = :id AND spirit_id = :spirit_id "
                "AND status IN ('active', 'sealed') AND version = :expected_version "
                f"RETURNING {_MUTATION_RETURNING}"
            ),
            {
                "id": memory_id,
                "spirit_id": spirit_id,
                "expected_version": expected_version,
            },
        )
    ).first()
    if row is None:
        return None
    return _mutation_row(row)


async def clear_undeleted_memories(session: AsyncSession, *, spirit_id: uuid.UUID) -> int:
    rows = (
        await session.execute(
            text(
                "UPDATE public.memories SET status = 'deleted', deleted_at = now(), "
                "version = version + 1, updated_at = now() "
                "WHERE spirit_id = :spirit_id AND status <> 'deleted' "
                "RETURNING id"
            ),
            {"spirit_id": spirit_id},
        )
    ).all()
    return len(rows)


async def deactivate_samples_for_source_message(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    source_message_id: uuid.UUID | None,
) -> None:
    if source_message_id is None:
        return
    await session.execute(
        text(
            "UPDATE public.style_samples SET status = 'inactive', updated_at = now() "
            "WHERE spirit_id = :spirit_id AND status = 'active' "
            "AND source_window_id = ("
            "SELECT conversation_window_id FROM public.messages "
            "WHERE id = :message_id AND spirit_id = :spirit_id"
            ")"
        ),
        {"spirit_id": spirit_id, "message_id": source_message_id},
    )


async def deactivate_all_samples(session: AsyncSession, *, spirit_id: uuid.UUID) -> int:
    rows = (
        await session.execute(
            text(
                "UPDATE public.style_samples SET status = 'inactive', updated_at = now() "
                "WHERE spirit_id = :spirit_id AND status = 'active' "
                "RETURNING id"
            ),
            {"spirit_id": spirit_id},
        )
    ).all()
    return len(rows)


async def claim_memory_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    operation: str,
    client_id: uuid.UUID,
    request_hash: str,
) -> IdempotencyClaim:
    params = {
        "user_id": owner_id,
        "operation": operation,
        "client_id": client_id,
        "request_hash": request_hash,
    }
    for _ in range(3):
        inserted = (
            await session.execute(
                text(
                    "INSERT INTO public.idempotency_records ("
                    "user_id, operation, client_id, request_hash, status"
                    ") VALUES ("
                    ":user_id, :operation, :client_id, :request_hash, 'in_progress'"
                    ") ON CONFLICT (user_id, operation, client_id) DO NOTHING "
                    "RETURNING request_hash, status, resource_id"
                ),
                params,
            )
        ).first()
        if inserted is not None:
            return IdempotencyClaim(
                inserted=True,
                request_hash=str(inserted.request_hash),
                status=str(inserted.status),
                resource_id=inserted.resource_id,
            )
        existing = (
            await session.execute(
                text(
                    "SELECT request_hash, status, resource_id "
                    "FROM public.idempotency_records "
                    "WHERE user_id = :user_id AND operation = :operation "
                    "AND client_id = :client_id "
                    "FOR UPDATE"
                ),
                params,
            )
        ).first()
        if existing is not None:
            return IdempotencyClaim(
                inserted=False,
                request_hash=str(existing.request_hash),
                status=str(existing.status),
                resource_id=existing.resource_id,
            )
    raise RuntimeError("memory idempotency claim failed")


async def complete_memory_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    operation: str,
    client_id: uuid.UUID,
    *,
    resource_type: str,
    resource_id: uuid.UUID,
) -> None:
    await session.execute(
        text(
            "UPDATE public.idempotency_records "
            "SET status = 'completed', resource_type = :resource_type, "
            "resource_id = :resource_id, completed_at = now() "
            "WHERE user_id = :user_id AND operation = :operation "
            "AND client_id = :client_id"
        ),
        {
            "user_id": owner_id,
            "operation": operation,
            "client_id": client_id,
            "resource_type": resource_type,
            "resource_id": resource_id,
        },
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _list_row(row: Any) -> MemoryListRow:
    tags = row.tags
    if tags is None:
        tag_list: list[str] = []
    else:
        tag_list = [str(item) for item in tags]
    confidence = row.confidence
    if isinstance(confidence, Decimal):
        confidence_value = float(confidence)
    else:
        confidence_value = float(confidence)
    return MemoryListRow(
        id=row.id,
        type=str(row.type),
        summary=str(row.summary),
        tags=tag_list,
        salience=int(row.salience),
        confidence=confidence_value,
        status=str(row.status),
        version=int(row.version),
        created_at=_aware(row.created_at),
    )


def _tombstone_row(row: Any) -> MemoryTombstoneRow:
    deleted_at = row.deleted_at
    if not isinstance(deleted_at, datetime):
        raise RuntimeError("memory deleted_at is not a timestamp")
    return MemoryTombstoneRow(id=row.id, deleted_at=_aware(deleted_at))


def _mutation_row(row: Any) -> MemoryMutationRow:
    listed = _list_row(row)
    deleted_at = row.deleted_at
    sealed_at = row.sealed_at
    source = row.source_message_id
    return MemoryMutationRow(
        id=listed.id,
        spirit_id=uuid.UUID(str(row.spirit_id)),
        type=listed.type,
        summary=listed.summary,
        tags=listed.tags,
        salience=listed.salience,
        confidence=listed.confidence,
        status=listed.status,
        version=listed.version,
        created_at=listed.created_at,
        source_message_id=uuid.UUID(str(source)) if source is not None else None,
        deleted_at=_aware(deleted_at) if isinstance(deleted_at, datetime) else None,
        sealed_at=_aware(sealed_at) if isinstance(sealed_at, datetime) else None,
    )
