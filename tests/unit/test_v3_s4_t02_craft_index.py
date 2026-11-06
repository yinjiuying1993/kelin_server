"""V3-S4-T02 craft writes use the real match-index service."""

from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from app.core.errors import ApiError
from app.domain.match_index.rows import MatchRow
from app.repositories import craft as craft_sql
from app.repositories.craft import EditableSkill, StoredClaim, StoredSkill
from app.services.craft.create import create_craft_skill
from app.services.craft.edit import delete_craft_skill, save_craft_skill, set_craft_visibility
from app.services.match_index import owned as owned_index
from app.services.match_index.index import MatchIndexService

_OWNER = UUID("00000000-0000-4000-8000-000000000010")
_CLIENT = UUID("00000000-0000-4000-8000-000000000011")
_OTHER_CLIENT = UUID("00000000-0000-4000-8000-000000000014")
_SPIRIT = UUID("00000000-0000-4000-8000-000000000012")
_DETAIL = "先烧水，再闷三分钟。"
_LINK = "https://tea.example/a"


@dataclass
class _Record:
    craft_id: UUID
    spirit_id: UUID
    skill_slug: str
    title: str
    keywords: tuple[str, ...]
    detail: str
    reference_links: tuple[str, ...]
    body: str
    draft_title: str | None = None
    draft_keywords: tuple[str, ...] | None = None
    draft_detail: str | None = None
    draft_reference_links: tuple[str, ...] | None = None
    draft_body: str | None = None
    visibility: str = "unpublished"
    points: int = 0
    deleted_at: datetime | None = None


@dataclass
class _Claim:
    request_hash: str
    status: str
    resource_id: UUID | None


class _Depth:
    def __init__(self) -> None:
        self.value = 0


class _Rows:
    def __init__(self, depth: _Depth) -> None:
        self.depth = depth
        self.rows: dict[UUID, MatchRow] = {}
        self.active_while_staging: list[str] = []

    async def get(self, craft_id: UUID) -> MatchRow | None:
        assert self.depth.value == 0
        return self.rows.get(craft_id)

    async def save(self, row: MatchRow) -> None:
        assert self.depth.value == 0
        if row.active_title is not None and row.staged_title is not None:
            self.active_while_staging.append(row.active_title)
        self.rows[row.craft_id] = row

    async def list_rows(self) -> list[MatchRow]:
        assert self.depth.value == 0
        return list(self.rows.values())


class _Skills:
    def __init__(self) -> None:
        self.record: _Record | None = None
        self.claims: dict[tuple[UUID, UUID, str], _Claim] = {}

    def stored(self) -> StoredSkill:
        row = self._row()
        return StoredSkill(
            craft_id=row.craft_id,
            spirit_id=row.spirit_id,
            skill_slug=row.skill_slug,
            title=row.title,
            keywords=row.keywords,
            detail=row.detail,
            reference_links=row.reference_links,
            visibility=row.visibility,  # type: ignore[arg-type]
            points=row.points,
        )

    def editable(self) -> EditableSkill:
        row = self._row()
        return EditableSkill(
            craft_id=row.craft_id,
            spirit_id=row.spirit_id,
            skill_slug=row.skill_slug,
            title=row.title,
            keywords=row.keywords,
            detail=row.detail,
            reference_links=row.reference_links,
            body=row.body,
            draft_title=row.draft_title,
            draft_keywords=row.draft_keywords,
            draft_detail=row.draft_detail,
            draft_reference_links=row.draft_reference_links,
            draft_body=row.draft_body,
            visibility=row.visibility,  # type: ignore[arg-type]
            points=row.points,
            deleted_at=row.deleted_at,
        )

    def _row(self) -> _Record:
        if self.record is None:
            raise AssertionError("missing skill")
        return self.record


class _CreateStore:
    def __init__(self, skills: _Skills) -> None:
        self.skills = skills

    async def lock_owner(self, owner_id: UUID) -> None:
        del owner_id

    async def owner_spirit(self, owner_id: UUID) -> UUID | None:
        del owner_id
        return _SPIRIT

    async def claim(self, owner_id: UUID, client_id: UUID, request_hash: str) -> StoredClaim:
        return _claim(self.skills, owner_id, client_id, request_hash, "craft.skill.create")

    async def taken_slugs(self, spirit_id: UUID) -> set[str]:
        row = self.skills.record
        if row is None or row.spirit_id != spirit_id:
            return set()
        return {row.skill_slug}

    async def insert_unpublished(self, **fields: object) -> StoredSkill:
        self.skills.record = _Record(
            craft_id=uuid4(),
            spirit_id=fields["spirit_id"],  # type: ignore[arg-type]
            skill_slug=str(fields["skill_slug"]),
            title=str(fields["title"]),
            keywords=fields["keywords"],  # type: ignore[arg-type]
            detail=str(fields["detail"]),
            reference_links=fields["reference_links"],  # type: ignore[arg-type]
            body=str(fields["body"]),
        )
        return self.skills.stored()

    async def get_skill(self, craft_id: UUID) -> StoredSkill | None:
        row = self.skills.record
        if row is None or row.craft_id != craft_id:
            return None
        return self.skills.stored()

    async def set_visibility(self, craft_id: UUID, visibility: str) -> None:
        del craft_id
        self.skills._row().visibility = visibility

    async def bind_claim(self, owner_id: UUID, client_id: UUID, craft_id: UUID) -> None:
        _claim_row(self.skills, owner_id, client_id, "craft.skill.create").resource_id = craft_id

    async def complete_claim(self, owner_id: UUID, client_id: UUID, craft_id: UUID) -> None:
        row = _claim_row(self.skills, owner_id, client_id, "craft.skill.create")
        row.status = "completed"
        row.resource_id = craft_id


