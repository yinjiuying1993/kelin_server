"""Craft skill rows. Create writes an unpublished skill and does not touch the index."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import Text, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause

from app.domain.craft.create import CRAFT_SKILL_CREATE_OPERATION, SkillSlugTaken
from app.repositories.spirit import fetch_spirit_for_owner

CRAFT_CREATE_LOCK_NS = 6103202
SKILL_SLUG_UNIQUE = "uq_craft_skills_skill_slug"
Visibility = Literal["unpublished", "public"]


@dataclass(frozen=True)
class StoredClaim:
    inserted: bool
    request_hash: str
    status: str
    resource_id: uuid.UUID | None


@dataclass(frozen=True)
class StoredSkill:
    craft_id: uuid.UUID
    spirit_id: uuid.UUID
    skill_slug: str
    title: str
    keywords: tuple[str, ...]
    detail: str
    reference_links: tuple[str, ...]
    visibility: Visibility
    points: int


class SqlCraftCreateStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_owner(self, owner_id: uuid.UUID) -> None:
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:ns, hashtext(:uid))"),
            {"ns": CRAFT_CREATE_LOCK_NS, "uid": str(owner_id)},
        )

    async def owner_spirit(self, owner_id: uuid.UUID) -> uuid.UUID | None:
        row = await fetch_spirit_for_owner(self._session, owner_id)
        if row is None:
            return None
        return row.id

    async def claim(
        self, owner_id: uuid.UUID, client_id: uuid.UUID, request_hash: str
    ) -> StoredClaim:
        params = {
            "user_id": owner_id,
            "operation": CRAFT_SKILL_CREATE_OPERATION,
            "client_id": client_id,
            "request_hash": request_hash,
        }
        inserted = (
            await self._session.execute(
                text(
                    "INSERT INTO public.idempotency_records ("
                    "user_id, operation, client_id, request_hash, status"
                    ") VALUES ("
                    ":user_id, :operation, :client_id, :request_hash, 'in_progress'"
                    ") ON CONFLICT (user_id, operation, client_id) DO NOTHING "
                    "RETURNING request_hash, status, resource_id"
                ),
                params,
            )
        ).first()
        if inserted is not None:
            return StoredClaim(True, str(inserted.request_hash), str(inserted.status), None)
        existing = (
            await self._session.execute(
                text(
                    "SELECT request_hash, status, resource_id "
                    "FROM public.idempotency_records "
                    "WHERE user_id = :user_id AND operation = :operation "
                    "AND client_id = :client_id "
                    "FOR UPDATE"
                ),
                params,
            )
        ).first()
        if existing is None:
            raise RuntimeError("idempotency claim failed")
        return StoredClaim(
            False,
            str(existing.request_hash),
            str(existing.status),
            existing.resource_id,
        )

    async def taken_slugs(self, spirit_id: uuid.UUID) -> set[str]:
        rows = (
            await self._session.execute(
                text("SELECT skill_slug FROM public.craft_skills WHERE spirit_id = :spirit_id"),
                {"spirit_id": spirit_id},
            )
        ).all()
        return {str(row.skill_slug) for row in rows}

    async def insert_unpublished(
        self,
        *,
        spirit_id: uuid.UUID,
        skill_slug: str,
        title: str,
        keywords: tuple[str, ...],
        detail: str,
        reference_links: tuple[str, ...],
        body: str,
    ) -> StoredSkill:
        try:
            row = (
                await self._session.execute(
                    text(
                        "INSERT INTO public.craft_skills ("
                        "spirit_id, skill_slug, title, keywords, detail, "
                        "reference_links, body, visibility, points"
                        ") VALUES ("
                        ":spirit_id, :skill_slug, :title, :keywords, :detail, "
                        ":reference_links, :body, 'unpublished', 0"
                        ") RETURNING id"
                    ).bindparams(
                        bindparam("keywords", type_=ARRAY(Text())),
                        bindparam("reference_links", type_=ARRAY(Text())),
                    ),
                    {
                        "spirit_id": spirit_id,
                        "skill_slug": skill_slug,
                        "title": title,
                        "keywords": list(keywords),
                        "detail": detail,
                        "reference_links": list(reference_links),
                        "body": body,
                    },
                )
            ).one()
        except IntegrityError as exc:
            if SKILL_SLUG_UNIQUE in str(exc):
                raise SkillSlugTaken from exc
            raise
        return StoredSkill(
            craft_id=row.id,
            spirit_id=spirit_id,
            skill_slug=skill_slug,
            title=title,
            keywords=keywords,
            detail=detail,
            reference_links=reference_links,
            visibility="unpublished",
            points=0,
        )

    async def get_skill(self, craft_id: uuid.UUID) -> StoredSkill | None:
        row = (
            await self._session.execute(
                text(
                    "SELECT id, spirit_id, skill_slug, title, keywords, detail, "
                    "reference_links, visibility, points "
                    "FROM public.craft_skills WHERE id = :id"
                ),
                {"id": craft_id},
            )
        ).first()
        if row is None:
            return None
        return StoredSkill(
            craft_id=row.id,
            spirit_id=row.spirit_id,
            skill_slug=str(row.skill_slug),
            title=str(row.title),
            keywords=tuple(row.keywords),
            detail=str(row.detail),
            reference_links=tuple(row.reference_links),
            visibility=row.visibility,
            points=int(row.points),
        )

    async def set_visibility(self, craft_id: uuid.UUID, visibility: Visibility) -> None:
        await self._session.execute(
            text(
                "UPDATE public.craft_skills "
                "SET visibility = :visibility, updated_at = now() "
                "WHERE id = :id AND deleted_at IS NULL"
            ),
            {"id": craft_id, "visibility": visibility},
        )

    async def bind_claim(
        self, owner_id: uuid.UUID, client_id: uuid.UUID, craft_id: uuid.UUID
    ) -> None:
        await self._session.execute(
            text(
                "UPDATE public.idempotency_records "
                "SET resource_type = 'craft_skill', resource_id = :resource_id "
                "WHERE user_id = :user_id AND operation = :operation "
                "AND client_id = :client_id AND status = 'in_progress'"
            ),
            _claim_params(owner_id, client_id, craft_id),
        )

    async def complete_claim(
        self, owner_id: uuid.UUID, client_id: uuid.UUID, craft_id: uuid.UUID
    ) -> None:
        await self._session.execute(
            text(
                "UPDATE public.idempotency_records "
                "SET status = 'completed', resource_type = 'craft_skill', "
                "resource_id = :resource_id, completed_at = now() "
                "WHERE user_id = :user_id AND operation = :operation "
                "AND client_id = :client_id"
            ),
            _claim_params(owner_id, client_id, craft_id),
        )


def _claim_params(
    owner_id: uuid.UUID, client_id: uuid.UUID, craft_id: uuid.UUID
) -> dict[str, uuid.UUID | str]:
    return {
        "user_id": owner_id,
        "operation": CRAFT_SKILL_CREATE_OPERATION,
        "client_id": client_id,
        "resource_id": craft_id,
    }


EditVisibility = Literal["unpublished", "public", "private"]

MARK_DELETED_SQL = (
    "UPDATE public.craft_skills "
    "SET deleted_at = now(), updated_at = now() "
    "WHERE id = :id AND deleted_at IS NULL"
)

PROMOTE_DRAFT_SQL = (
    "UPDATE public.craft_skills SET "
    "title = draft_title, keywords = draft_keywords, detail = draft_detail, "
    "reference_links = draft_reference_links, body = draft_body, "
    "draft_title = NULL, draft_keywords = NULL, draft_detail = NULL, "
    "draft_reference_links = NULL, draft_body = NULL, "
    "content_updated_at = now(), updated_at = now() "
    "WHERE id = :id AND deleted_at IS NULL AND visibility = 'public' "
    "AND draft_title IS NOT NULL "
    "RETURNING id"
)


@dataclass(frozen=True)
class EditableSkill:
    craft_id: uuid.UUID
    spirit_id: uuid.UUID
    skill_slug: str
    title: str
    keywords: tuple[str, ...]
    detail: str
    reference_links: tuple[str, ...]
    body: str
    draft_title: str | None
    draft_keywords: tuple[str, ...] | None
    draft_detail: str | None
    draft_reference_links: tuple[str, ...] | None
    draft_body: str | None
    visibility: EditVisibility
    points: int
    deleted_at: datetime | None


class SqlCraftEditStore:
    """Skill-row edits. Ask snapshots, ledgers, and the match index are not updated here."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_owner(self, owner_id: uuid.UUID) -> None:
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:ns, hashtext(:uid))"),
            {"ns": CRAFT_CREATE_LOCK_NS, "uid": str(owner_id)},
        )

    async def owner_spirit(self, owner_id: uuid.UUID) -> uuid.UUID | None:
        row = await fetch_spirit_for_owner(self._session, owner_id)
        if row is None:
            return None
        return row.id

    async def claim(
        self,
        owner_id: uuid.UUID,
        client_id: uuid.UUID,
        request_hash: str,
        operation: str,
    ) -> StoredClaim:
        params = {
            "user_id": owner_id,
            "operation": operation,
            "client_id": client_id,
            "request_hash": request_hash,
        }
        inserted = (
            await self._session.execute(
                text(
                    "INSERT INTO public.idempotency_records ("
                    "user_id, operation, client_id, request_hash, status"
                    ") VALUES ("
                    ":user_id, :operation, :client_id, :request_hash, 'in_progress'"
                    ") ON CONFLICT (user_id, operation, client_id) DO NOTHING "
                    "RETURNING request_hash, status, resource_id"
                ),
                params,
            )
        ).first()
        if inserted is not None:
            return StoredClaim(True, str(inserted.request_hash), str(inserted.status), None)
        existing = (
            await self._session.execute(
                text(
                    "SELECT request_hash, status, resource_id "
                    "FROM public.idempotency_records "
                    "WHERE user_id = :user_id AND operation = :operation "
                    "AND client_id = :client_id FOR UPDATE"
                ),
                params,
            )
        ).first()
        if existing is None:
            raise RuntimeError("idempotency claim failed")
        return StoredClaim(
            False, str(existing.request_hash), str(existing.status), existing.resource_id
        )

    async def complete_claim(
        self, owner_id: uuid.UUID, client_id: uuid.UUID, craft_id: uuid.UUID, operation: str
    ) -> None:
        await self._session.execute(
            text(
                "UPDATE public.idempotency_records "
                "SET status = 'completed', resource_type = 'craft_skill', "
                "resource_id = :resource_id, completed_at = now() "
                "WHERE user_id = :user_id AND operation = :operation AND client_id = :client_id"
            ),
            {
                "user_id": owner_id,
                "operation": operation,
                "client_id": client_id,
                "resource_id": craft_id,
            },
        )

    async def get_skill(self, craft_id: uuid.UUID, spirit_id: uuid.UUID) -> EditableSkill | None:
        row = (
            await self._session.execute(
                text(
                    "SELECT id, spirit_id, skill_slug, title, keywords, detail, "
                    "reference_links, body, draft_title, draft_keywords, draft_detail, "
                    "draft_reference_links, draft_body, visibility, points, deleted_at "
                    "FROM public.craft_skills "
                    "WHERE id = :id AND spirit_id = :spirit_id"
                ),
                {"id": craft_id, "spirit_id": spirit_id},
            )
        ).first()
        if row is None:
            return None
        return _editable(row)

    async def write_draft(
        self,
        craft_id: uuid.UUID,
        *,
        title: str,
        keywords: tuple[str, ...],
        detail: str,
        reference_links: tuple[str, ...],
        body: str,
    ) -> None:
        await self._session.execute(
            _arrays(
                "UPDATE public.craft_skills SET "
                "draft_title = :title, draft_keywords = :keywords, draft_detail = :detail, "
                "draft_reference_links = :reference_links, draft_body = :body, "
                "updated_at = now() "
                "WHERE id = :id AND deleted_at IS NULL AND visibility = 'public'",
                "keywords",
                "reference_links",
            ),
            {
                "id": craft_id,
                "title": title,
                "keywords": list(keywords),
                "detail": detail,
                "reference_links": list(reference_links),
                "body": body,
            },
        )

    async def promote_draft(self, craft_id: uuid.UUID) -> bool:
        row = (await self._session.execute(text(PROMOTE_DRAFT_SQL), {"id": craft_id})).first()
        return row is not None

    async def restore_visible(
        self,
        craft_id: uuid.UUID,
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
    ) -> None:
        await self._session.execute(
            _arrays(
                "UPDATE public.craft_skills SET "
                "title = :title, keywords = :keywords, detail = :detail, "
                "reference_links = :reference_links, body = :body, "
                "draft_title = :draft_title, draft_keywords = :draft_keywords, "
                "draft_detail = :draft_detail, "
                "draft_reference_links = :draft_reference_links, draft_body = :draft_body, "
                "updated_at = now() "
                "WHERE id = :id AND deleted_at IS NULL",
                "keywords",
                "reference_links",
                "draft_keywords",
                "draft_reference_links",
            ),
            {
                "id": craft_id,
                "title": title,
                "keywords": list(keywords),
                "detail": detail,
                "reference_links": list(reference_links),
                "body": body,
                "draft_title": draft_title,
                "draft_keywords": list(draft_keywords),
                "draft_detail": draft_detail,
                "draft_reference_links": list(draft_reference_links),
                "draft_body": draft_body,
            },
        )

    async def replace_visible(
        self,
        craft_id: uuid.UUID,
        *,
        title: str,
        keywords: tuple[str, ...],
        detail: str,
        reference_links: tuple[str, ...],
        body: str,
    ) -> None:
        await self._session.execute(
            _arrays(
                "UPDATE public.craft_skills SET "
                "title = :title, keywords = :keywords, detail = :detail, "
                "reference_links = :reference_links, body = :body, "
                "draft_title = NULL, draft_keywords = NULL, draft_detail = NULL, "
                "draft_reference_links = NULL, draft_body = NULL, "
                "content_updated_at = now(), updated_at = now() "
                "WHERE id = :id AND deleted_at IS NULL AND visibility <> 'public'",
                "keywords",
                "reference_links",
            ),
            {
                "id": craft_id,
                "title": title,
                "keywords": list(keywords),
                "detail": detail,
                "reference_links": list(reference_links),
                "body": body,
            },
        )

    async def set_visibility(self, craft_id: uuid.UUID, visibility: EditVisibility) -> None:
        await self._session.execute(
            text(
                "UPDATE public.craft_skills SET visibility = :visibility, updated_at = now() "
                "WHERE id = :id AND deleted_at IS NULL"
            ),
            {"id": craft_id, "visibility": visibility},
        )

    async def mark_deleted(self, craft_id: uuid.UUID) -> None:
        await self._session.execute(text(MARK_DELETED_SQL), {"id": craft_id})


