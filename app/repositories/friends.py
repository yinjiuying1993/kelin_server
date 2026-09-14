"""Owner-filtered undirected friend edges. Spec §§7.3–7.4, 8.9, 14.1–14.3."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_PUBLIC_RETURNING = (
    "friend_id, created_at, peer_id, title, stage, public_marks, status"
)
_LIST_SQL = (
    "SELECT "
    f"{_PUBLIC_RETURNING} "
    "FROM private.list_friends_page("
    ":owner_spirit_id, :snapshot_at, :fetch_limit, :cursor_time, :cursor_id)"
)
_EDGE_SQL = (
    f"SELECT {_PUBLIC_RETURNING} FROM private.friend_edge_public(:friend_id)"
)


@dataclass(frozen=True, slots=True)
class FriendPublicRow:
    friend_id: uuid.UUID
    created_at: datetime
    peer_id: uuid.UUID
    title: str
    stage: str
    public_marks: tuple[str, ...]
    status: str


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


def _attr_str(obj: object, name: str) -> str | None:
    value = getattr(obj, name, None)
    if value is None:
        return None
    text_value = str(value)
    return text_value if text_value else None


def friend_definer_sqlstate(exc: BaseException) -> tuple[str | None, str]:
    sqlstate: str | None = None
    message = str(exc)
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        sqlstate = _attr_str(current, "sqlstate") or _attr_str(current, "pgcode") or sqlstate
        nxt = current.__cause__ or current.__context__
        orig = getattr(current, "orig", None)
        if isinstance(orig, BaseException) and orig is not current:
            current = orig
        elif isinstance(nxt, BaseException) and nxt is not current:
            current = nxt
        else:
            current = None
    return sqlstate, message


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _marks(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return ()


def _public_row(row: Any) -> FriendPublicRow:
    return FriendPublicRow(
        friend_id=uuid.UUID(str(row.friend_id)),
        created_at=_aware(row.created_at),
        peer_id=uuid.UUID(str(row.peer_id)),
        title=str(row.title),
        stage=str(row.stage),
        public_marks=_marks(row.public_marks),
        status=str(row.status),
    )


async def assert_writable_transaction(session: AsyncSession) -> None:
    flag = await session.scalar(text("SHOW transaction_read_only"))
    if str(flag).lower() == "on":
        raise RuntimeError("friends cannot write in a read-only transaction")


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


async def claim_friend_idempotency(
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
    raise RuntimeError("friend idempotency claim failed")


async def complete_friend_idempotency(
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
            "SET status = 'completed', resource_type = 'friend', "
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


async def add_friend_by_code(session: AsyncSession, invite_code: str) -> uuid.UUID:
    value = await session.scalar(
        text("SELECT private.add_friend_by_code(:invite_code)"),
        {"invite_code": invite_code},
    )
    if value is None:
        raise RuntimeError("add_friend_by_code did not return an id")
    return uuid.UUID(str(value))


async def remove_friend_edge(session: AsyncSession, friend_id: uuid.UUID) -> uuid.UUID | None:
    value = await session.scalar(
        text("SELECT private.remove_friend_edge(:friend_id)"),
        {"friend_id": friend_id},
    )
    if value is None:
        return None
    return uuid.UUID(str(value))


async def fetch_friend_public(
    session: AsyncSession, friend_id: uuid.UUID
) -> FriendPublicRow | None:
    row = (await session.execute(text(_EDGE_SQL), {"friend_id": friend_id})).first()
    if row is None:
        return None
    return _public_row(row)


async def list_friends_keyset(
    session: AsyncSession,
    *,
    owner_spirit_id: uuid.UUID,
    snapshot_at: datetime,
    fetch_limit: int,
    cursor_time: datetime | None = None,
    cursor_id: uuid.UUID | None = None,
) -> list[FriendPublicRow]:
    rows = (
        await session.execute(
            text(_LIST_SQL),
            {
                "owner_spirit_id": owner_spirit_id,
                "snapshot_at": snapshot_at,
                "fetch_limit": fetch_limit,
                "cursor_time": cursor_time,
                "cursor_id": cursor_id,
            },
        )
    ).all()
    return [_public_row(row) for row in rows]


