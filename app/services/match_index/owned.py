"""One committed index transaction per craft call. Skill rows are not updated here."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import CurrentUser
from app.db.session import claimed_transaction
from app.repositories.match_index import SqlMatchIndexStore
from app.services.match_index.index import MatchIndexService


class OwnedMatchIndex:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        user: CurrentUser,
    ) -> None:
        self._session_factory = session_factory
        self._user = user

    async def stage(
        self,
        *,
        craft_id: UUID,
        spirit_id: UUID,
        skill_slug: str,
        title: str,
        keywords: tuple[str, ...],
    ) -> None:
        async with _open_service(self._session_factory, self._user) as service:
            await service.stage(
                craft_id=craft_id,
                spirit_id=spirit_id,
                skill_slug=skill_slug,
                title=title,
                keywords=keywords,
            )

    async def activate(self, *, craft_id: UUID) -> None:
        async with _open_service(self._session_factory, self._user) as service:
            await service.activate(craft_id=craft_id)

    async def withdraw(self, *, craft_id: UUID) -> None:
        async with _open_service(self._session_factory, self._user) as service:
            await service.withdraw(craft_id=craft_id)

    async def remove_active(self, *, craft_id: UUID) -> None:
        async with _open_service(self._session_factory, self._user) as service:
            await service.remove_active(craft_id=craft_id)


@asynccontextmanager
async def _open_service(
    session_factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
) -> AsyncIterator[MatchIndexService]:
    async with claimed_transaction(session_factory, user) as session:
        yield MatchIndexService(SqlMatchIndexStore(session))
