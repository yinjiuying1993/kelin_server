"""Asker-facing history. Rank names stay at the value copied when the chat opened."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.core.errors import ApiError, public_error_message
from app.domain.cursor import (
    CURSOR_VERSION,
    InvalidCursor,
    MessageCursor,
    ask_filter_hash,
    decode_ask_cursor,
    encode_ask_cursor,
)
from app.repositories.ask_history import HistoryRow
from app.schemas.ask import (
    AskQuestionDetail,
    AskQuestionPage,
    AskQuestionSummary,
    AskRecent,
    AskTurn,
    AskView,
)

ASK_LIST_PAGE_SIZE = 20
ASK_RECENT_LIMIT = 3


class AskHistory(Protocol):
    async def list_history(
        self,
        *,
        asker_user_id: UUID,
        view: str,
        snapshot_at: datetime,
        fetch_limit: int,
        cursor_time: datetime | None,
        cursor_id: UUID | None,
    ) -> list[HistoryRow]: ...

    async def read_history(
        self, asker_user_id: UUID, question_id: UUID
    ) -> tuple[HistoryRow, tuple[tuple[str, str], ...]] | None: ...


async def list_answered_questions(
    history: AskHistory,
    *,
    asker_user_id: UUID,
    view: AskView,
    cursor: str | None,
    secret: bytes,
    now: datetime,
    limit: int = ASK_LIST_PAGE_SIZE,
) -> AskQuestionPage:
    cursor_time, cursor_id, snapshot_at = _cursor(cursor, secret, asker_user_id, view)
    if snapshot_at is None:
        snapshot_at = now
    rows = await history.list_history(
        asker_user_id=asker_user_id,
        view=view,
        snapshot_at=snapshot_at,
        fetch_limit=limit + 1,
        cursor_time=cursor_time,
        cursor_id=cursor_id,
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = encode_ask_cursor(
            MessageCursor(
                version=CURSOR_VERSION,
                sort_time=last.first_reply_at,
                id=last.question_id,
                snapshot_at=snapshot_at,
                filter_hash=ask_filter_hash(asker_user_id, view),
            ),
            secret=secret,
        )
    return AskQuestionPage(
        items=[_summary(row) for row in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def list_recent_questions(
    history: AskHistory,
    *,
    asker_user_id: UUID,
    now: datetime,
) -> AskRecent:
    rows = await history.list_history(
        asker_user_id=asker_user_id,
        view="all",
        snapshot_at=now,
        fetch_limit=ASK_RECENT_LIMIT,
        cursor_time=None,
        cursor_id=None,
    )
    return AskRecent(items=[_summary(row) for row in rows[:ASK_RECENT_LIMIT]])


async def read_answered_question(
    history: AskHistory,
    *,
    asker_user_id: UUID,
    question_id: UUID,
) -> AskQuestionDetail:
    loaded = await history.read_history(asker_user_id, question_id)
    if loaded is None:
        raise _error("NOT_FOUND", 404)
    row, lines = loaded
    turns = [AskTurn(role=role, text=text) for role, text in lines if role in {"user", "spirit"}]
    if not any(turn.role == "spirit" for turn in turns):
        raise _error("NOT_FOUND", 404)
    summary = _summary(row)
    return AskQuestionDetail(
        id=summary.id,
        question=summary.question,
        spirit_name=summary.spirit_name,
        spirit_id=summary.spirit_id,
        rank_name=summary.rank_name,
        skill_title=summary.skill_title,
        rated=summary.rated,
        rating=summary.rating,
        turns=turns,
    )


def _summary(row: HistoryRow) -> AskQuestionSummary:
    rated = row.rating is not None
    return AskQuestionSummary(
        id=row.question_id,
        question=row.question_text,
        spirit_name=row.spirit_name,
        spirit_id=row.spirit_id,
        rank_name=row.rank_name,  # type: ignore[arg-type]
        skill_title=None if row.skill_title in (None, "") else row.skill_title,
        rated=rated,
        rating=row.rating if rated else None,  # type: ignore[arg-type]
    )


def _cursor(
    cursor: str | None, secret: bytes, asker_user_id: UUID, view: str
) -> tuple[datetime | None, UUID | None, datetime | None]:
    if cursor is None:
        return None, None, None
    try:
        decoded = decode_ask_cursor(
            cursor,
            secret=secret,
            expected_filter_hash=ask_filter_hash(asker_user_id, view),
        )
    except InvalidCursor as exc:
        raise _error("INVALID_CURSOR", 422) from exc
    return decoded.sort_time, decoded.id, decoded.snapshot_at


def _error(code: str, status_code: int) -> ApiError:
    return ApiError(code, public_error_message(code), status_code=status_code)
