"""Save, hide, republish, and soft-delete one skill.

Index calls stay outside the skill transaction and use the match-index service.
Ask snapshots and points are not written here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser
from app.db.session import claimed_transaction
from app.domain.craft.edit import (
    CRAFT_SKILL_DELETE_OPERATION,
    CRAFT_SKILL_SAVE_OPERATION,
    CRAFT_SKILL_VISIBILITY_OPERATION,
    craft_delete_request_hash,
    craft_save_request_hash,
    craft_visibility_request_hash,
)
from app.domain.craft.skill_body import (
    CraftContentRejected,
    CraftModerationRejected,
    prepare_craft_content,
    render_skill_body,
)
from app.domain.match_index import MatchIndex, MatchIndexError
from app.repositories.craft import EditableSkill, SqlCraftEditStore, StoredClaim

VisibilityTarget = Literal["public", "private"]


class CraftEditStore(Protocol):
    async def lock_owner(self, owner_id: UUID) -> None: ...

    async def owner_spirit(self, owner_id: UUID) -> UUID | None: ...

    async def claim(
        self, owner_id: UUID, client_id: UUID, request_hash: str, operation: str
    ) -> StoredClaim: ...

    async def complete_claim(
        self, owner_id: UUID, client_id: UUID, craft_id: UUID, operation: str
    ) -> None: ...

    async def get_skill(self, craft_id: UUID, spirit_id: UUID) -> EditableSkill | None: ...

    async def write_draft(
        self,
        craft_id: UUID,
        *,
        title: str,
        keywords: tuple[str, ...],
        detail: str,
        reference_links: tuple[str, ...],
        body: str,
    ) -> None: ...

    async def promote_draft(self, craft_id: UUID) -> bool: ...

    async def restore_visible(
        self,
        craft_id: UUID,
        *,
        title: str,
        keywords: tuple[str, ...],
        detail: str,
        reference_links: tuple[str, ...],
        body: str,
        draft_title: str,
        draft_keywords: tuple[str, ...],
        draft_detail: str,
        draft_reference_links: tuple[str, ...],
        draft_body: str,
    ) -> None: ...

    async def replace_visible(
        self,
        craft_id: UUID,
        *,
        title: str,
        keywords: tuple[str, ...],
        detail: str,
        reference_links: tuple[str, ...],
        body: str,
    ) -> None: ...

    async def set_visibility(self, craft_id: UUID, visibility: str) -> None: ...

    async def mark_deleted(self, craft_id: UUID) -> None: ...


class EditUnit(Protocol):
    def transaction(self) -> AbstractAsyncContextManager[CraftEditStore]: ...


@dataclass(frozen=True)
class CraftSkillView:
    craft_id: UUID
    title: str
    keywords: tuple[str, ...]
    detail: str
    reference_links: tuple[str, ...]
    visibility: Literal["unpublished", "public", "private"]
    points: int


@dataclass(frozen=True)
class CraftSkillRemoved:
    craft_id: UUID
    deleted: Literal[True]


class SessionEditUnit:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], user: CurrentUser
    ) -> None:
        self._session_factory = session_factory
        self._user = user

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[SqlCraftEditStore]:
        async with claimed_transaction(self._session_factory, self._user) as session:
            yield SqlCraftEditStore(session)


async def save_craft_skill(
    unit: EditUnit,
    index: MatchIndex,
    *,
    owner_id: UUID,
    client_id: UUID,
    craft_id: UUID,
    title: str,
    keywords: list[str] | tuple[str, ...],
    detail: str,
    reference_links: list[str] | tuple[str, ...] = (),
    moderation: Literal["allow", "block"],
) -> CraftSkillView:
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
    request_hash = craft_save_request_hash(craft_id, content)

    async with unit.transaction() as store:
        skill = await _owned_skill(store, owner_id, craft_id)
        claim = await _claim(
            store, owner_id, client_id, request_hash, CRAFT_SKILL_SAVE_OPERATION
        )
        if claim.status == "completed":
            return _view(skill)
        if skill.visibility != "public":
            body = render_skill_body(content, skill.skill_slug)
            await store.replace_visible(
                craft_id,
                title=content.title,
                keywords=content.keywords,
                detail=content.detail,
                reference_links=content.reference_links,
                body=body,
            )
            await store.complete_claim(
                owner_id, client_id, craft_id, CRAFT_SKILL_SAVE_OPERATION
            )
            return _view(
                skill,
                title=content.title,
                keywords=content.keywords,
                detail=content.detail,
                reference_links=content.reference_links,
            )
        if skill.draft_title is None:
            pending_body = render_skill_body(content, skill.skill_slug)
            await store.write_draft(
                craft_id,
                title=content.title,
                keywords=content.keywords,
                detail=content.detail,
                reference_links=content.reference_links,
                body=pending_body,
            )
            pending = (
                content.title,
                content.keywords,
                content.detail,
                content.reference_links,
                pending_body,
            )
        else:
            pending = (
                skill.draft_title,
                skill.draft_keywords or (),
                skill.draft_detail or "",
                skill.draft_reference_links or (),
                skill.draft_body or "",
            )
        previous = _visible(skill)

    await _stage(index, skill, pending[0], pending[1])
    async with unit.transaction() as store:
        promoted = await store.promote_draft(craft_id)
    if not promoted:
        await _withdraw(index, craft_id)
        raise _error("NOT_FOUND", 404)
    try:
        await index.activate(craft_id=craft_id)
    except MatchIndexError as exc:
        async with unit.transaction() as store:
            await store.restore_visible(craft_id, **previous, **_draft_kwargs(pending))
        await _withdraw(index, craft_id)
        raise _error("DEPENDENCY_UNAVAILABLE", 503, retryable=True) from exc
    async with unit.transaction() as store:
        await store.complete_claim(owner_id, client_id, craft_id, CRAFT_SKILL_SAVE_OPERATION)
    return _view(
        skill,
        title=pending[0],
        keywords=pending[1],
        detail=pending[2],
        reference_links=pending[3],
    )


async def set_craft_visibility(
    unit: EditUnit,
    index: MatchIndex,
    *,
    owner_id: UUID,
    client_id: UUID,
    craft_id: UUID,
    visibility: VisibilityTarget,
) -> CraftSkillView:
    request_hash = craft_visibility_request_hash(craft_id, visibility)
    async with unit.transaction() as store:
        skill = await _owned_skill(store, owner_id, craft_id)
        claim = await _claim(
            store, owner_id, client_id, request_hash, CRAFT_SKILL_VISIBILITY_OPERATION
        )
        if claim.status == "completed":
            return _view(skill)
        previous = skill.visibility

    if visibility == "private":
        await _remove_active(index, craft_id)
        async with unit.transaction() as store:
            await store.set_visibility(craft_id, "private")
            await store.complete_claim(
                owner_id, client_id, craft_id, CRAFT_SKILL_VISIBILITY_OPERATION
            )
        return _view(skill, visibility="private")

    if previous == "public":
        async with unit.transaction() as store:
            await store.complete_claim(
                owner_id, client_id, craft_id, CRAFT_SKILL_VISIBILITY_OPERATION
            )
        return _view(skill)

    await _stage(index, skill, skill.title, skill.keywords)
    async with unit.transaction() as store:
        await store.set_visibility(craft_id, "public")
    try:
        await index.activate(craft_id=craft_id)
    except MatchIndexError as exc:
        async with unit.transaction() as store:
            await store.set_visibility(craft_id, previous)
        await _withdraw(index, craft_id)
        raise _error("DEPENDENCY_UNAVAILABLE", 503, retryable=True) from exc
    async with unit.transaction() as store:
        await store.complete_claim(
            owner_id, client_id, craft_id, CRAFT_SKILL_VISIBILITY_OPERATION
        )
    return _view(skill, visibility="public")


async def delete_craft_skill(
    unit: EditUnit,
    index: MatchIndex,
    *,
    owner_id: UUID,
    client_id: UUID,
    craft_id: UUID,
) -> CraftSkillRemoved:
    request_hash = craft_delete_request_hash(craft_id)
    async with unit.transaction() as store:
        skill = await _skill_row(store, owner_id, craft_id)
        claim = await _claim(
            store, owner_id, client_id, request_hash, CRAFT_SKILL_DELETE_OPERATION
        )
        if claim.status == "completed" or skill.deleted_at is not None:
            if claim.status != "completed":
                await store.complete_claim(
                    owner_id, client_id, craft_id, CRAFT_SKILL_DELETE_OPERATION
                )
            return CraftSkillRemoved(craft_id=craft_id, deleted=True)

    await _remove_active(index, craft_id)
    async with unit.transaction() as store:
        await store.mark_deleted(craft_id)
        await store.complete_claim(
            owner_id, client_id, craft_id, CRAFT_SKILL_DELETE_OPERATION
        )
    return CraftSkillRemoved(craft_id=craft_id, deleted=True)


async def _owned_skill(store: CraftEditStore, owner_id: UUID, craft_id: UUID) -> EditableSkill:
    skill = await _skill_row(store, owner_id, craft_id)
    if skill.deleted_at is not None:
        raise _error("NOT_FOUND", 404)
    return skill


async def _skill_row(store: CraftEditStore, owner_id: UUID, craft_id: UUID) -> EditableSkill:
    await store.lock_owner(owner_id)
    spirit_id = await store.owner_spirit(owner_id)
    if spirit_id is None:
        raise _error("NOT_FOUND", 404)
    skill = await store.get_skill(craft_id, spirit_id)
    if skill is None:
        raise _error("NOT_FOUND", 404)
    return skill


async def _claim(
    store: CraftEditStore,
    owner_id: UUID,
    client_id: UUID,
    request_hash: str,
    operation: str,
) -> StoredClaim:
    claim = await store.claim(owner_id, client_id, request_hash, operation)
    if not claim.inserted and (claim.status == "conflict" or claim.request_hash != request_hash):
        raise _error("IDEMPOTENCY_CONFLICT", 409)
    return claim


def _visible(skill: EditableSkill) -> dict[str, object]:
    return {
        "title": skill.title,
        "keywords": skill.keywords,
        "detail": skill.detail,
        "reference_links": skill.reference_links,
        "body": skill.body,
    }


def _draft_kwargs(
    pending: tuple[str, tuple[str, ...], str, tuple[str, ...], str],
) -> dict[str, object]:
    return {
        "draft_title": pending[0],
        "draft_keywords": pending[1],
        "draft_detail": pending[2],
        "draft_reference_links": pending[3],
        "draft_body": pending[4],
    }


def _view(
    skill: EditableSkill,
    *,
    title: str | None = None,
    keywords: tuple[str, ...] | None = None,
    detail: str | None = None,
    reference_links: tuple[str, ...] | None = None,
    visibility: Literal["unpublished", "public", "private"] | None = None,
) -> CraftSkillView:
    return CraftSkillView(
        craft_id=skill.craft_id,
        title=skill.title if title is None else title,
        keywords=skill.keywords if keywords is None else keywords,
        detail=skill.detail if detail is None else detail,
        reference_links=skill.reference_links if reference_links is None else reference_links,
        visibility=skill.visibility if visibility is None else visibility,
        points=skill.points,
    )


async def _stage(
    index: MatchIndex, skill: EditableSkill, title: str, keywords: tuple[str, ...]
) -> None:
    try:
        await index.stage(
            craft_id=skill.craft_id,
            spirit_id=skill.spirit_id,
            skill_slug=skill.skill_slug,
            title=title,
            keywords=keywords,
        )
    except MatchIndexError as exc:
        raise _error("DEPENDENCY_UNAVAILABLE", 503, retryable=True) from exc


async def _remove_active(index: MatchIndex, craft_id: UUID) -> None:
    try:
        await index.remove_active(craft_id=craft_id)
    except MatchIndexError as exc:
        raise _error("DEPENDENCY_UNAVAILABLE", 503, retryable=True) from exc


async def _withdraw(index: MatchIndex, craft_id: UUID) -> None:
    try:
        await index.withdraw(craft_id=craft_id)
    except MatchIndexError as exc:
        raise _error("DEPENDENCY_UNAVAILABLE", 503, retryable=True) from exc


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