def _arrays(sql: str, *names: str) -> TextClause:
    statement = text(sql)
    if not names:
        return statement
    return statement.bindparams(*(bindparam(name, type_=ARRAY(Text())) for name in names))


def _editable(row: object) -> EditableSkill:
    draft_keywords = getattr(row, "draft_keywords", None)
    draft_links = getattr(row, "draft_reference_links", None)
    return EditableSkill(
        craft_id=row.id,  # type: ignore[attr-defined]
        spirit_id=row.spirit_id,  # type: ignore[attr-defined]
        skill_slug=str(row.skill_slug),  # type: ignore[attr-defined]
        title=str(row.title),  # type: ignore[attr-defined]
        keywords=tuple(row.keywords),  # type: ignore[attr-defined]
        detail=str(row.detail),  # type: ignore[attr-defined]
        reference_links=tuple(row.reference_links),  # type: ignore[attr-defined]
        body=str(row.body),  # type: ignore[attr-defined]
        draft_title=row.draft_title,  # type: ignore[attr-defined]
        draft_keywords=None if draft_keywords is None else tuple(draft_keywords),
        draft_detail=row.draft_detail,  # type: ignore[attr-defined]
        draft_reference_links=None if draft_links is None else tuple(draft_links),
        draft_body=row.draft_body,  # type: ignore[attr-defined]
        visibility=row.visibility,  # type: ignore[attr-defined]
        points=int(row.points),  # type: ignore[attr-defined]
        deleted_at=row.deleted_at,  # type: ignore[attr-defined]
    )


