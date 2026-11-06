"""Owner list pages use the ledger total and never return a skill slug."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.core.errors import ApiError
from app.repositories.craft import (
    LEDGER_TOTAL_SQL,
    LIST_OWNER_SKILLS_SQL,
    OWNER_SKILL_DETAIL_SQL,
    OwnerSkillDetail,
)
from app.services.craft.owner_list import get_owner_skill, list_owner_skills

_OWNER = UUID("00000000-0000-4000-8000-000000000010")
_OTHER = UUID("00000000-0000-4000-8000-000000000019")
_SPIRIT = UUID("00000000-0000-4000-8000-000000000012")
_SECRET = b"craft-list-test-secret"
_NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def _skill(
    craft_id: UUID,
    *,
    title: str,
    points: int,
    minutes: int,
    visibility: str = "public",
    detail: str = "先烧水，再闷三分钟。",
) -> OwnerSkillDetail:
    return OwnerSkillDetail(
        craft_id=craft_id,
        title=title,
        visibility=visibility,
        points=points,
        updated_at=_NOW - timedelta(minutes=minutes),
        keywords=("水温",),
        detail=detail,
        reference_links=("https://example.com/tea",),
    )


class _Store:
    def __init__(self, rows: list[OwnerSkillDetail], ledger: int | None) -> None:
        self.rows = rows
        self.ledger = ledger
        self.spirit_id: UUID | None = _SPIRIT

    async def transaction_now(self) -> datetime:
        return _NOW

    async def owner_spirit(self, owner_id: UUID) -> UUID | None:
        del owner_id
        return self.spirit_id

    async def ledger_total(self, spirit_id: UUID) -> int:
        del spirit_id
        if self.ledger is None:
            return 0
        return self.ledger

    async def list_skills(
        self,
        spirit_id: UUID,
        *,
        snapshot_at: datetime,
        fetch_limit: int,
        cursor_time: datetime | None,
        cursor_id: UUID | None,
    ) -> list[OwnerSkillDetail]:
        del spirit_id
        chosen = []
        for row in self.rows:
            if row.updated_at > snapshot_at:
                continue
            if cursor_time is not None and cursor_id is not None:
                newer = row.updated_at > cursor_time
                same_and_seen = row.updated_at == cursor_time and row.craft_id >= cursor_id
                if newer or same_and_seen:
                    continue
            chosen.append(row)
        chosen.sort(key=lambda row: (row.updated_at, row.craft_id), reverse=True)
        return chosen[:fetch_limit]

    async def get_skill(self, craft_id: UUID, spirit_id: UUID) -> OwnerSkillDetail | None:
        del spirit_id
        for row in self.rows:
            if row.craft_id == craft_id:
                return row
        return None


class _Unit:
    def __init__(self, store: _Store) -> None:
        self.store = store

    @asynccontextmanager
    async def transaction(self):  # type: ignore[no-untyped-def]
        yield self.store


def _ids() -> tuple[UUID, UUID, UUID]:
    return (
        UUID("00000000-0000-4000-8000-0000000000a1"),
        UUID("00000000-0000-4000-8000-0000000000a2"),
        UUID("00000000-0000-4000-8000-0000000000a3"),
    )


async def test_page_total_comes_from_the_ledger_not_a_sum() -> None:
    older, newer, _hidden = _ids()
    store = _Store(
        [
            _skill(older, title="旧", points=10, minutes=10),
            _skill(newer, title="新", points=5, minutes=1),
        ],
        ledger=100,
    )
    page = await list_owner_skills(
        _Unit(store), owner_id=_OWNER, cursor=None, secret=_SECRET, limit=10
    )
    assert page.total_points == 100
    assert [item.points for item in page.items] == [5, 10]
    assert [item.title for item in page.items] == ["新", "旧"]
    dumped = page.model_dump()
    assert "skill_slug" not in str(dumped)
    assert "keywords" not in dumped["items"][0]
    assert page.items[0].rank_name == "门边学徒"
    assert page.items[0].points_to_next == 95
    assert page.items[0].at_max_rank is False


async def test_cursor_returns_the_next_older_page_without_the_slug() -> None:
    first, second, third = _ids()
    store = _Store(
        [
            _skill(first, title="一", points=1, minutes=3),
            _skill(second, title="二", points=1, minutes=2),
            _skill(third, title="三", points=1, minutes=1),
        ],
        ledger=3,
    )
    unit = _Unit(store)
    opened = await list_owner_skills(
        unit, owner_id=_OWNER, cursor=None, secret=_SECRET, limit=2
    )
    assert [item.title for item in opened.items] == ["三", "二"]
    assert opened.has_more is True
    assert opened.next_cursor is not None
    assert "pour-tea" not in opened.next_cursor
    nxt = await list_owner_skills(
        unit, owner_id=_OWNER, cursor=opened.next_cursor, secret=_SECRET, limit=2
    )
    assert [item.title for item in nxt.items] == ["一"]
    assert nxt.has_more is False
    assert nxt.next_cursor is None
    assert nxt.total_points == 3
    with pytest.raises(ApiError) as other:
        await list_owner_skills(
            unit, owner_id=_OTHER, cursor=opened.next_cursor, secret=_SECRET, limit=2
        )
    assert other.value.code == "INVALID_CURSOR"


async def test_bad_cursor_is_rejected() -> None:
    store = _Store([], ledger=0)
    with pytest.raises(ApiError) as caught:
        await list_owner_skills(
            _Unit(store), owner_id=_OWNER, cursor="nope", secret=_SECRET, limit=2
        )
    assert caught.value.code == "INVALID_CURSOR"


async def test_unpublished_stays_on_the_list_as_not_public() -> None:
    craft_id, _, _ = _ids()
    store = _Store(
        [_skill(craft_id, title="未公开", points=0, minutes=1, visibility="unpublished")],
        ledger=0,
    )
    page = await list_owner_skills(
        _Unit(store), owner_id=_OWNER, cursor=None, secret=_SECRET, limit=10
    )
    assert page.items[0].visibility == "private"
    assert page.items[0].rank_name is None
    assert page.items[0].points_to_next == 1


async def test_detail_shows_the_teaching_fields_and_the_top_rank() -> None:
    craft_id, _, _ = _ids()
    store = _Store(
        [_skill(craft_id, title="镇场", points=1500, minutes=1, detail="按这个火候收汁。")],
        ledger=2000,
    )
    detail = await get_owner_skill(_Unit(store), owner_id=_OWNER, craft_id=craft_id)
    assert detail.points == 1500
    assert detail.rank_name == "镇场宗师"
    assert detail.points_to_next is None
    assert detail.at_max_rank is True
    assert detail.keywords == ["水温"]
    assert detail.description == "按这个火候收汁。"
    assert detail.reference_links == ["https://example.com/tea"]
    assert "skill_slug" not in detail.model_dump()
    assert "body" not in detail.model_dump()


async def test_missing_ledger_does_not_invent_a_total() -> None:
    craft_id, _, _ = _ids()
    store = _Store([_skill(craft_id, title="有分", points=7, minutes=1)], ledger=None)
    page = await list_owner_skills(
        _Unit(store), owner_id=_OWNER, cursor=None, secret=_SECRET, limit=10
    )
    assert page.total_points == 0
    assert page.items[0].points == 7


async def test_missing_skill_is_not_found() -> None:
    store = _Store([], ledger=0)
    with pytest.raises(ApiError) as caught:
        await get_owner_skill(_Unit(store), owner_id=_OWNER, craft_id=_ids()[0])
    assert caught.value.code == "NOT_FOUND"


def test_read_sql_uses_the_ledger_and_skips_ask_dialogues() -> None:
    assert "ask_questions" not in LIST_OWNER_SKILLS_SQL
    assert "ask_questions" not in OWNER_SKILL_DETAIL_SQL
    assert "SUM(" not in LIST_OWNER_SKILLS_SQL.upper()
    assert "skill_slug" not in LIST_OWNER_SKILLS_SQL
    assert "skill_slug" not in OWNER_SKILL_DETAIL_SQL
    assert "deleted_at IS NULL" in LIST_OWNER_SKILLS_SQL
    assert "craft_ledgers" in LEDGER_TOTAL_SQL
    assert "SUM(" not in LEDGER_TOTAL_SQL.upper()
