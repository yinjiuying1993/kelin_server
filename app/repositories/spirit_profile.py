"""Owner spirit page rows. Public skills stay title and rank only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.spirit_face import SpiritFace
from app.domain.spirit_profile import avatar_url, current_avatar_key, display_name

LOAD_SQL = """
SELECT
    id,
    name,
    nickname,
    stage,
    invite_code,
    hatched_at,
    default_avatar_key,
    custom_avatar_key,
    public.v3_ledger_help_count(id) AS help_count
FROM public.spirits
WHERE user_id = :user_id
"""

SKILL_SQL = """
SELECT title, rank_name
FROM public.craft_skills
WHERE spirit_id = :spirit_id
  AND deleted_at IS NULL
  AND visibility = 'public'
ORDER BY title ASC, id ASC
"""

NICKNAME_SQL = """
UPDATE public.spirits
SET nickname = :nickname, updated_at = now()
WHERE user_id = :user_id
RETURNING id
"""

AVATAR_SQL = """
UPDATE public.spirits
SET custom_avatar_key = :avatar_key, updated_at = now()
WHERE user_id = :user_id
RETURNING id
"""

FACE_SQL = """
SELECT id, name, nickname, default_avatar_key, custom_avatar_key
FROM public.spirits
WHERE user_id = :user_id
"""


@dataclass(frozen=True)
class ProfileSkill:
    title: str
    rank_name: str | None


@dataclass(frozen=True)
class OwnerProfile:
    spirit_id: UUID
    name: str
    nickname: str | None
    stage: str
    invite_code: str
    hatched_at: datetime | None
    default_avatar_key: str
    custom_avatar_key: str | None
    help_count: int
    skills: tuple[ProfileSkill, ...]


class SqlSpiritProfile:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def load(self, user_id: UUID) -> OwnerProfile | None:
        result = await self._session.execute(text(LOAD_SQL), {"user_id": user_id})
        row = result.one_or_none()
        if row is None:
            return None
        skills = await self._session.execute(text(SKILL_SQL), {"spirit_id": row.id})
        return OwnerProfile(
            spirit_id=row.id,
            name=str(row.name),
            nickname=None if row.nickname is None else str(row.nickname),
            stage=str(row.stage),
            invite_code=str(row.invite_code),
            hatched_at=row.hatched_at,
            default_avatar_key=str(row.default_avatar_key),
            custom_avatar_key=None if row.custom_avatar_key is None else str(row.custom_avatar_key),
            help_count=int(row.help_count),
            skills=tuple(
                ProfileSkill(
                    str(item.title),
                    None if item.rank_name is None else str(item.rank_name),
                )
                for item in skills
            ),
        )

    async def owner_face(self, user_id: UUID, *, available: bool = True) -> SpiritFace | None:
        result = await self._session.execute(text(FACE_SQL), {"user_id": user_id})
        row = result.one_or_none()
        if row is None:
            return None
        nickname = None if row.nickname is None else str(row.nickname)
        custom = None if row.custom_avatar_key is None else str(row.custom_avatar_key)
        return SpiritFace(
            spirit_id=row.id,
            display_name=display_name(str(row.name), nickname),
            avatar_url=avatar_url(current_avatar_key(str(row.default_avatar_key), custom)),
            available=available,
        )

    async def set_nickname(self, user_id: UUID, nickname: str | None) -> bool:
        result = await self._session.execute(
            text(NICKNAME_SQL), {"user_id": user_id, "nickname": nickname}
        )
        return result.scalar_one_or_none() is not None

    async def set_custom_avatar(self, user_id: UUID, avatar_key: str | None) -> bool:
        result = await self._session.execute(
            text(AVATAR_SQL), {"user_id": user_id, "avatar_key": avatar_key}
        )
        return result.scalar_one_or_none() is not None
