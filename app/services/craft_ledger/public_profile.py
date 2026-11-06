"""Assemble a spirit's public profile from the allowed columns."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.core.errors import ApiError, public_error_message
from app.repositories.spirit_public import PublicProfileRow
from app.schemas.ask import PublicSkill, SpiritPublicProfile


class PublicProfileReader(Protocol):
    async def read(self, spirit_id: UUID) -> PublicProfileRow | None: ...


async def read_public_profile(
    reader: PublicProfileReader, *, spirit_id: UUID
) -> SpiritPublicProfile:
    row = await reader.read(spirit_id)
    if row is None:
        raise ApiError("NOT_FOUND", public_error_message("NOT_FOUND"), status_code=404)
    return SpiritPublicProfile(
        id=row.spirit_id,
        name=row.name,
        help_count=row.help_count,
        skills=[
            PublicSkill(title=skill.title, rank_name=skill.rank_name) for skill in row.skills
        ],
    )
