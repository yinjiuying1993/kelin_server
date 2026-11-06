"""Create one skill. Index calls stay outside the skill transaction."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser
from app.db.session import claimed_transaction
from app.domain.craft.create import (
    SkillSlugTaken,
    craft_create_request_hash,
)
from app.domain.craft.skill_body import (
    CraftContentRejected,
    CraftModerationRejected,
    CraftSlugExhausted,
    PreparedCraftContent,
    new_skill_slug,
    prepare_craft_content,
    render_skill_body,
)
from app.domain.match_index import MatchIndex, MatchIndexError
from app.repositories.craft import SqlCraftCreateStore, StoredClaim, StoredSkill

_SLUG_INSERT_ATTEMPTS = 8


class CraftCreateStore(Protocol):
    async def lock_owner(self, owner_id: UUID) -> None: ...

    async def owner_spirit(self, owner_id: UUID) -> UUID | None: ...

    async def claim(self, owner_id: UUID, client_id: UUID, request_hash: str) -> StoredClaim: ...

    async def taken_slugs(self, spirit_id: UUID) -> set[str]: ...

    async def insert_unpublished(
        self,
        *,
        spirit_id: UUID,
        skill_slug: str,
        title: str,
        keywords: tuple[str, ...],
        detail: str,
        reference_links: tuple[str, ...],
        body: str,
    ) -> StoredSkill: ...

    async def get_skill(self, craft_id: UUID) -> StoredSkill | None: ...

    async def set_visibility(
        self, craft_id: UUID, visibility: Literal["unpublished", "public"]
    ) -> None: ...

    async def bind_claim(self, owner_id: UUID, client_id: UUID, craft_id: UUID) -> None: ...

    async def complete_claim(self, owner_id: UUID, client_id: UUID, craft_id: UUID) -> None: ...


class CreateUnit(Protocol):
    def transaction(self) -> AbstractAsyncContextManager[CraftCreateStore]: ...


@dataclass(frozen=True)
class CreatedCraftSkill:
    craft_id: UUID
    title: str
    keywords: tuple[str, ...]
    detail: str
    reference_links: tuple[str, ...]
    visibility: Literal["public"]
    points: int


class SessionCreateUnit:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        user: CurrentUser,
    ) -> None:
        self._session_factory = session_factory
        self._user = user

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[SqlCraftCreateStore]:
        async with claimed_transaction(self._session_factory, self._user) as session:
            yield SqlCraftCreateStore(session)


async def create_craft_skill(
    unit: CreateUnit,
    index: MatchIndex,
    *,
    owner_id: UUID,
    client_id: UUID,
    title: str,
    keywords: list[str] | tuple[str, ...],
    detail: str,
    reference_links: list[str] | tuple[str, ...] = (),
    moderation: Literal["allow", "block"],
    rng: Callable[[], str] | None = None,
) -> CreatedCraftSkill:
    try:
        content = prepare_craft_content(
            title=title,
            keywords=keywords,
            detail=detail,
            reference_links=reference_links,
            moderation=moderation,
        )
    except CraftContentRejected as exc:
        raise _error("INVALID_INPUT", 422, details={"field": exc.field}) from exc
    except CraftModerationRejected as exc:
        raise _error("MODERATION_REJECTED", 422) from exc
    request_hash = craft_create_request_hash(content)

    async with unit.transaction() as store:
        await store.lock_owner(owner_id)
        spirit_id = await store.owner_spirit(owner_id)
        if spirit_id is None:
            raise _error("NOT_FOUND", 404)
        claim = await store.claim(owner_id, client_id, request_hash)
        skill = await _skill_for_claim(
            store, claim, content, spirit_id, owner_id, client_id, request_hash, rng
        )
        claim_status = claim.status

    if claim_status == "completed" and skill.visibility == "public":
        return _public(skill)

    if skill.visibility != "public":
        await _stage(index, skill)
        async with unit.transaction() as store:
            await store.set_visibility(skill.craft_id, "public")

    try:
        await index.activate(craft_id=skill.craft_id)
    except MatchIndexError as exc:
        async with unit.transaction() as store:
            await store.set_visibility(skill.craft_id, "unpublished")
        await _withdraw(index, skill.craft_id)
        raise _error("DEPENDENCY_UNAVAILABLE", 503, retryable=True) from exc

    async with unit.transaction() as store:
        await store.complete_claim(owner_id, client_id, skill.craft_id)
    return _public(skill)


async def _skill_for_claim(
    store: CraftCreateStore,
    claim: StoredClaim,
    content: PreparedCraftContent,
    spirit_id: UUID,
    owner_id: UUID,
    client_id: UUID,
    request_hash: str,
    rng: Callable[[], str] | None,
) -> StoredSkill:
    if not claim.inserted:
        if claim.status == "conflict" or claim.request_hash != request_hash:
            raise _error("IDEMPOTENCY_CONFLICT", 409)
        if claim.resource_id is not None:
            existing = await store.get_skill(claim.resource_id)
            if existing is None:
                raise _error("INTERNAL_ERROR", 500)
            return existing
        if claim.status == "completed":
            raise _error("INTERNAL_ERROR", 500)
    skill = await _insert_unique(store, content, spirit_id, rng)
    await store.bind_claim(owner_id, client_id, skill.craft_id)
    return skill


async def _insert_unique(
    store: CraftCreateStore,
    content: PreparedCraftContent,
    spirit_id: UUID,
    rng: Callable[[], str] | None,
) -> StoredSkill:
    taken = await store.taken_slugs(spirit_id)
    for _ in range(_SLUG_INSERT_ATTEMPTS):
        try:
            skill_slug = new_skill_slug(taken, rng)
        except CraftSlugExhausted as exc:
            raise _error("INTERNAL_ERROR", 500) from exc
        try:
            return await store.insert_unpublished(
                spirit_id=spirit_id,
                skill_slug=skill_slug,
                title=content.title,
                keywords=content.keywords,
                detail=content.detail,
                reference_links=content.reference_links,
                body=render_skill_body(content, skill_slug),
            )
        except SkillSlugTaken:
            taken.add(skill_slug)
    raise _error("INTERNAL_ERROR", 500)


async def _stage(index: MatchIndex, skill: StoredSkill) -> None:
    try:
        await index.stage(
            craft_id=skill.craft_id,
            spirit_id=skill.spirit_id,
            skill_slug=skill.skill_slug,
            title=skill.title,
            keywords=skill.keywords,
        )
    except MatchIndexError as exc:
        raise _error("DEPENDENCY_UNAVAILABLE", 503, retryable=True) from exc


async def _withdraw(index: MatchIndex, craft_id: UUID) -> None:
    try:
        await index.withdraw(craft_id=craft_id)
    except MatchIndexError as exc:
        raise _error("DEPENDENCY_UNAVAILABLE", 503, retryable=True) from exc


def _public(skill: StoredSkill) -> CreatedCraftSkill:
    return CreatedCraftSkill(
        craft_id=skill.craft_id,
        title=skill.title,
        keywords=skill.keywords,
        detail=skill.detail,
        reference_links=skill.reference_links,
        visibility="public",
        points=skill.points,
    )


def _error(
    code: str,
    status_code: int,
    *,
    retryable: bool = False,
    details: dict[str, str] | None = None,
) -> ApiError:
    return ApiError(
        code,
        public_error_message(code),
        status_code=status_code,
        retryable=retryable,
        details=details,
    )
