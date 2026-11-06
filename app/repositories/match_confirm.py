"""Confirm returned skill ids. The match prompt does not use this row."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Text, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.ask.match import ConfirmableSkill

CONFIRM_SQL = """
SELECT
    s.skill_slug,
    s.spirit_id,
    s.title,
    s.keywords,
    s.visibility,
    s.deleted_at,
    s.points,
    s.content_updated_at,
    public.v3_ledger_help_count(s.spirit_id) AS help_count
FROM public.craft_skills s
WHERE s.skill_slug = ANY(:slugs)
"""


class SqlMatchConfirm:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def confirm(self, skill_slugs: tuple[str, ...]) -> list[ConfirmableSkill]:
        if not skill_slugs:
            return []
        statement = text(CONFIRM_SQL).bindparams(bindparam("slugs", type_=ARRAY(Text())))
        result = await self._session.execute(statement, {"slugs": list(skill_slugs)})
        return [_row(row) for row in result]


def _row(row: object) -> ConfirmableSkill:
    deleted_at = row.deleted_at  # type: ignore[attr-defined]
    help_count = row.help_count  # type: ignore[attr-defined]
    updated_at = row.content_updated_at  # type: ignore[attr-defined]
    return ConfirmableSkill(
        skill_slug=str(row.skill_slug),  # type: ignore[attr-defined]
        spirit_id=row.spirit_id,  # type: ignore[attr-defined]
        title=str(row.title),  # type: ignore[attr-defined]
        keywords=tuple(row.keywords or ()),  # type: ignore[attr-defined]
        public=deleted_at is None and row.visibility == "public",  # type: ignore[attr-defined]
        points=int(row.points),  # type: ignore[attr-defined]
        help_count=0 if help_count is None else int(help_count),
        updated_at=updated_at if isinstance(updated_at, datetime) else datetime.min,
    )
