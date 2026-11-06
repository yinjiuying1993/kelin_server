"""Self-test ticket rows. Ask dialogues and ledgers are not written here."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.craft_self_test import GeneratedSkill, StoredTicket

SKILL_SQL = """
SELECT skill_slug, body
FROM public.craft_skills
WHERE id = :craft_id
  AND spirit_id = :spirit_id
  AND deleted_at IS NULL
  AND char_length(body) >= 1
"""

INSERT_SQL = """
INSERT INTO public.craft_self_test_tickets (
    spirit_id,
    craft_skill_id,
    skill_slug,
    snapshot_body,
    expires_at
) VALUES (
    :spirit_id,
    :craft_id,
    :skill_slug,
    :snapshot_body,
    :expires_at
)
RETURNING id
"""

GET_SQL = """
SELECT id, spirit_id, craft_skill_id, skill_slug, snapshot_body, expires_at
FROM public.craft_self_test_tickets
WHERE id = :ticket_id
"""

DELETE_EXPIRED_SQL = """
DELETE FROM public.craft_self_test_tickets
WHERE expires_at <= :now
"""

SPIRIT_SQL = "SELECT id FROM public.spirits WHERE user_id = :user_id"


class SelfTestStore(Protocol):
    async def owner_spirit(self, owner_id: UUID) -> UUID | None: ...

    async def generated_skill(self, craft_id: UUID, spirit_id: UUID) -> GeneratedSkill | None: ...

    async def insert_ticket(
        self,
        *,
        spirit_id: UUID,
        craft_id: UUID,
        skill_slug: str,
        snapshot_body: str,
        expires_at: datetime,
    ) -> UUID: ...

    async def get_ticket(self, ticket_id: UUID) -> StoredTicket | None: ...

    async def delete_expired(self, now: datetime) -> int: ...


class SqlSelfTestStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def owner_spirit(self, owner_id: UUID) -> UUID | None:
        result = await self._session.execute(text(SPIRIT_SQL), {"user_id": owner_id})
        return result.scalar_one_or_none()

    async def generated_skill(self, craft_id: UUID, spirit_id: UUID) -> GeneratedSkill | None:
        result = await self._session.execute(
            text(SKILL_SQL), {"craft_id": craft_id, "spirit_id": spirit_id}
        )
        row = result.one_or_none()
        if row is None:
            return None
        return GeneratedSkill(skill_slug=str(row.skill_slug), body=str(row.body))

    async def insert_ticket(
        self,
        *,
        spirit_id: UUID,
        craft_id: UUID,
        skill_slug: str,
        snapshot_body: str,
        expires_at: datetime,
    ) -> UUID:
        result = await self._session.execute(
            text(INSERT_SQL),
            {
                "spirit_id": spirit_id,
                "craft_id": craft_id,
                "skill_slug": skill_slug,
                "snapshot_body": snapshot_body,
                "expires_at": expires_at,
            },
        )
        return result.scalar_one()

    async def get_ticket(self, ticket_id: UUID) -> StoredTicket | None:
        result = await self._session.execute(text(GET_SQL), {"ticket_id": ticket_id})
        row = result.one_or_none()
        if row is None:
            return None
        return StoredTicket(
            ticket_id=row.id,
            spirit_id=row.spirit_id,
            craft_id=row.craft_skill_id,
            skill_slug=str(row.skill_slug),
            snapshot_body=str(row.snapshot_body),
            expires_at=row.expires_at,
        )

    async def delete_expired(self, now: datetime) -> int:
        result = await self._session.execute(text(DELETE_EXPIRED_SQL), {"now": now})
        return int(result.rowcount or 0)
