"""V3-S7-T02 history is ordered by the first usable reply and keeps that rank."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.core.errors import ApiError
from app.domain.cursor import MessageCursor, ask_filter_hash, encode_ask_cursor
from app.repositories import ask_history as history_sql
from app.repositories.ask_history import HistoryRow
from app.schemas.ask import AskRecent
from app.services.ask.history import (
    list_answered_questions,
    list_recent_questions,
    read_answered_question,
)

_ASKER = UUID("00000000-0000-4000-8000-0000000000b0")
_SECRET = b"ask-history-test"
_NOW = datetime(2026, 11, 4, 4, tzinfo=UTC)
_T3 = datetime(2026, 11, 4, 3, tzinfo=UTC)
_T2 = datetime(2026, 11, 4, 2, tzinfo=UTC)
_T1 = datetime(2026, 11, 4, 1, tzinfo=UTC)
_A = UUID("00000000-0000-4000-8000-0000000000a3")
_B = UUID("00000000-0000-4000-8000-0000000000a2")
_C = UUID("00000000-0000-4000-8000-0000000000a1")
_D = UUID("00000000-0000-4000-8000-0000000000a4")
_E = UUID("00000000-0000-4000-8000-0000000000a9")
_SPIRIT = UUID("00000000-0000-4000-8000-0000000000a8")


def _row(
    question_id: UUID,
    replied_at: datetime,
    *,
    rank: str | None,
    rating: str | None = None,
    name: str = "雾生",
    question: str = "谁会倒茶",
    skill_title: str | None = None,
    spirit_id: UUID | None = None,
) -> HistoryRow:
    return HistoryRow(
        question_id,
        question,
        name,
        rank,
        replied_at,
        rating,
        skill_title,
        spirit_id,
    )


class _History:
    def __init__(self, rows: list[HistoryRow]) -> None:
        self.rows = rows
        self.turns = {
            _A: (("user", "谁会倒茶"), ("spirit", "先烧水。")),
            _B: (("user", "怎么收"), ("spirit", "阴干。")),
        }
        self.calls = 0

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
        del asker_user_id
        self.calls += 1
        chosen = []
        for row in self.rows:
            if row.first_reply_at > snapshot_at:
                continue
            if view == "rated" and row.rating is None:
                continue
            if view == "unrated" and row.rating is not None:
                continue
            if cursor_time is not None and cursor_id is not None:
                later = row.first_reply_at > cursor_time
                same = row.first_reply_at == cursor_time and row.question_id >= cursor_id
                if later or same:
                    continue
            chosen.append(row)
        chosen.sort(key=lambda item: (item.first_reply_at, item.question_id), reverse=True)
        return chosen[:fetch_limit]

    async def read_history(
        self, asker_user_id: UUID, question_id: UUID
    ) -> tuple[HistoryRow, tuple[tuple[str, str], ...]] | None:
        del asker_user_id
        self.calls += 1
        for row in self.rows:
            if row.question_id == question_id:
                return row, self.turns.get(question_id, ())
        return None


def _rows() -> list[HistoryRow]:
    return [
        _row(_C, _T1, rank=None, skill_title=""),
        _row(_A, _T3, rank="门边学徒", rating="满意"),
        _row(_B, _T2, rank="搭手帮工"),
        _row(_D, _T3, rank="镇场宗师"),
        _row(_E, _NOW, rank="门边学徒", skill_title="泡茶", spirit_id=_SPIRIT),
    ]


@pytest.mark.asyncio
async def test_history_is_newest_first_and_keeps_the_opening_rank() -> None:
    history = _History(_rows())
    first = await list_answered_questions(
        history,
        asker_user_id=_ASKER,
        view="all",
        cursor=None,
        secret=_SECRET,
        now=_NOW,
        limit=2,
    )
    assert [item.id for item in first.items] == [_E, _D]
    assert first.has_more is True
    assert first.next_cursor is not None
    assert first.items[0].rank_name == "门边学徒"
    assert first.items[0].skill_title == "泡茶"
    assert first.items[0].spirit_id == _SPIRIT
    assert first.items[1].rank_name == "镇场宗师"
    assert first.items[1].skill_title is None
    second = await list_answered_questions(
        history,
        asker_user_id=_ASKER,
        view="all",
        cursor=first.next_cursor,
        secret=_SECRET,
        now=_NOW,
        limit=2,
    )
    assert [item.id for item in second.items] == [_A, _B]
    assert second.has_more is True
    assert second.items[0].rank_name == "门边学徒"
    assert second.items[0].rated is True
    assert second.items[0].rating == "满意"
    assert second.items[1].rank_name == "搭手帮工"
    third = await list_answered_questions(
        history,
        asker_user_id=_ASKER,
        view="all",
        cursor=second.next_cursor,
        secret=_SECRET,
        now=_NOW,
        limit=2,
    )
    assert [item.id for item in third.items] == [_C]
    assert third.items[0].rank_name is None
    assert third.items[0].skill_title is None
    assert third.items[0].spirit_id is None
    assert third.items[0].rated is False
    assert third.has_more is False
    assert third.next_cursor is None


@pytest.mark.asyncio
async def test_recent_is_three_and_filters_do_not_share_a_cursor() -> None:
    history = _History(_rows())
    recent = await list_recent_questions(history, asker_user_id=_ASKER, now=_NOW)
    assert [item.id for item in recent.items] == [_E, _D, _A]
    assert "next_cursor" not in AskRecent.model_fields
    rated = await list_answered_questions(
        history,
        asker_user_id=_ASKER,
        view="rated",
        cursor=None,
        secret=_SECRET,
        now=_NOW,
    )
    assert [item.id for item in rated.items] == [_A]
    unrated = await list_answered_questions(
        history,
        asker_user_id=_ASKER,
        view="unrated",
        cursor=None,
        secret=_SECRET,
        now=_NOW,
        limit=10,
    )
    assert _A not in [item.id for item in unrated.items]
    fresh = _History(_rows())
    with pytest.raises(ApiError) as caught:
        await list_answered_questions(
            fresh,
            asker_user_id=_ASKER,
            view="unrated",
            cursor=encode_ask_cursor(
                MessageCursor(
                    version=1,
                    sort_time=_T3,
                    id=_A,
                    snapshot_at=_NOW,
                    filter_hash=ask_filter_hash(_ASKER, "rated"),
                ),
                secret=_SECRET,
            ),
            secret=_SECRET,
            now=_NOW,
        )
    assert caught.value.code == "INVALID_CURSOR"
    assert caught.value.message == "invalid cursor"
    assert caught.value.status_code == 422
    assert fresh.calls == 0


@pytest.mark.asyncio
async def test_detail_returns_turns_and_the_opening_rank() -> None:
    history = _History(_rows())
    detail = await read_answered_question(history, asker_user_id=_ASKER, question_id=_A)
    assert detail.rank_name == "门边学徒"
    assert detail.skill_title is None
    assert detail.rating == "满意"
    assert [(turn.role, turn.text) for turn in detail.turns] == [
        ("user", "谁会倒茶"),
        ("spirit", "先烧水。"),
    ]
    assert detail.spirit_name == "雾生"
    with pytest.raises(ApiError) as missing:
        await read_answered_question(
            history,
            asker_user_id=_ASKER,
            question_id=UUID("00000000-0000-4000-8000-0000000000aa"),
        )
    assert missing.value.code == "NOT_FOUND"
    assert missing.value.message == "not found"


def test_history_queries_hide_skill_text() -> None:
    source = inspect.getsource(history_sql)
    for forbidden in (
        "snapshot_body",
        "snapshot_skill_slug",
        "snapshot_detail",
        "keywords",
        "reference_links",
        "skill_slug",
        "points",
        "rank_for_points",
    ):
        assert forbidden not in source
    assert "q.first_reply_at IS NOT NULL" in source
    assert "ORDER BY q.first_reply_at DESC, q.id DESC" in source
    assert "q.snapshot_rank_name" in source
    assert "q.snapshot_title" in source
    assert "q.spirit_id," in source
    assert "craft_skills" not in source
