"""Owner-filtered message history. Spec §§10.2, 15.1. Keyset only."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_PAGE_SELECT = (
    "SELECT m.id, m.client_id, m.role, m.content, m.source, m.status, "
    "m.reply_to_message_id, m.source_refs, m.created_at "
    "FROM public.messages m "
    "WHERE m.spirit_id = :spirit_id AND m.role <> 'system' "
    "AND m.created_at <= :snapshot_at"
)
_ORDER_LIMIT = " ORDER BY m.created_at DESC, m.id DESC LIMIT :fetch_limit"
_KEYSET = (
    " AND (m.created_at < :cursor_time OR (m.created_at = :cursor_time AND m.id < :cursor_id))"
)


@dataclass(frozen=True, slots=True)
class MessageListRow:
    id: uuid.UUID
    client_id: uuid.UUID | None
    role: str
    content: str
    source: str
    status: str
    reply_to_message_id: uuid.UUID | None
    source_refs: Any
    created_at: datetime


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


async def list_messages_keyset(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    snapshot_at: datetime,
    fetch_limit: int,
    cursor_time: datetime | None = None,
    cursor_id: uuid.UUID | None = None,
) -> list[MessageListRow]:
    params: dict[str, Any] = {
        "spirit_id": spirit_id,
        "snapshot_at": snapshot_at,
        "fetch_limit": fetch_limit,
    }
    sql = _PAGE_SELECT
    if cursor_time is not None and cursor_id is not None:
        sql += _KEYSET
        params["cursor_time"] = cursor_time
        params["cursor_id"] = cursor_id
    sql += _ORDER_LIMIT
    rows = (await session.execute(text(sql), params)).all()
    return [_list_row(row) for row in rows]


def _list_row(row: Any) -> MessageListRow:
    created_at = row.created_at
    if not isinstance(created_at, datetime):
        raise RuntimeError("message created_at is not a timestamp")
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    else:
        created_at = created_at.astimezone(UTC)
    return MessageListRow(
        id=row.id,
        client_id=row.client_id,
        role=str(row.role),
        content=str(row.content),
        source=str(row.source),
        status=str(row.status),
        reply_to_message_id=row.reply_to_message_id,
        source_refs=row.source_refs,
        created_at=created_at,
    )
