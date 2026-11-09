"""Ask dialogue rows. A snapshot is copied once and is not a live skill read."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Text, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.ask.official import OFFICIAL_SLUG

FIND_SQL = """
SELECT
    id,
    question_text,
    snapshot_title,
    snapshot_rank_name,
    snapshot_detail,
    snapshot_reference_links,
    snapshot_body,
    first_reply_at,
    snapshot_skill_slug,
    agent_session_id
FROM public.ask_questions
WHERE asker_user_id = :asker_user_id
  AND client_id = :client_id
"""

INSERT_SQL = """
INSERT INTO public.ask_questions (
    asker_user_id,
    client_id,
    spirit_id,
    spirit_owner_user_id,
    question_text,
    snapshot_title,
    snapshot_rank_name,
    snapshot_skill_slug,
    snapshot_body,
    snapshot_detail,
    snapshot_reference_links
) VALUES (
    :asker_user_id,
    :client_id,
    :spirit_id,
    :spirit_owner_user_id,
    :question_text,
    :snapshot_title,
    :snapshot_rank_name,
    :snapshot_skill_slug,
    :snapshot_body,
    :snapshot_detail,
    :snapshot_reference_links
)
RETURNING id
"""

OFFICIAL_SQL = "SELECT spirit_id, owner_user_id FROM public.v3_official_ask_target()"

SNAPSHOT_SQL = """
SELECT
    s.title,
    s.keywords,
    s.detail,
    s.reference_links,
    s.body,
    s.points,
    public.v3_spirit_owner_id(s.spirit_id) AS spirit_owner_user_id
FROM public.craft_skills s
WHERE s.skill_slug = :skill_slug
  AND s.spirit_id = :spirit_id
  AND s.deleted_at IS NULL
  AND s.visibility = 'public'
"""


@dataclass(frozen=True)
class SkillSnapshot:
    title: str
    keywords: tuple[str, ...]
    detail: str
    reference_links: tuple[str, ...]
    body: str
    points: int
    spirit_owner_user_id: UUID


@dataclass(frozen=True)
class OfficialTarget:
    spirit_id: UUID
    owner_user_id: UUID


@dataclass(frozen=True)
class StoredAsk:
    question_id: UUID
    question_text: str
    snapshot_title: str
    snapshot_rank_name: str | None
    snapshot_detail: str
    snapshot_reference_links: tuple[str, ...]
    snapshot_body: str
    first_reply_at: datetime | None
    official: bool = False
    agent_session_id: str | None = None


class SqlAskDialogue:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find(self, asker_user_id: UUID, client_id: UUID) -> StoredAsk | None:
        result = await self._session.execute(
            text(FIND_SQL),
            {"asker_user_id": asker_user_id, "client_id": client_id},
        )
        row = result.one_or_none()
        if row is None:
            return None
        return _stored(row)

    async def insert(
        self,
        *,
        asker_user_id: UUID,
        client_id: UUID,
        spirit_id: UUID,
        spirit_owner_user_id: UUID,
        question_text: str,
        snapshot_title: str,
        snapshot_rank_name: str | None,
        snapshot_skill_slug: str,
        snapshot_body: str,
        snapshot_detail: str,
        snapshot_reference_links: tuple[str, ...],
    ) -> UUID:
        result = await self._session.execute(
            text(INSERT_SQL).bindparams(
                bindparam("snapshot_reference_links", type_=ARRAY(Text()))
            ),
            {
                "asker_user_id": asker_user_id,
                "client_id": client_id,
                "spirit_id": spirit_id,
                "spirit_owner_user_id": spirit_owner_user_id,
                "question_text": question_text,
                "snapshot_title": snapshot_title,
                "snapshot_rank_name": snapshot_rank_name,
                "snapshot_skill_slug": snapshot_skill_slug,
                "snapshot_body": snapshot_body,
                "snapshot_detail": snapshot_detail,
                "snapshot_reference_links": list(snapshot_reference_links),
            },
        )
        return result.scalar_one()

    async def official(self) -> OfficialTarget | None:
        result = await self._session.execute(text(OFFICIAL_SQL))
        row = result.one_or_none()
        if row is None or row.spirit_id is None or row.owner_user_id is None:
            return None
        return OfficialTarget(spirit_id=row.spirit_id, owner_user_id=row.owner_user_id)

    async def snapshot(self, skill_slug: str, spirit_id: UUID) -> SkillSnapshot | None:
        result = await self._session.execute(
            text(SNAPSHOT_SQL),
            {"skill_slug": skill_slug, "spirit_id": spirit_id},
        )
        row = result.one_or_none()
        if row is None:
            return None
        return SkillSnapshot(
            title=str(row.title),
            keywords=tuple(row.keywords or ()),
            detail=str(row.detail),
            reference_links=tuple(row.reference_links or ()),
            body=str(row.body),
            points=int(row.points),
            spirit_owner_user_id=row.spirit_owner_user_id,
        )


def _stored(row: object) -> StoredAsk:
    return StoredAsk(
        question_id=row.id,  # type: ignore[attr-defined]
        question_text=str(row.question_text),  # type: ignore[attr-defined]
        snapshot_title=str(row.snapshot_title),  # type: ignore[attr-defined]
        snapshot_rank_name=row.snapshot_rank_name,  # type: ignore[attr-defined]
        snapshot_detail=str(row.snapshot_detail),  # type: ignore[attr-defined]
        snapshot_reference_links=tuple(row.snapshot_reference_links or ()),  # type: ignore[attr-defined]
        snapshot_body=str(row.snapshot_body),  # type: ignore[attr-defined]
        first_reply_at=row.first_reply_at,  # type: ignore[attr-defined]
        official=str(row.snapshot_skill_slug) == OFFICIAL_SLUG,  # type: ignore[attr-defined]
        agent_session_id=_session_id(row.agent_session_id),  # type: ignore[attr-defined]
    )


def _session_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None
