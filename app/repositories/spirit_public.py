"""Public spirit profile. Only the name, live public titles, ranks, and help count.

help_count is read from craft_ledgers by v3_ledger_help_count.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

PROFILE_SQL = """
SELECT
    :spirit_id AS spirit_id,
    public.v3_spirit_public_name(:spirit_id) AS name,
    public.v3_ledger_help_count(:spirit_id) AS help_count,
    skill.title AS title,
    skill.rank_name AS rank_name
FROM (SELECT 1) AS anchor
LEFT JOIN public.craft_skills skill
    ON skill.spirit_id = :spirit_id
   AND skill.deleted_at IS NULL
   AND skill.visibility = 'public'
WHERE public.v3_spirit_public_name(:spirit_id) IS NOT NULL
ORDER BY skill.title ASC, skill.id ASC
"""


@dataclass(frozen=True)
class PublicSkillRow:
    title: str
    rank_name: str | None


@dataclass(frozen=True)
class PublicProfileRow:
    spirit_id: UUID
    name: str
    help_count: int
    skills: tuple[PublicSkillRow, ...]


class SqlPublicProfile:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def read(self, spirit_id: UUID) -> PublicProfileRow | None:
        result = await self._session.execute(text(PROFILE_SQL), {"spirit_id": spirit_id})
        rows = result.all()
        if not rows:
            return None
        first = rows[0]
        skills = tuple(
            PublicSkillRow(str(row.title), None if row.rank_name is None else str(row.rank_name))
            for row in rows
            if row.title is not None
        )
        return PublicProfileRow(first.spirit_id, str(first.name), int(first.help_count), skills)
