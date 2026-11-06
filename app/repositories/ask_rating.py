"""One rating per dialogue, plus the settlement job. Points are not applied here."""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.craft_ledger.settle_outbox import CraftRatingSettleJob, outbox_insert_columns

QUESTION_SQL = """
SELECT spirit_id, spirit_owner_user_id, snapshot_skill_slug, first_reply_at
FROM public.ask_questions
WHERE id = :question_id
  AND asker_user_id = :asker_user_id
"""

FIND_SQL = """
SELECT rating, points
FROM public.ask_ratings
WHERE ask_question_id = :question_id
  AND asker_user_id = :asker_user_id
"""

INSERT_SQL = """
INSERT INTO public.ask_ratings (
    ask_question_id, asker_user_id, rating, points
) VALUES (
    :question_id, :asker_user_id, :rating, :points
)
ON CONFLICT (ask_question_id) DO NOTHING
RETURNING rating
"""

_OUTBOX_SQL = """
INSERT INTO public.outbox_events (
    aggregate_type, aggregate_id, event_type, dedupe_key, owner_id, payload
) VALUES (
    :aggregate_type, :aggregate_id, :event_type, :dedupe_key, :owner_id,
    CAST(:payload AS jsonb)
)
"""


@dataclass(frozen=True)
class RateTarget:
    spirit_id: UUID
    spirit_owner_user_id: UUID
    skill_slug: str
    answered: bool


@dataclass(frozen=True)
class StoredRating:
    rating: str
    points: int


class SqlAskRating:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def target(self, asker_user_id: UUID, question_id: UUID) -> RateTarget | None:
        result = await self._session.execute(
            text(QUESTION_SQL),
            {"asker_user_id": asker_user_id, "question_id": question_id},
        )
        row = result.one_or_none()
        if row is None:
            return None
        return RateTarget(
            spirit_id=row.spirit_id,
            spirit_owner_user_id=row.spirit_owner_user_id,
            skill_slug=str(row.snapshot_skill_slug),
            answered=row.first_reply_at is not None,
        )

    async def find(self, asker_user_id: UUID, question_id: UUID) -> StoredRating | None:
        result = await self._session.execute(
            text(FIND_SQL),
            {"asker_user_id": asker_user_id, "question_id": question_id},
        )
        row = result.one_or_none()
        if row is None:
            return None
        return StoredRating(str(row.rating), int(row.points))

    async def insert(
        self,
        *,
        asker_user_id: UUID,
        question_id: UUID,
        rating: str,
        points: int,
    ) -> bool:
        result = await self._session.execute(
            text(INSERT_SQL),
            {
                "asker_user_id": asker_user_id,
                "question_id": question_id,
                "rating": rating,
                "points": points,
            },
        )
        return result.scalar_one_or_none() is not None

    async def enqueue(self, job: CraftRatingSettleJob) -> None:
        columns = job.insert_columns()
        if tuple(columns) != outbox_insert_columns():
            raise RuntimeError("rating outbox shape drifted")
        await self._session.execute(
            text(_OUTBOX_SQL),
            {**columns, "payload": json.dumps(columns["payload"])},
        )
