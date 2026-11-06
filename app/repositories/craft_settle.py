"""Worker settlement writes. The orchestrator calls these, it does not own the SQL."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.craft import rank_for_points

CREDIT_SQL = """
INSERT INTO public.craft_ledgers (spirit_id, total_points, help_count)
VALUES (:spirit_id, :points, 1)
ON CONFLICT (spirit_id) DO UPDATE
SET help_count = craft_ledgers.help_count + 1,
    total_points = craft_ledgers.total_points + :points,
    updated_at = now()
"""

SKILL_SQL = """
SELECT points, deleted_at
FROM public.craft_skills
WHERE skill_slug = :skill_slug
  AND spirit_id = :spirit_id
"""

APPLY_SQL = """
UPDATE public.craft_skills
SET points = :points, rank_name = :rank_name
WHERE skill_slug = :skill_slug
  AND spirit_id = :spirit_id
  AND deleted_at IS NULL
RETURNING id
"""

FIND_SQL = """
SELECT points_added, skill_points_applied
FROM public.craft_settlements
WHERE ask_question_id = :question_id
"""

INSERT_SQL = """
INSERT INTO public.craft_settlements (
    ask_question_id, spirit_id, points_added, help_counted, skill_points_applied
) VALUES (
    :question_id, :spirit_id, :points_added, true, :skill_points_applied
)
ON CONFLICT (ask_question_id) DO NOTHING
RETURNING id
"""


@dataclass(frozen=True)
class RecordedSettlement:
    points_added: int
    skill_points_applied: bool


class SqlSettlementBook:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def credit(self, spirit_id: UUID, points: int) -> None:
        await self._session.execute(
            text(CREDIT_SQL),
            {"spirit_id": spirit_id, "points": points},
        )

    async def apply_skill(self, spirit_id: UUID, skill_slug: str, points: int) -> bool:
        found = await self._session.execute(
            text(SKILL_SQL),
            {"spirit_id": spirit_id, "skill_slug": skill_slug},
        )
        row = found.one_or_none()
        if row is None or row.deleted_at is not None:
            return False
        updated = rank_for_points(int(row.points) + points)
        result = await self._session.execute(
            text(APPLY_SQL),
            {
                "spirit_id": spirit_id,
                "skill_slug": skill_slug,
                "points": int(row.points) + points,
                "rank_name": updated.name,
            },
        )
        return result.scalar_one_or_none() is not None

    async def find(self, question_id: UUID) -> RecordedSettlement | None:
        result = await self._session.execute(text(FIND_SQL), {"question_id": question_id})
        row = result.one_or_none()
        if row is None:
            return None
        return RecordedSettlement(int(row.points_added), bool(row.skill_points_applied))

    async def insert(
        self,
        *,
        question_id: UUID,
        spirit_id: UUID,
        points_added: int,
        skill_points_applied: bool,
    ) -> bool:
        result = await self._session.execute(
            text(INSERT_SQL),
            {
                "question_id": question_id,
                "spirit_id": spirit_id,
                "points_added": points_added,
                "skill_points_applied": skill_points_applied,
            },
        )
        return result.scalar_one_or_none() is not None
