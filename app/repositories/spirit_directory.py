"""Directory lookup through the definer function. No skill, memory, or room columns."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.spirit_directory import DirectoryRow

LOOKUP_SQL = """
SELECT spirit_id, owner_user_id, display_name, avatar_key, available
FROM public.v4_spirit_directory(:spirit_id, :viewer_id)
"""


class SqlSpiritDirectory:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lookup(self, viewer_id: UUID, spirit_id: UUID) -> DirectoryRow | None:
        result = await self._session.execute(
            text(LOOKUP_SQL),
            {"viewer_id": viewer_id, "spirit_id": spirit_id},
        )
        row = result.one_or_none()
        if row is None:
            return None
        return DirectoryRow(
            spirit_id=row.spirit_id,
            owner_user_id=row.owner_user_id,
            display_name=str(row.display_name),
            avatar_key=str(row.avatar_key),
            available=bool(row.available),
        )
