"""Craft HTTP calls. Index and moderation stay outside the skill transaction."""

from __future__ import annotations

from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser
from app.providers.errors import ProviderError
from app.schemas.craft import CraftDeleted, CraftSkillDetail, CraftSkillPage
from app.services.craft.create import SessionCreateUnit, create_craft_skill
from app.services.craft.edit import (
    SessionEditUnit,
    delete_craft_skill,
    save_craft_skill,
    set_craft_visibility,
)
from app.services.craft.owner_list import SessionReadUnit, get_owner_skill, list_owner_skills
from app.services.match_index.owned import OwnedMatchIndex


class _Moderator(Protocol):
    async def moderate_text(self, text: str) -> str: ...


async def create_owned_skill(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    provider: _Moderator,
    *,
    client_id: UUID,
    title: str,
    keywords: list[str],
    description: str,
    reference_links: list[str],
) -> CraftSkillDetail:
    created = await create_craft_skill(
        SessionCreateUnit(factory, user),
        OwnedMatchIndex(factory, user),
        owner_id=user.id,
        client_id=client_id,
        title=title,
        keywords=keywords,
        detail=description,
        reference_links=reference_links,
        moderation=await _decision(provider, title, keywords, description, reference_links),
    )
    return await get_owner_skill(
        SessionReadUnit(factory, user),
        owner_id=user.id,
        craft_id=created.craft_id,
    )


async def save_owned_skill(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    provider: _Moderator,
    *,
    craft_id: UUID,
    client_id: UUID,
    title: str,
    keywords: list[str],
    description: str,
    reference_links: list[str],
) -> CraftSkillDetail:
    await save_craft_skill(
        SessionEditUnit(factory, user),
        OwnedMatchIndex(factory, user),
        owner_id=user.id,
        client_id=client_id,
        craft_id=craft_id,
        title=title,
        keywords=keywords,
        detail=description,
        reference_links=reference_links,
        moderation=await _decision(provider, title, keywords, description, reference_links),
    )
    return await get_owner_skill(
        SessionReadUnit(factory, user),
        owner_id=user.id,
        craft_id=craft_id,
    )


async def change_owned_visibility(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    *,
    craft_id: UUID,
    client_id: UUID,
    visibility: Literal["public", "private"],
) -> CraftSkillDetail:
    await set_craft_visibility(
        SessionEditUnit(factory, user),
        OwnedMatchIndex(factory, user),
        owner_id=user.id,
        client_id=client_id,
        craft_id=craft_id,
        visibility=visibility,
    )
    return await get_owner_skill(
        SessionReadUnit(factory, user),
        owner_id=user.id,
        craft_id=craft_id,
    )


async def remove_owned_skill(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    *,
    craft_id: UUID,
    client_id: UUID,
) -> CraftDeleted:
    removed = await delete_craft_skill(
        SessionEditUnit(factory, user),
        OwnedMatchIndex(factory, user),
        owner_id=user.id,
        client_id=client_id,
        craft_id=craft_id,
    )
    return CraftDeleted(id=removed.craft_id, deleted=True)


async def list_owned_skills(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    *,
    cursor: str | None,
    secret: bytes,
) -> CraftSkillPage:
    return await list_owner_skills(
        SessionReadUnit(factory, user),
        owner_id=user.id,
        cursor=cursor,
        secret=secret,
    )


async def read_owned_skill(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    *,
    craft_id: UUID,
) -> CraftSkillDetail:
    return await get_owner_skill(
        SessionReadUnit(factory, user),
        owner_id=user.id,
        craft_id=craft_id,
    )


async def _decision(
    provider: _Moderator,
    title: str,
    keywords: list[str],
    description: str,
    reference_links: list[str],
) -> Literal["allow", "block"]:
    try:
        decision = await provider.moderate_text(
            "\n".join([title, *keywords, description, *reference_links])
        )
    except ProviderError as exc:
        raise _unavailable() from exc
    if decision == "block":
        return "block"
    if decision != "allow":
        raise _unavailable()
    return "allow"


def _unavailable() -> ApiError:
    return ApiError(
        "DEPENDENCY_UNAVAILABLE",
        public_error_message("DEPENDENCY_UNAVAILABLE"),
        status_code=503,
        retryable=True,
    )
