"""Question materials. Active title, keywords, and skill id, without the asker's spirit.

The ask flow decides whether this projection fits one model call. This module does not.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.match_index import MatchIndexError
from app.domain.match_index.rows import QuestionMaterial
from app.repositories.spirit import fetch_spirit_for_owner
from app.services.match_index.index import MatchIndexService


class AskerSpirits(Protocol):
    async def spirit_id(self, user_id: UUID) -> UUID | None: ...


class SessionAskerSpirits:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def spirit_id(self, user_id: UUID) -> UUID | None:
        owned = await fetch_spirit_for_owner(self._session, user_id)
        if owned is None:
            return None
        return owned.id


async def read_asker_materials(
    index: MatchIndexService,
    askers: AskerSpirits,
    *,
    asker_user_id: UUID,
    asker_spirit_id: UUID | None,
) -> list[QuestionMaterial]:
    owned = await askers.spirit_id(asker_user_id)
    if owned != asker_spirit_id:
        raise MatchIndexError
    return await index.materials_for_asker(asker_spirit_id=owned)
