"""Receiver-only postcard persistence. Spec §§7.3–7.4, 14.4–14.5."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_PUBLIC_RETURNING = (
    "postcard_id, created_at, read_at, visit_id, body, sender, npc, visit"
)
_LIST_SQL = (
    "SELECT "
    f"{_PUBLIC_RETURNING} "
    "FROM private.list_postcards_page("
    ":owner_spirit_id, :unread_only, :snapshot_at, :fetch_limit, "
    ":cursor_time, :cursor_id)"
)
_ITEM_SQL = f"SELECT {_PUBLIC_RETURNING} FROM private.postcard_public(:postcard_id)"


@dataclass(frozen=True, slots=True)
class PostcardPublicRow:
    postcard_id: uuid.UUID
    created_at: datetime
    read_at: datetime | None
    visit_id: uuid.UUID
    body: str
    sender: dict[str, Any] | None
    npc: dict[str, Any] | None
    visit: dict[str, Any]


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


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _json_object(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)
    raise RuntimeError("postcard json column was not an object")


def _public_row(row: Any) -> PostcardPublicRow:
    visit = _json_object(row.visit)
    if visit is None:
        raise RuntimeError("postcard visit payload is required")
    read_at = row.read_at
    return PostcardPublicRow(
        postcard_id=uuid.UUID(str(row.postcard_id)),
        created_at=_aware(row.created_at),
        read_at=None if read_at is None else _aware(read_at),
        visit_id=uuid.UUID(str(row.visit_id)),
        body=str(row.body),
        sender=_json_object(row.sender),
        npc=_json_object(row.npc),
        visit=visit,
    )


async def assert_writable_transaction(session: AsyncSession) -> None:
    flag = await session.scalar(text("SHOW transaction_read_only"))
    if str(flag).lower() == "on":
        raise RuntimeError("postcards cannot write in a read-only transaction")


async def fetch_transaction_now(session: AsyncSession) -> datetime:
    value = await session.scalar(text("SELECT now()"))
    if not isinstance(value, datetime):
        raise RuntimeError("database now() did not return a timestamp")
    return _aware(value)


async def fetch_owned_spirit_id(session: AsyncSession, owner_id: uuid.UUID) -> uuid.UUID | None:
    value = await session.scalar(
        text("SELECT id FROM public.spirits WHERE user_id = :user_id"),
        {"user_id": owner_id},
    )
    if value is None:
        return None
    return uuid.UUID(str(value))


async def lock_owned_spirit(session: AsyncSession, owner_id: uuid.UUID) -> SpiritLock | None:
    row = (
        await session.execute(
            text(
                "SELECT id, version FROM public.spirits "
                "WHERE user_id = :user_id FOR UPDATE"
            ),
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


async def claim_postcard_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    request_hash: str,
    *,
    operation: str,
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
    raise RuntimeError("postcard idempotency claim failed")


async def complete_postcard_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    resource_id: uuid.UUID,
    *,
    operation: str,
) -> None:
    await session.execute(
        text(
            "UPDATE public.idempotency_records "
            "SET status = 'completed', resource_type = 'postcard', "
            "resource_id = :resource_id, completed_at = now() "
            "WHERE user_id = :user_id AND operation = :operation "
            "AND client_id = :client_id"
        ),
        {
            "user_id": owner_id,
            "operation": operation,
            "client_id": client_id,
            "resource_id": resource_id,
        },
    )


async def mark_postcard_read(
    session: AsyncSession, *, spirit_id: uuid.UUID, postcard_id: uuid.UUID
) -> datetime | None:
    value = await session.scalar(
        text(
            "UPDATE public.postcards SET read_at = now() "
            "WHERE id = :id AND receiver_spirit_id = :spirit_id "
            "AND read_at IS NULL RETURNING read_at"
        ),
        {"id": postcard_id, "spirit_id": spirit_id},
    )
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise RuntimeError("postcard read_at did not return a timestamp")
    return _aware(value)


async def fetch_postcard_public(
    session: AsyncSession, postcard_id: uuid.UUID
) -> PostcardPublicRow | None:
    row = (await session.execute(text(_ITEM_SQL), {"postcard_id": postcard_id})).first()
    if row is None:
        return None
    return _public_row(row)


async def list_postcards_keyset(
    session: AsyncSession,
    *,
    owner_spirit_id: uuid.UUID,
    unread_only: bool,
    snapshot_at: datetime,
    fetch_limit: int,
    cursor_time: datetime | None = None,
    cursor_id: uuid.UUID | None = None,
) -> list[PostcardPublicRow]:
    rows = (
        await session.execute(
            text(_LIST_SQL),
            {
                "owner_spirit_id": owner_spirit_id,
                "unread_only": unread_only,
                "snapshot_at": snapshot_at,
                "fetch_limit": fetch_limit,
                "cursor_time": cursor_time,
                "cursor_id": cursor_id,
            },
        )
    ).all()
    return [_public_row(row) for row in rows]
