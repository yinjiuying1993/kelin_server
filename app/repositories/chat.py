"""Owner-filtered chat-turn persistence. Spec §§8.2, 8.3, 10.1."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.chat import CHAT_TURN_OPERATION

CHAT_TURN_LOCK_NS = 5389877


@dataclass(frozen=True, slots=True)
class LockedChatSpirit:
    id: uuid.UUID
    onboarding_step: int
    ordinary_dialogue_rounds: int
    version: int
    hatched_at: datetime | None
    onboarding_completed_at: datetime | None
    last_interact_at: datetime


class SpiritLockBusy(Exception):
    """Another chat turn holds the spirit generate lease."""


@dataclass(frozen=True, slots=True)
class TurnIdempotencyClaim:
    inserted: bool
    blocked: bool
    request_hash: str
    status: str
    resource_id: uuid.UUID | None
    locked_until: datetime | None


@dataclass(frozen=True, slots=True)
class MessageRow:
    id: uuid.UUID
    content: str
    source: str
    onboarding: bool
    status: str
    conversation_window_id: uuid.UUID | None
    source_refs: object


@dataclass(frozen=True, slots=True)
class WindowRow:
    id: uuid.UUID
    start_message_id: uuid.UUID
    end_message_id: uuid.UUID
    user_round_count: int
    status: str
    onboarding: bool


@dataclass(frozen=True, slots=True)
class ActivePactRow:
    id: uuid.UUID
    title: str


async def assert_writable_transaction(session: AsyncSession) -> None:
    flag = await session.scalar(text("SHOW transaction_read_only"))
    if str(flag).lower() == "on":
        raise RuntimeError("chat turn cannot write in a read-only transaction")


async def lock_spirit_for_owner(
    session: AsyncSession, owner_id: uuid.UUID, *, nowait: bool = False
) -> LockedChatSpirit | None:
    clause = "FOR UPDATE OF s NOWAIT" if nowait else "FOR UPDATE OF s"
    statement = (
        "SELECT s.id, s.onboarding_step, s.ordinary_dialogue_rounds, s.version, "
        "s.hatched_at, s.onboarding_completed_at, s.last_interact_at "
        "FROM public.spirits s WHERE s.user_id = :user_id "
        f"{clause}"
    )
    try:
        row = (await session.execute(text(statement), {"user_id": owner_id})).first()
    except DBAPIError as exc:
        if nowait and _is_lock_not_available(exc):
            raise SpiritLockBusy from exc
        raise
    if row is None:
        return None
    return LockedChatSpirit(
        id=row.id,
        onboarding_step=int(row.onboarding_step),
        ordinary_dialogue_rounds=int(row.ordinary_dialogue_rounds),
        version=int(row.version),
        hatched_at=row.hatched_at,
        onboarding_completed_at=row.onboarding_completed_at,
        last_interact_at=row.last_interact_at,
    )


async def fetch_user_message(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    client_id: uuid.UUID,
) -> MessageRow | None:
    row = (
        await session.execute(
            text(
                "SELECT id, content, source, onboarding, status, conversation_window_id, "
                "source_refs "
                "FROM public.messages "
                "WHERE spirit_id = :spirit_id AND client_id = :client_id AND role = 'user'"
            ),
            {"spirit_id": spirit_id, "client_id": client_id},
        )
    ).first()
    if row is None:
        return None
    return _message_row(row)


async def fetch_spirit_reply(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    user_message_id: uuid.UUID,
) -> MessageRow | None:
    row = (
        await session.execute(
            text(
                "SELECT id, content, source, onboarding, status, conversation_window_id, "
                "source_refs "
                "FROM public.messages "
                "WHERE spirit_id = :spirit_id AND reply_to_message_id = :user_message_id "
                "AND role = 'spirit'"
            ),
            {"spirit_id": spirit_id, "user_message_id": user_message_id},
        )
    ).first()
    if row is None:
        return None
    return _message_row(row)


async def insert_user_message(
    session: AsyncSession,
    *,
    message_id: uuid.UUID,
    spirit_id: uuid.UUID,
    client_id: uuid.UUID,
    content: str,
    source: str,
    onboarding: bool,
) -> None:
    await session.execute(
        text(
            "INSERT INTO public.messages ("
            "id, spirit_id, client_id, role, content, source, status, onboarding"
            ") VALUES ("
            ":id, :spirit_id, :client_id, 'user', :content, :source, 'accepted', :onboarding"
            ")"
        ),
        {
            "id": message_id,
            "spirit_id": spirit_id,
            "client_id": client_id,
            "content": content,
            "source": source,
            "onboarding": onboarding,
        },
    )


async def insert_spirit_message(
    session: AsyncSession,
    *,
    message_id: uuid.UUID,
    spirit_id: uuid.UUID,
    user_message_id: uuid.UUID,
    content: str,
    source: str,
    onboarding: bool,
    source_refs: list[dict[str, object]],
) -> None:
    await session.execute(
        text(
            "INSERT INTO public.messages ("
            "id, spirit_id, client_id, role, content, source, status, onboarding, "
            "reply_to_message_id, source_refs"
            ") VALUES ("
            ":id, :spirit_id, NULL, 'spirit', :content, :source, 'generated', :onboarding, "
            ":reply_to, CAST(:source_refs AS jsonb)"
            ")"
        ),
        {
            "id": message_id,
            "spirit_id": spirit_id,
            "content": content,
            "source": source,
            "onboarding": onboarding,
            "reply_to": user_message_id,
            "source_refs": json.dumps(source_refs),
        },
    )


async def insert_window(
    session: AsyncSession,
    *,
    window_id: uuid.UUID,
    spirit_id: uuid.UUID,
    start_message_id: uuid.UUID,
    end_message_id: uuid.UUID,
    onboarding: bool,
    user_round_count: int,
    status: str,
) -> None:
    await session.execute(
        text(
            "INSERT INTO public.conversation_windows ("
            "id, spirit_id, start_message_id, end_message_id, user_round_count, "
            "onboarding, status"
            ") VALUES ("
            ":id, :spirit_id, :start_id, :end_id, :round_count, :onboarding, :status"
            ")"
        ),
        {
            "id": window_id,
            "spirit_id": spirit_id,
            "start_id": start_message_id,
            "end_id": end_message_id,
            "round_count": user_round_count,
            "onboarding": onboarding,
            "status": status,
        },
    )


async def fetch_window(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    window_id: uuid.UUID,
) -> WindowRow | None:
    row = (
        await session.execute(
            text(
                "SELECT id, start_message_id, end_message_id, user_round_count, "
                "status, onboarding "
                "FROM public.conversation_windows "
                "WHERE spirit_id = :spirit_id AND id = :window_id"
            ),
            {"spirit_id": spirit_id, "window_id": window_id},
        )
    ).first()
    if row is None:
        return None
    return _window_row(row)


async def fetch_open_ordinary_window(
    session: AsyncSession, *, spirit_id: uuid.UUID
) -> WindowRow | None:
    row = (
        await session.execute(
            text(
                "SELECT id, start_message_id, end_message_id, user_round_count, "
                "status, onboarding "
                "FROM public.conversation_windows "
                "WHERE spirit_id = :spirit_id AND onboarding = false AND status = 'open' "
                "ORDER BY created_at ASC, id ASC "
                "LIMIT 1 "
                "FOR UPDATE"
            ),
            {"spirit_id": spirit_id},
        )
    ).first()
    if row is None:
        return None
    return _window_row(row)


async def extend_open_ordinary_window(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    window_id: uuid.UUID,
    end_message_id: uuid.UUID,
    user_round_count: int,
    status: str,
) -> bool:
    row = (
        await session.execute(
            text(
                "UPDATE public.conversation_windows "
                "SET end_message_id = :end_id, user_round_count = :round_count, "
                "status = :status "
                "WHERE id = :id AND spirit_id = :spirit_id "
                "AND onboarding = false AND status = 'open' "
                "RETURNING id"
            ),
            {
                "id": window_id,
                "spirit_id": spirit_id,
                "end_id": end_message_id,
                "round_count": user_round_count,
                "status": status,
            },
        )
    ).first()
    return row is not None


async def attach_window(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    window_id: uuid.UUID,
    user_message_id: uuid.UUID,
    spirit_message_id: uuid.UUID,
) -> None:
    await session.execute(
        text(
            "UPDATE public.messages SET conversation_window_id = :window_id "
            "WHERE spirit_id = :spirit_id "
            "AND (id = :user_message_id OR id = :spirit_message_id)"
        ),
        {
            "window_id": window_id,
            "spirit_id": spirit_id,
            "user_message_id": user_message_id,
            "spirit_message_id": spirit_message_id,
        },
    )


async def apply_complete_turn(
    session: AsyncSession,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
    *,
    onboarding_step: int,
    ordinary_dialogue_rounds: int,
    now: datetime,
) -> tuple[int, datetime]:
    row = (
        await session.execute(
            text(
                "UPDATE public.spirits SET "
                "onboarding_step = :onboarding_step, "
                "ordinary_dialogue_rounds = :ordinary_rounds, "
                "last_interact_at = GREATEST(last_interact_at, :now), "
                "version = version + 1 "
                "WHERE id = :id AND user_id = :user_id "
                "RETURNING version, last_interact_at"
            ),
            {
                "onboarding_step": onboarding_step,
                "ordinary_rounds": ordinary_dialogue_rounds,
                "now": now,
                "id": spirit_id,
                "user_id": owner_id,
            },
        )
    ).first()
    if row is None:
        raise RuntimeError("chat turn update matched no owner row")
    return int(row.version), row.last_interact_at


async def insert_chat_completed_event(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    user_message_id: uuid.UUID,
    occurred_at: datetime,
) -> None:
    await session.execute(
        text(
            "INSERT INTO public.growth_events ("
            "spirit_id, source_type, source_id, event_type, occurred_at"
            ") VALUES ("
            ":spirit_id, 'chat_turn', :source_id, 'chat_completed', :occurred_at"
            ") ON CONFLICT (source_type, source_id, event_type) DO NOTHING"
        ),
        {
            "spirit_id": spirit_id,
            "source_id": user_message_id,
            "occurred_at": occurred_at,
        },
    )


async def claim_turn_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    request_hash: str,
    *,
    locked_until: datetime,
) -> TurnIdempotencyClaim:
    token = f"{owner_id}:{CHAT_TURN_OPERATION}:{client_id}"
    acquired = await session.scalar(
        text("SELECT pg_try_advisory_xact_lock(:ns, hashtext(:token))"),
        {"ns": CHAT_TURN_LOCK_NS, "token": token},
    )
    if not bool(acquired):
        return TurnIdempotencyClaim(
            inserted=False,
            blocked=True,
            request_hash=request_hash,
            status="in_progress",
            resource_id=None,
            locked_until=None,
        )
    params = {
        "user_id": owner_id,
        "operation": CHAT_TURN_OPERATION,
        "client_id": client_id,
        "request_hash": request_hash,
        "locked_until": locked_until,
    }
    inserted = (
        await session.execute(
            text(
                "INSERT INTO public.idempotency_records ("
                "user_id, operation, client_id, request_hash, status, locked_until"
                ") VALUES ("
                ":user_id, :operation, :client_id, :request_hash, 'in_progress', :locked_until"
                ") ON CONFLICT (user_id, operation, client_id) DO NOTHING "
                "RETURNING request_hash, status, resource_id, locked_until"
            ),
            params,
        )
    ).first()
    if inserted is not None:
        return _turn_claim(inserted, inserted=True, blocked=False)
    existing = (
        await session.execute(
            text(
                "SELECT request_hash, status, resource_id, locked_until "
                "FROM public.idempotency_records "
                "WHERE user_id = :user_id AND operation = :operation "
                "AND client_id = :client_id "
                "FOR UPDATE"
            ),
            params,
        )
    ).first()
    if existing is None:
        raise RuntimeError("chat turn idempotency claim failed")
    return _turn_claim(existing, inserted=False, blocked=False)


async def refresh_turn_lease(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    *,
    locked_until: datetime,
) -> None:
    await session.execute(
        text(
            "UPDATE public.idempotency_records SET locked_until = :locked_until "
            "WHERE user_id = :user_id AND operation = :operation "
            "AND client_id = :client_id"
        ),
        {
            "user_id": owner_id,
            "operation": CHAT_TURN_OPERATION,
            "client_id": client_id,
            "locked_until": locked_until,
        },
    )


async def complete_turn_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    user_message_id: uuid.UUID,
) -> None:
    await session.execute(
        text(
            "UPDATE public.idempotency_records "
            "SET status = 'completed', resource_type = 'chat_turn', "
            "resource_id = :resource_id, completed_at = now(), locked_until = NULL "
            "WHERE user_id = :user_id AND operation = :operation "
            "AND client_id = :client_id"
        ),
        {
            "user_id": owner_id,
            "operation": CHAT_TURN_OPERATION,
            "client_id": client_id,
            "resource_id": user_message_id,
        },
    )


def _turn_claim(row: Any, *, inserted: bool, blocked: bool) -> TurnIdempotencyClaim:
    return TurnIdempotencyClaim(
        inserted=inserted,
        blocked=blocked,
        request_hash=str(row.request_hash),
        status=str(row.status),
        resource_id=row.resource_id,
        locked_until=row.locked_until,
    )


def _is_lock_not_available(exc: DBAPIError) -> bool:
    orig = getattr(exc, "orig", None)
    return getattr(orig, "sqlstate", None) == "55P03"


def _message_row(row: Any) -> MessageRow:
    return MessageRow(
        id=row.id,
        content=str(row.content),
        source=str(row.source),
        onboarding=bool(row.onboarding),
        status=str(row.status),
        conversation_window_id=row.conversation_window_id,
        source_refs=row.source_refs,
    )


async def fetch_remote_search_on(session: AsyncSession, owner_id: uuid.UUID) -> bool:
    value = await session.scalar(
        text("SELECT remote_search_on FROM public.user_preferences WHERE user_id = :id"),
        {"id": owner_id},
    )
    if value is None:
        return True
    return bool(value)


async def fetch_active_owned_pact(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
) -> ActivePactRow | None:
    row = (
        await session.execute(
            text(
                "SELECT p.id, p.title FROM public.pacts p "
                "JOIN public.spirits s ON s.id = p.spirit_id "
                "WHERE p.spirit_id = :spirit_id AND s.user_id = :owner_id "
                "AND p.status = 'active'"
            ),
            {"spirit_id": spirit_id, "owner_id": owner_id},
        )
    ).first()
    if row is None:
        return None
    return ActivePactRow(id=row.id, title=str(row.title))


def _window_row(row: Any) -> WindowRow:
    return WindowRow(
        id=row.id,
        start_message_id=row.start_message_id,
        end_message_id=row.end_message_id,
        user_round_count=int(row.user_round_count),
        status=str(row.status),
        onboarding=bool(row.onboarding),
    )