LIST_OWNER_SKILLS_SQL = (
    "SELECT id, title, visibility, points, content_updated_at "
    "FROM public.craft_skills "
    "WHERE spirit_id = :spirit_id AND deleted_at IS NULL "
    "AND content_updated_at <= :snapshot_at "
    "AND ("
    ":has_cursor = false "
    "OR content_updated_at < :cursor_time "
    "OR (content_updated_at = :cursor_time AND id < :cursor_id)"
    ") "
    "ORDER BY content_updated_at DESC, id DESC "
    "LIMIT :fetch_limit"
)

LEDGER_TOTAL_SQL = (
    "SELECT total_points FROM public.craft_ledgers WHERE spirit_id = :spirit_id"
)

OWNER_SKILL_DETAIL_SQL = (
    "SELECT id, title, visibility, points, keywords, detail, reference_links, "
    "content_updated_at "
    "FROM public.craft_skills "
    "WHERE id = :id AND spirit_id = :spirit_id AND deleted_at IS NULL"
)


@dataclass(frozen=True)
class OwnerSkillRow:
    craft_id: uuid.UUID
    title: str
    visibility: str
    points: int
    updated_at: datetime


@dataclass(frozen=True)
class OwnerSkillDetail(OwnerSkillRow):
    keywords: tuple[str, ...]
    detail: str
    reference_links: tuple[str, ...]


