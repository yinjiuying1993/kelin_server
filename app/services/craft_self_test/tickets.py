"""Issue a short-lived self-test ticket and drop expired ones.

Turns are not stored. Ask dialogues and help counts are not written.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ApiError, public_error_message
from app.db.session import claimed_transaction
from app.domain.craft_self_test import (
    SELF_TEST_TICKET_TTL,
    IssuedTicket,
    StoredTicket,
    ticket_is_expired,
)
from app.repositories.self_test import SelfTestStore, SqlSelfTestStore


async def start_self_test(
    store: SelfTestStore,
    *,
    owner_id: UUID,
    craft_id: UUID,
    now: datetime,
) -> IssuedTicket:
    _aware(now)
    spirit_id = await store.owner_spirit(owner_id)
    if spirit_id is None:
        raise _missing()
    skill = await store.generated_skill(craft_id, spirit_id)
    if skill is None:
        raise _missing()
    expires_at = now + SELF_TEST_TICKET_TTL
    ticket_id = await store.insert_ticket(
        spirit_id=spirit_id,
        craft_id=craft_id,
        skill_slug=skill.skill_slug,
        snapshot_body=skill.body,
        expires_at=expires_at,
    )
    return IssuedTicket(ticket_id=ticket_id, expires_at=expires_at)


async def require_self_test_ticket(
    store: SelfTestStore,
    *,
    owner_id: UUID,
    ticket_id: UUID,
    craft_id: UUID,
    now: datetime,
) -> StoredTicket:
    _aware(now)
    spirit_id = await store.owner_spirit(owner_id)
    ticket = None if spirit_id is None else await store.get_ticket(ticket_id)
    if (
        spirit_id is None
        or ticket is None
        or ticket.spirit_id != spirit_id
        or ticket.craft_id != craft_id
        or ticket_is_expired(ticket.expires_at, now)
    ):
        raise _missing()
    return ticket


async def require_owned_self_test_ticket(
    store: SelfTestStore,
    *,
    owner_id: UUID,
    ticket_id: UUID,
    now: datetime,
) -> StoredTicket:
    _aware(now)
    spirit_id = await store.owner_spirit(owner_id)
    ticket = None if spirit_id is None else await store.get_ticket(ticket_id)
    if spirit_id is None or ticket is None or ticket.spirit_id != spirit_id or ticket_is_expired(
        ticket.expires_at, now
    ):
        raise _missing()
    return ticket


async def purge_expired_self_test_tickets(store: SelfTestStore, *, now: datetime) -> int:
    _aware(now)
    return await store.delete_expired(now)


async def run_self_test_ticket_cleanup(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
) -> int:
    async with claimed_transaction(session_factory, None, db_role="kelin_worker") as session:
        return await purge_expired_self_test_tickets(SqlSelfTestStore(session), now=now)


def _aware(now: datetime) -> None:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")


def _missing() -> ApiError:
    return ApiError("NOT_FOUND", public_error_message("NOT_FOUND"), status_code=404)
