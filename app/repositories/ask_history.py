"""Asker history. Only dialogues with a usable reply are visible."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_COLUMNS = """
    q.id,
    q.question_text,
    public.v3_spirit_public_name(q.spirit_id) AS spirit_name,
    q.spirit_id,
    q.snapshot_rank_name,
    q.snapshot_title,
    q.first_reply_at,
    rating.rating AS rating
"""

_FROM = """
FROM public.ask_questions q
LEFT JOIN public.ask_ratings rating
  ON rating.ask_question_id = q.id
 AND rating.asker_user_id = q.asker_user_id
"""

LIST_SQL = f"""
SELECT {_COLUMNS}
{_FROM}
WHERE q.asker_user_id = :asker_user_id
  AND q.first_reply_at IS NOT NULL
  AND q.first_reply_at <= :snapshot_at
  AND (
    :has_cursor = false
    OR q.first_reply_at < :cursor_time
    OR (q.first_reply_at = :cursor_time AND q.id < :cursor_id)
  )
  AND (
    :view = 'all'
    OR (:view = 'rated' AND rating.rating IS NOT NULL)
    OR (:view = 'unrated' AND rating.rating IS NULL)
  )
ORDER BY q.first_reply_at DESC, q.id DESC
LIMIT :fetch_limit
"""

READ_SQL = f"""
SELECT {_COLUMNS}
{_FROM}
WHERE q.id = :question_id
  AND q.asker_user_id = :asker_user_id
  AND q.first_reply_at IS NOT NULL
"""

TURNS_SQL = """
SELECT user_text, reply_text
FROM public.ask_turns
WHERE ask_question_id = :question_id
  AND asker_user_id = :asker_user_id
  AND status = 'succeeded'
  AND reply_text IS NOT NULL
ORDER BY created_at
"""


@dataclass(frozen=True)
class HistoryRow:
    question_id: UUID
    question_text: str
    spirit_name: str
    rank_name: str | None
    first_reply_at: datetime
    rating: str | None
    skill_title: str | None = None
    spirit_id: UUID | None = None


class SqlAskHistory:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_history(
        self,
        *,
        asker_user_id: UUID,
        view: str,
        snapshot_at: datetime,
        fetch_limit: int,
        cursor_time: datetime | None,
        cursor_id: UUID | None,
    ) -> list[HistoryRow]:
        result = await self._session.execute(
            text(LIST_SQL),
            {
                "asker_user_id": asker_user_id,
                "view": view,
                "snapshot_at": snapshot_at,
                "fetch_limit": fetch_limit,
                "has_cursor": cursor_time is not None,
                "cursor_time": snapshot_at if cursor_time is None else cursor_time,
                "cursor_id": asker_user_id if cursor_id is None else cursor_id,
            },
        )
        return [_row(item) for item in result]

    async def read_history(
        self, asker_user_id: UUID, question_id: UUID
    ) -> tuple[HistoryRow, tuple[tuple[str, str], ...]] | None:
        result = await self._session.execute(
            text(READ_SQL),
            {"asker_user_id": asker_user_id, "question_id": question_id},
        )
        row = result.one_or_none()
        if row is None:
            return None
        turns = await self._session.execute(
            text(TURNS_SQL),
            {"asker_user_id": asker_user_id, "question_id": question_id},
        )
        lines: list[tuple[str, str]] = []
        for item in turns:
            spoken = str(item.user_text).strip()
            reply = str(item.reply_text).strip()
            if spoken:
                lines.append(("user", spoken))
            if reply:
                lines.append(("spirit", reply))
        return _row(row), tuple(lines)


def _row(row: object) -> HistoryRow:
    return HistoryRow(
        question_id=row.id,  # type: ignore[attr-defined]
        question_text=str(row.question_text),  # type: ignore[attr-defined]
        spirit_name=str(row.spirit_name),  # type: ignore[attr-defined]
        rank_name=row.snapshot_rank_name,  # type: ignore[attr-defined]
        first_reply_at=row.first_reply_at,  # type: ignore[attr-defined]
        rating=None if row.rating is None else str(row.rating),  # type: ignore[attr-defined]
        skill_title=_opened_title(row.snapshot_title),  # type: ignore[attr-defined]
        spirit_id=_spirit_id(row.spirit_id),  # type: ignore[attr-defined]
    )


def _spirit_id(value: object) -> UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _opened_title(value: object) -> str | None:
    if value is None or value == "":
        return None
    return str(value)