class _EditStore:
    def __init__(self, skills: _Skills) -> None:
        self.skills = skills

    async def lock_owner(self, owner_id: UUID) -> None:
        del owner_id

    async def owner_spirit(self, owner_id: UUID) -> UUID | None:
        del owner_id
        return _SPIRIT

    async def claim(
        self, owner_id: UUID, client_id: UUID, request_hash: str, operation: str
    ) -> StoredClaim:
        return _claim(self.skills, owner_id, client_id, request_hash, operation)

    async def complete_claim(
        self, owner_id: UUID, client_id: UUID, craft_id: UUID, operation: str
    ) -> None:
        row = _claim_row(self.skills, owner_id, client_id, operation)
        row.status = "completed"
        row.resource_id = craft_id

    async def get_skill(self, craft_id: UUID, spirit_id: UUID) -> EditableSkill | None:
        row = self.skills.record
        if row is None or row.craft_id != craft_id or row.spirit_id != spirit_id:
            return None
        return self.skills.editable()

    async def write_draft(self, craft_id: UUID, **fields: object) -> None:
        del craft_id
        row = self.skills._row()
        row.draft_title = str(fields["title"])
        row.draft_keywords = fields["keywords"]  # type: ignore[assignment]
        row.draft_detail = str(fields["detail"])
        row.draft_reference_links = fields["reference_links"]  # type: ignore[assignment]
        row.draft_body = str(fields["body"])

    async def promote_draft(self, craft_id: UUID) -> bool:
        del craft_id
        row = self.skills._row()
        if row.deleted_at is not None or row.visibility != "public" or row.draft_title is None:
            return False
        row.title = row.draft_title
        row.keywords = row.draft_keywords or ()
        row.detail = row.draft_detail or ""
        row.reference_links = row.draft_reference_links or ()
        row.body = row.draft_body or ""
        row.draft_title = None
        row.draft_keywords = None
        row.draft_detail = None
        row.draft_reference_links = None
        row.draft_body = None
        return True

    async def restore_visible(self, craft_id: UUID, **fields: object) -> None:
        del craft_id
        updated = replace(self.skills.editable(), **fields)  # type: ignore[arg-type]
        self.skills.record = _Record(**updated.__dict__)

    async def replace_visible(self, craft_id: UUID, **fields: object) -> None:
        del craft_id
        row = self.skills._row()
        row.title = str(fields["title"])
        row.keywords = fields["keywords"]  # type: ignore[assignment]
        row.detail = str(fields["detail"])
        row.reference_links = fields["reference_links"]  # type: ignore[assignment]
        row.body = str(fields["body"])
        row.draft_title = None
        row.draft_keywords = None
        row.draft_detail = None
        row.draft_reference_links = None
        row.draft_body = None

    async def set_visibility(self, craft_id: UUID, visibility: str) -> None:
        del craft_id
        self.skills._row().visibility = visibility

    async def mark_deleted(self, craft_id: UUID) -> None:
        del craft_id
        self.skills._row().deleted_at = datetime.now(UTC)


class _Unit:
    def __init__(self, store: object, depth: _Depth) -> None:
        self.store = store
        self.depth = depth

    @asynccontextmanager
    async def transaction(self):  # type: ignore[no-untyped-def]
        self.depth.value += 1
        try:
            yield self.store
        finally:
            self.depth.value -= 1


def _claim(
    skills: _Skills, owner_id: UUID, client_id: UUID, request_hash: str, operation: str
) -> StoredClaim:
    key = (owner_id, client_id, operation)
    row = skills.claims.get(key)
    if row is None:
        skills.claims[key] = _Claim(request_hash, "in_progress", None)
        return StoredClaim(True, request_hash, "in_progress", None)
    return StoredClaim(False, row.request_hash, row.status, row.resource_id)


def _claim_row(skills: _Skills, owner_id: UUID, client_id: UUID, operation: str) -> _Claim:
    return skills.claims[(owner_id, client_id, operation)]


