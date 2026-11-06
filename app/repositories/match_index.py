"""Persist match-index slots. Columns are title and keywords only."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from sqlalchemy import ARRAY, Text, bindparam, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause

from app.domain.match_index.rows import MatchRow

UPSERT_SQL = """
INSERT INTO public.craft_match_index (
    spirit_id,
    craft_skill_id,
    skill_slug,
    active_title,
    active_keywords,
    staged_title,
    staged_keywords
) VALUES (
    :spirit_id,
    :craft_id,
    :skill_slug,
    :active_title,
    :active_keywords,
    :staged_title,
    :staged_keywords
)
ON CONFLICT (craft_skill_id) DO UPDATE SET
    active_title = EXCLUDED.active_title,
    active_keywords = EXCLUDED.active_keywords,
    staged_title = EXCLUDED.staged_title,
    staged_keywords = EXCLUDED.staged_keywords,
    updated_at = now()
"""

_SLOT_COLUMNS = """
    spirit_id,
    craft_skill_id,
    skill_slug,
    active_title,
    active_keywords,
    staged_title,
    staged_keywords
"""

GET_SQL = f"""
SELECT {_SLOT_COLUMNS}
FROM public.craft_match_index
WHERE craft_skill_id = :craft_id
"""

LIST_SQL = f"""
SELECT {_SLOT_COLUMNS}
FROM public.craft_match_index
"""

ASK_SQL = """
SELECT
    spirit_id,
    craft_skill_id,
    skill_slug,
    active_title,
    active_keywords
FROM public.craft_match_index
WHERE active_title IS NOT NULL
  AND (
    CAST(:exclude_spirit AS uuid) IS NULL
    OR spirit_id <> CAST(:exclude_spirit AS uuid)
  )
"""


class MatchStore(Protocol):
    async def get(self, craft_id: UUID) -> MatchRow | None: ...

    async def save(self, row: MatchRow) -> None: ...

    async def list_rows(self) -> list[MatchRow]: ...

    async def list_for_asker(self, exclude_spirit_id: UUID | None) -> list[MatchRow]: ...


class SqlMatchIndexStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, craft_id: UUID) -> MatchRow | None:
        result = await self._session.execute(text(GET_SQL), {"craft_id": craft_id})
        row = result.one_or_none()
        if row is None:
            return None
        return _row(row)

    async def save(self, row: MatchRow) -> None:
        await self._session.execute(
            _arrays(UPSERT_SQL, "active_keywords", "staged_keywords"),
            {
                "spirit_id": row.spirit_id,
                "craft_id": row.craft_id,
                "skill_slug": row.skill_slug,
                "active_title": row.active_title,
                "active_keywords": _list(row.active_keywords),
                "staged_title": row.staged_title,
                "staged_keywords": _list(row.staged_keywords),
            },
        )

    async def list_rows(self) -> list[MatchRow]:
        result = await self._session.execute(text(LIST_SQL))
        return [_row(row) for row in result]

    async def list_for_asker(self, exclude_spirit_id: UUID | None) -> list[MatchRow]:
        statement = text(ASK_SQL).bindparams(
            bindparam("exclude_spirit", type_=PG_UUID(as_uuid=True))
        )
        result = await self._session.execute(
            statement, {"exclude_spirit": exclude_spirit_id}
        )
        return [_active_row(row) for row in result]


def _list(values: tuple[str, ...] | None) -> list[str] | None:
    if values is None:
        return None
    return list(values)


def _row(row: object) -> MatchRow:
    return MatchRow(
        craft_id=row.craft_skill_id,  # type: ignore[attr-defined]
        spirit_id=row.spirit_id,  # type: ignore[attr-defined]
        skill_slug=str(row.skill_slug),  # type: ignore[attr-defined]
        active_title=row.active_title,  # type: ignore[attr-defined]
        active_keywords=_tuple(row.active_keywords),  # type: ignore[attr-defined]
        staged_title=row.staged_title,  # type: ignore[attr-defined]
        staged_keywords=_tuple(row.staged_keywords),  # type: ignore[attr-defined]
    )


def _active_row(row: object) -> MatchRow:
    return MatchRow(
        craft_id=row.craft_skill_id,  # type: ignore[attr-defined]
        spirit_id=row.spirit_id,  # type: ignore[attr-defined]
        skill_slug=str(row.skill_slug),  # type: ignore[attr-defined]
        active_title=row.active_title,  # type: ignore[attr-defined]
        active_keywords=_tuple(row.active_keywords),  # type: ignore[attr-defined]
        staged_title=None,
        staged_keywords=None,
    )


def _tuple(values: list[str] | None) -> tuple[str, ...] | None:
    if values is None:
        return None
    return tuple(values)


def _arrays(sql: str, *names: str) -> TextClause:
    statement = text(sql)
    return statement.bindparams(*(bindparam(name, type_=ARRAY(Text())) for name in names))