class SqlCraftReadStore:
    """Owner reads. The page total comes from the ledger, not a sum of skill points."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def transaction_now(self) -> datetime:
        value = await self._session.scalar(text("SELECT now()"))
        if not isinstance(value, datetime):
            raise RuntimeError("database clock did not return a timestamp")
        return value

    async def owner_spirit(self, owner_id: uuid.UUID) -> uuid.UUID | None:
        row = await fetch_spirit_for_owner(self._session, owner_id)
        if row is None:
            return None
        return row.id

    async def ledger_total(self, spirit_id: uuid.UUID) -> int:
        value = await self._session.scalar(
            text(LEDGER_TOTAL_SQL),
            {"spirit_id": spirit_id},
        )
        if value is None:
            return 0
        return int(value)

    async def list_skills(
        self,
        spirit_id: uuid.UUID,
        *,
        snapshot_at: datetime,
        fetch_limit: int,
        cursor_time: datetime | None,
        cursor_id: uuid.UUID | None,
    ) -> list[OwnerSkillRow]:
        rows = (
            await self._session.execute(
                text(LIST_OWNER_SKILLS_SQL),
                {
                    "spirit_id": spirit_id,
                    "snapshot_at": snapshot_at,
                    "fetch_limit": fetch_limit,
                    "has_cursor": cursor_time is not None,
                    "cursor_time": snapshot_at if cursor_time is None else cursor_time,
                    "cursor_id": spirit_id if cursor_id is None else cursor_id,
                },
            )
        ).all()
        return [
            OwnerSkillRow(
                craft_id=row.id,
                title=str(row.title),
                visibility=str(row.visibility),
                points=int(row.points),
                updated_at=row.content_updated_at,
            )
            for row in rows
        ]

    async def get_skill(
        self, craft_id: uuid.UUID, spirit_id: uuid.UUID
    ) -> OwnerSkillDetail | None:
        row = (
            await self._session.execute(
                text(OWNER_SKILL_DETAIL_SQL),
                {"id": craft_id, "spirit_id": spirit_id},
            )
        ).first()
        if row is None:
            return None
        return OwnerSkillDetail(
            craft_id=row.id,
            title=str(row.title),
            visibility=str(row.visibility),
            points=int(row.points),
            updated_at=row.content_updated_at,
            keywords=tuple(row.keywords),
            detail=str(row.detail),
            reference_links=tuple(row.reference_links),
        )