def _section(body: str, heading: str) -> str:
    return body.split(f"## {heading}", maxsplit=1)[1].split("##", maxsplit=1)[0]


@pytest.mark.asyncio
async def test_success_keeps_the_visible_body_and_the_active_index_together() -> None:
    depth = _Depth()
    skills = _Skills()
    rows = _Rows(depth)
    index = MatchIndexService(rows)
    create_unit = _Unit(_CreateStore(skills), depth)
    edit_unit = _Unit(_EditStore(skills), depth)

    created = await create_craft_skill(
        create_unit,
        index,
        owner_id=_OWNER,
        client_id=_CLIENT,
        title="泡茶",
        keywords=["水温"],
        detail=_DETAIL,
        reference_links=[_LINK],
        moderation="allow",
        rng=lambda: "pour-tea",
    )
    record = skills._row()
    visible = await index.materials_for_questions()
    assert created.visibility == "public"
    assert "skill_slug" not in created.__dataclass_fields__
    assert visible[0].title == created.title == record.title == "泡茶"
    assert visible[0].keywords == created.keywords == record.keywords == ("水温",)
    assert visible[0].skill_slug == record.skill_slug
    assert "泡茶。遇到水温相关的问题时使用。" in record.body
    assert "水温" in _section(record.body, "关键词")
    assert _DETAIL in _section(record.body, "详细描述")
    assert _LINK in _section(record.body, "参考链接")
    assert _LINK not in visible[0].keywords

    record.points = 12
    saved = await save_craft_skill(
        edit_unit,
        index,
        owner_id=_OWNER,
        client_id=_CLIENT,
        craft_id=created.craft_id,
        title="煎茶",
        keywords=["火候"],
        detail="小火慢煎，不要煮开。",
        reference_links=["https://tea.example/b"],
        moderation="allow",
    )
    record = skills._row()
    visible = await index.materials_for_questions()
    assert saved.craft_id == created.craft_id
    assert saved.points == 12
    assert record.skill_slug == "pour-tea"
    assert "pour-tea" not in str(saved)
    assert rows.active_while_staging == ["泡茶"]
    assert visible[0].title == saved.title == record.title == "煎茶"
    assert visible[0].keywords == saved.keywords == record.keywords == ("火候",)
    assert "火候" in _section(record.body, "关键词")
    assert len(rows.rows) == 1
    assert rows.rows[created.craft_id].staged_title is None

    hidden = await set_craft_visibility(
        edit_unit,
        index,
        owner_id=_OWNER,
        client_id=_CLIENT,
        craft_id=created.craft_id,
        visibility="private",
    )
    assert hidden.visibility == "private"
    assert await index.materials_for_questions() == []

    private_save = await save_craft_skill(
        edit_unit,
        index,
        owner_id=_OWNER,
        client_id=_OTHER_CLIENT,
        craft_id=created.craft_id,
        title="冷泡",
        keywords=["冷水"],
        detail="冷水浸泡四个小时。",
        moderation="allow",
    )
    record = skills._row()
    assert private_save.title == record.title == "冷泡"
    assert record.skill_slug == "pour-tea"
    assert await index.materials_for_questions() == []

    shown = await set_craft_visibility(
        edit_unit,
        index,
        owner_id=_OWNER,
        client_id=_OTHER_CLIENT,
        craft_id=created.craft_id,
        visibility="public",
    )
    visible = await index.materials_for_questions()
    assert shown.visibility == "public"
    assert visible[0].title == record.title == "冷泡"
    assert visible[0].keywords == record.keywords == ("冷水",)
    assert visible[0].skill_slug == "pour-tea"

    removed = await delete_craft_skill(
        edit_unit,
        index,
        owner_id=_OWNER,
        client_id=_CLIENT,
        craft_id=created.craft_id,
    )
    assert removed.deleted is True
    assert skills._row().deleted_at is not None
    assert await index.materials_for_questions() == []

    with pytest.raises(ApiError) as caught:
        await create_craft_skill(
            create_unit,
            index,
            owner_id=_OWNER,
            client_id=uuid4(),
            title="再泡",
            keywords=["新叶"],
            detail="换一壶水，再闷三分钟。",
            moderation="allow",
            rng=lambda: "pour-tea",
        )
    assert caught.value.code == "INTERNAL_ERROR"
    assert "pour-tea" not in str(caught.value)
    assert await index.materials_for_questions() == []
    assert len(rows.rows) == 1


def test_skill_repository_does_not_write_the_match_index() -> None:
    source = Path(craft_sql.__file__).read_text(encoding="utf-8")
    assert "craft_match_index" not in source
    owned = inspect.getsource(owned_index)
    assert "MatchIndexService" in owned
    assert "SqlMatchIndexStore" in owned
