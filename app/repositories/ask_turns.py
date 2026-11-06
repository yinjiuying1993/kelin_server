"""First-turn rows. History is stamped only when a usable reply is stored."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

FIND_SQL = """
SELECT reply_text, status
FROM public.ask_turns
WHERE ask_question_id = :question_id
  AND asker_user_id = :asker_user_id
  AND client_id = :client_id
"""

FAIL_SQL = """
INSERT INTO public.ask_turns (
    ask_question_id, asker_user_id, client_id, user_text, status
) VALUES (
    :question_id, :asker_user_id, :client_id, :user_text, 'failed'
)
ON CONFLICT (ask_question_id, client_id)
DO UPDATE SET status = 'failed', reply_text = NULL
WHERE ask_turns.status <> 'succeeded'
"""

SUCCEED_SQL = """
INSERT INTO public.ask_turns (
    ask_question_id, asker_user_id, client_id, user_text, reply_text, status
) VALUES (
    :question_id, :asker_user_id, :client_id, :user_text, :reply_text, 'succeeded'
)
ON CONFLICT (ask_question_id, client_id)
DO UPDATE SET reply_text = EXCLUDED.reply_text, status = 'succeeded'
WHERE ask_turns.status <> 'succeeded'
"""

HISTORY_SQL = """
UPDATE public.ask_questions
SET first_reply_at = COALESCE(first_reply_at, :answered_at)
WHERE id = :question_id
  AND asker_user_id = :asker_user_id
RETURNING first_reply_at
"""

OCCUPY_SQL = """
INSERT INTO public.ask_turns (
    ask_question_id, asker_user_id, client_id, user_text, status
) VALUES (
    :question_id, :asker_user_id, :client_id, :user_text, 'pending'
)
ON CONFLICT (ask_question_id, client_id) DO NOTHING
RETURNING status
"""

CLAIM_SQL = """
UPDATE public.ask_turns
SET status = 'pending', reply_text = NULL
WHERE ask_question_id = :question_id
  AND asker_user_id = :asker_user_id
  AND client_id = :client_id
  AND status = 'failed'
RETURNING user_text
"""


@dataclass(frozen=True)
class StoredTurn:
    reply_text: str | None
    status: str
    user_text: str = ""


@dataclass(frozen=True)
class TurnClaim:
    kind: str
    reply_text: str | None = None
    user_text: str = ""


class SqlAskTurns:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find(
        self, question_id: UUID, asker_user_id: UUID, client_id: UUID
    ) -> StoredTurn | None:
        result = await self._session.execute(
            text(FIND_SQL),
            {
                "question_id": question_id,
                "asker_user_id": asker_user_id,
                "client_id": client_id,
            },
        )
        row = result.one_or_none()
        if row is None:
            return None
        reply = row.reply_text
        return StoredTurn(None if reply is None else str(reply), str(row.status))

    async def occupy(
        self,
        *,
        question_id: UUID,
        asker_user_id: UUID,
        client_id: UUID,
        user_text: str,
    ) -> TurnClaim:
        inserted = await self._session.execute(
            text(OCCUPY_SQL),
            {
                "question_id": question_id,
                "asker_user_id": asker_user_id,
                "client_id": client_id,
                "user_text": user_text,
            },
        )
        if inserted.scalar_one_or_none() is not None:
            return TurnClaim("owned", user_text=user_text)
        current = await self.find(question_id, asker_user_id, client_id)
        if current is not None and current.status == "succeeded" and current.reply_text:
            return TurnClaim("succeeded", reply_text=current.reply_text)
        if current is not None and current.status == "failed":
            claimed = await self._session.execute(
                text(CLAIM_SQL),
                {
                    "question_id": question_id,
                    "asker_user_id": asker_user_id,
                    "client_id": client_id,
                },
            )
            stored = claimed.scalar_one_or_none()
            if stored is not None:
                return TurnClaim("owned", user_text=str(stored))
        return TurnClaim("busy")

    async def fail(
        self,
        *,
        question_id: UUID,
        asker_user_id: UUID,
        client_id: UUID,
        user_text: str,
    ) -> None:
        await self._session.execute(
            text(FAIL_SQL),
            {
                "question_id": question_id,
                "asker_user_id": asker_user_id,
                "client_id": client_id,
                "user_text": user_text,
            },
        )

    async def succeed(
        self,
        *,
        question_id: UUID,
        asker_user_id: UUID,
        client_id: UUID,
        user_text: str,
        reply_text: str,
        answered_at: datetime,
    ) -> datetime:
        await self._session.execute(
            text(SUCCEED_SQL),
            {
                "question_id": question_id,
                "asker_user_id": asker_user_id,
                "client_id": client_id,
                "user_text": user_text,
                "reply_text": reply_text,
            },
        )
        return await self.mark_history(question_id, asker_user_id, answered_at)

    async def mark_history(
        self, question_id: UUID, asker_user_id: UUID, answered_at: datetime
    ) -> datetime:
        result = await self._session.execute(
            text(HISTORY_SQL),
            {
                "question_id": question_id,
                "asker_user_id": asker_user_id,
                "answered_at": answered_at,
            },
        )
        return result.scalar_one()
