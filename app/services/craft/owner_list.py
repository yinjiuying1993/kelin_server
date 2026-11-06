"""Owner skill list and detail. The page total is the ledger, not a sum."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser
from app.db.session import claimed_read_transaction
from app.domain.cursor import (
    CURSOR_VERSION,
    InvalidCursor,
    MessageCursor,
    craft_filter_hash,
    decode_craft_cursor,
    encode_craft_cursor,
)
from app.repositories.craft import OwnerSkillDetail, OwnerSkillRow, SqlCraftReadStore
from app.schemas.craft import (
    CraftSkillDetail,
    CraftSkillPage,
    CraftSkillSummary,
    rank_for_points,
)

CRAFT_LIST_PAGE_SIZE = 20
OwnerVisibility = Literal["public", "private"]


class CraftReadStore(Protocol):
    async def transaction_now(self) -> datetime: ...

    async def owner_spirit(self, owner_id: UUID) -> UUID | None: ...

    async def ledger_total(self, spirit_id: UUID) -> int: ...

    async def list_skills(
        self,
        spirit_id: UUID,
        *,
        snapshot_at: datetime,
        fetch_limit: int,
        cursor_time: datetime | None,
        cursor_id: UUID | None,
    ) -> list[OwnerSkillRow]: ...

    async def get_skill(self, craft_id: UUID, spirit_id: UUID) -> OwnerSkillDetail | None: ...


class ReadUnit(Protocol):
    def transaction(self) -> AbstractAsyncContextManager[CraftReadStore]: ...


class SessionReadUnit:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], user: CurrentUser
    ) -> None:
        self._session_factory = session_factory
        self._user = user

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[SqlCraftReadStore]:
        async with claimed_read_transaction(self._session_factory, self._user) as session:
            yield SqlCraftReadStore(session)


async def list_owner_skills(
    unit: ReadUnit,
    *,
    owner_id: UUID,
    cursor: str | None,
    secret: bytes,
    limit: int = CRAFT_LIST_PAGE_SIZE,
) -> CraftSkillPage:
    filter_hash = craft_filter_hash(owner_id)
    cursor_time, cursor_id, snapshot_at = _cursor_position(cursor, secret, filter_hash)
    async with unit.transaction() as store:
        if snapshot_at is None:
            snapshot_at = await store.transaction_now()
        spirit_id = await store.owner_spirit(owner_id)
        if spirit_id is None:
            return CraftSkillPage(
                total_points=0, items=[], next_cursor=None, has_more=False
            )
        total_points = await store.ledger_total(spirit_id)
        rows = await store.list_skills(
            spirit_id,
            snapshot_at=snapshot_at,
            fetch_limit=limit + 1,
            cursor_time=cursor_time,
            cursor_id=cursor_id,
        )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = None
    if has_more:
        last = page[-1]
        next_cursor = encode_craft_cursor(
            MessageCursor(
                version=CURSOR_VERSION,
                sort_time=last.updated_at,
                id=last.craft_id,
                snapshot_at=snapshot_at,
                filter_hash=filter_hash,
            ),
            secret=secret,
        )
    return CraftSkillPage(
        total_points=total_points,
        items=[_summary(row) for row in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def get_owner_skill(
    unit: ReadUnit,
    *,
    owner_id: UUID,
    craft_id: UUID,
) -> CraftSkillDetail:
    async with unit.transaction() as store:
        spirit_id = await store.owner_spirit(owner_id)
        if spirit_id is None:
            raise _error("NOT_FOUND", 404)
        row = await store.get_skill(craft_id, spirit_id)
    if row is None:
        raise _error("NOT_FOUND", 404)
    rank = rank_for_points(row.points)
    return CraftSkillDetail(
        id=row.craft_id,
        title=row.title,
        visibility=_owner_visibility(row.visibility),
        points=row.points,
        rank_name=rank.name,
        points_to_next=rank.points_to_next,
        at_max_rank=rank.at_max_rank,
        updated_at=row.updated_at,
        keywords=list(row.keywords),
        description=row.detail,
        reference_links=list(row.reference_links),
    )


def _cursor_position(
    cursor: str | None, secret: bytes, filter_hash: str
) -> tuple[datetime | None, UUID | None, datetime | None]:
    if cursor is None:
        return None, None, None
    try:
        decoded = decode_craft_cursor(
            cursor, secret=secret, expected_filter_hash=filter_hash
        )
    except InvalidCursor as exc:
        raise _error("INVALID_CURSOR", 422) from exc
    return decoded.sort_time, decoded.id, decoded.snapshot_at


def _summary(row: OwnerSkillRow) -> CraftSkillSummary:
    rank = rank_for_points(row.points)
    return CraftSkillSummary(
        id=row.craft_id,
        title=row.title,
        visibility=_owner_visibility(row.visibility),
        points=row.points,
        rank_name=rank.name,
        points_to_next=rank.points_to_next,
        at_max_rank=rank.at_max_rank,
        updated_at=row.updated_at,
    )


def _owner_visibility(value: str) -> OwnerVisibility:
    if value == "public":
        return "public"
    return "private"


def _error(code: str, status_code: int) -> ApiError:
    return ApiError(code, public_error_message(code), status_code=status_code)
