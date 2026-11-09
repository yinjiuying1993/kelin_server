"""One later ask turn. The reply uses the stored snapshot and does not rematch."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.ask.official import OFFICIAL_SLUG
from app.domain.ask.prompt import DialogueRole

LOAD_SQL = """
SELECT
    q.question_text,
    q.snapshot_detail,
    q.snapshot_reference_links,
    q.snapshot_skill_slug,
    q.agent_session_id,
    q.first_reply_at,
    EXISTS (
        SELECT 1
        FROM public.ask_ratings rated
        WHERE rated.ask_question_id = q.id
          AND rated.asker_user_id = q.asker_user_id
    ) AS rated
FROM public.ask_questions q
WHERE q.id = :question_id
  AND q.asker_user_id = :asker_user_id
"""

PRIOR_SQL = """
SELECT user_text, reply_text
FROM public.ask_turns
WHERE ask_question_id = :question_id
  AND asker_user_id = :asker_user_id
  AND status = 'succeeded'
  AND reply_text IS NOT NULL
ORDER BY created_at
"""


@dataclass(frozen=True)
class FollowDialogue:
    question_id: UUID
    question_text: str
    snapshot_detail: str
    snapshot_reference_links: tuple[str, ...]
    first_reply_at: datetime | None
    rated: bool
    turns: tuple[tuple[DialogueRole, str], ...]
    official: bool = False
    agent_session_id: str | None = None


class SqlAskFollow:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def load(self, asker_user_id: UUID, question_id: UUID) -> FollowDialogue | None:
        result = await self._session.execute(
            text(LOAD_SQL),
            {"asker_user_id": asker_user_id, "question_id": question_id},
        )
        row = result.one_or_none()
        if row is None:
            return None
        prior = await self._session.execute(
            text(PRIOR_SQL),
            {"asker_user_id": asker_user_id, "question_id": question_id},
        )
        turns: list[tuple[DialogueRole, str]] = []
        for item in prior:
            spoken = str(item.user_text).strip()
            reply = str(item.reply_text).strip()
            if spoken:
                turns.append(("user", spoken))
            if reply:
                turns.append(("spirit", reply))
        return FollowDialogue(
            question_id=question_id,
            question_text=str(row.question_text),
            snapshot_detail=str(row.snapshot_detail),
            snapshot_reference_links=tuple(row.snapshot_reference_links or ()),
            first_reply_at=row.first_reply_at,
            rated=bool(row.rated),
            turns=tuple(turns),
            official=str(row.snapshot_skill_slug) == OFFICIAL_SLUG,
            agent_session_id=_session_id(row.agent_session_id),
        )


def _session_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None
