"""Save, hide, republish, and delete keep the slug, the points, and ask snapshots."""

from __future__ import annotations

import copy
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from app.core.errors import ApiError
from app.db.rls_matrix import V3_WRITE_IDEMPOTENCY
from app.domain.craft.edit import (
    CRAFT_SKILL_DELETE_OPERATION,
    CRAFT_SKILL_SAVE_OPERATION,
    CRAFT_SKILL_VISIBILITY_OPERATION,
)
from app.domain.match_index import MatchIndexError
from app.repositories.craft import MARK_DELETED_SQL, EditableSkill, StoredClaim
from app.services.craft.edit import (
    CraftSkillView,
    delete_craft_skill,
    save_craft_skill,
    set_craft_visibility,
)

_OWNER = UUID("00000000-0000-4000-8000-000000000010")
_CLIENT = UUID("00000000-0000-4000-8000-000000000011")
_SPIRIT = UUID("00000000-0000-4000-8000-000000000012")
_CRAFT = UUID("00000000-0000-4000-8000-000000000013")
_SERVER = Path(__file__).parents[2]
_ASK_SNAPSHOT = {"snapshot_body": "开聊时的正文", "skill_slug": "pour-tea"}


@dataclass
class _Claim:
    request_hash: str
    status: str
    resource_id: UUID | None


def _row(**overrides: object) -> EditableSkill:
    skill = EditableSkill(
        craft_id=_CRAFT,
        spirit_id=_SPIRIT,
        skill_slug="pour-tea",
        title="泡茶",
        keywords=("水温",),
        detail="先烧水，再闷三分钟。",
        reference_links=(),
        body="old-body",
        draft_title=None,
        draft_keywords=None,
        draft_detail=None,
        draft_reference_links=None,
        draft_body=None,
        visibility="public",
        points=12,
        deleted_at=None,
    )
    return replace(skill, **overrides)  # type: ignore[arg-type]


class _Store:
    def __init__(self, skill: EditableSkill) -> None:
        self.spirit_id: UUID | None = _SPIRIT
        self.skill = skill
        self.claims: dict[tuple[UUID, UUID, str], _Claim] = {}
        self.locks = 0
        self.fail: str | None = None
        self.ask_snapshot = dict(_ASK_SNAPSHOT)

    def snapshot(self) -> object:
        return copy.deepcopy((self.skill, self.claims, self.locks, self.ask_snapshot))

    def restore(self, snapshot: object) -> None:
        self.skill, self.claims, self.locks, self.ask_snapshot = snapshot  # type: ignore[misc]

    async def lock_owner(self, owner_id: UUID) -> None:
        del owner_id
        self.locks += 1

    async def owner_spirit(self, owner_id: UUID) -> UUID | None:
        del owner_id
        return self.spirit_id

    async def claim(
        self, owner_id: UUID, client_id: UUID, request_hash: str, operation: str
    ) -> StoredClaim:
        key = (owner_id, client_id, operation)
        row = self.claims.get(key)
        if row is None:
            self.claims[key] = _Claim(request_hash, "in_progress", None)
            return StoredClaim(True, request_hash, "in_progress", None)
        return StoredClaim(False, row.request_hash, row.status, row.resource_id)

    async def complete_claim(
        self, owner_id: UUID, client_id: UUID, craft_id: UUID, operation: str
    ) -> None:
        row = self.claims[(owner_id, client_id, operation)]
        row.status = "completed"
        row.resource_id = craft_id

    async def get_skill(self, craft_id: UUID, spirit_id: UUID) -> EditableSkill | None:
        if self.skill.craft_id != craft_id or self.skill.spirit_id != spirit_id:
            return None
        return self.skill

    async def write_draft(self, craft_id: UUID, **fields: object) -> None:
        del craft_id
        self.skill = replace(
            self.skill,
            draft_title=fields["title"],  # type: ignore[arg-type]
            draft_keywords=fields["keywords"],  # type: ignore[arg-type]
            draft_detail=fields["detail"],  # type: ignore[arg-type]
            draft_reference_links=fields["reference_links"],  # type: ignore[arg-type]
            draft_body=fields["body"],  # type: ignore[arg-type]
        )

    async def promote_draft(self, craft_id: UUID) -> bool:
        del craft_id
        skill = self.skill
        if (
            skill.deleted_at is not None
            or skill.visibility != "public"
            or skill.draft_title is None
            or skill.draft_keywords is None
        ):
            return False
        self.skill = replace(
            skill,
            title=skill.draft_title,
            keywords=skill.draft_keywords,
            detail=skill.draft_detail or "",
            reference_links=skill.draft_reference_links or (),
            body=skill.draft_body or "",
            draft_title=None,
            draft_keywords=None,
            draft_detail=None,
            draft_reference_links=None,
            draft_body=None,
        )
        return True

    async def restore_visible(self, craft_id: UUID, **fields: object) -> None:
        del craft_id
        self.skill = replace(self.skill, **fields)  # type: ignore[arg-type]

    async def replace_visible(self, craft_id: UUID, **fields: object) -> None:
        del craft_id
        self.skill = replace(
            self.skill,
            title=fields["title"],  # type: ignore[arg-type]
            keywords=fields["keywords"],  # type: ignore[arg-type]
            detail=fields["detail"],  # type: ignore[arg-type]
            reference_links=fields["reference_links"],  # type: ignore[arg-type]
            body=fields["body"],  # type: ignore[arg-type]
            draft_title=None,
            draft_keywords=None,
            draft_detail=None,
            draft_reference_links=None,
            draft_body=None,
        )

    async def set_visibility(self, craft_id: UUID, visibility: str) -> None:
        del craft_id
        if self.fail == visibility:
            self.fail = None
            raise RuntimeError("visibility failed")
        self.skill = replace(self.skill, visibility=visibility)  # type: ignore[arg-type]

    async def mark_deleted(self, craft_id: UUID) -> None:
        del craft_id
        if self.fail == "delete":
            self.fail = None
            raise RuntimeError("delete failed")
        self.skill = replace(self.skill, deleted_at=datetime.now(UTC))


class _Unit:
    def __init__(self, store: _Store) -> None:
        self.store = store
        self.depth = 0

    @asynccontextmanager
    async def transaction(self):  # type: ignore[no-untyped-def]
        self.depth += 1
        snapshot = self.store.snapshot()
        try:
            yield self.store
        except Exception:
            self.store.restore(snapshot)
            raise
        finally:
            self.depth -= 1


class _Index:
    def __init__(self, unit: _Unit) -> None:
        self.unit = unit
        self.fail: str | None = None
        self.calls: list[str] = []
        self.staged: dict[UUID, dict[str, object]] = {}
        self.active: dict[UUID, dict[str, object]] = {
            _CRAFT: {"title": "泡茶", "keywords": ("水温",)}
        }
        self.active_at_stage: list[object] = []

    def _outside(self) -> None:
        assert self.unit.depth == 0

    async def stage(self, **kwargs: object) -> None:
        self._outside()
        assert {"body", "detail", "reference_links", "description"}.isdisjoint(kwargs)
        self.calls.append("stage")
        self.active_at_stage.append(
            None if _CRAFT not in self.active else self.active[_CRAFT]["title"]
        )
        if self.fail == "stage":
            raise MatchIndexError
        self.staged[kwargs["craft_id"]] = kwargs  # type: ignore[index]

    async def activate(self, *, craft_id: UUID) -> None:
        self._outside()
        self.calls.append("activate")
        if self.fail == "activate":
            raise MatchIndexError
        self.active[craft_id] = self.staged.pop(craft_id)

    async def withdraw(self, *, craft_id: UUID) -> None:
        self._outside()
        self.calls.append("withdraw")
        self.staged.pop(craft_id, None)

    async def remove_active(self, *, craft_id: UUID) -> None:
        self._outside()
        self.calls.append("remove")
        self.active.pop(craft_id, None)


def _world(skill: EditableSkill | None = None) -> tuple[_Unit, _Index]:
    unit = _Unit(_Store(skill or _row()))
    return unit, _Index(unit)


async def _save(unit: _Unit, index: _Index, **overrides: object) -> CraftSkillView:
    payload: dict[str, object] = {
        "title": "煎茶",
        "keywords": ["水温", "苦味"],
        "detail": "水开后降到八十度再泡。",
        "moderation": "allow",
    }
    payload.update(overrides)
    return await save_craft_skill(
        unit,
        index,
        owner_id=_OWNER,
        client_id=_CLIENT,
        craft_id=_CRAFT,
        title=str(payload["title"]),
        keywords=payload["keywords"],  # type: ignore[arg-type]
        detail=str(payload["detail"]),
        moderation=payload["moderation"],  # type: ignore[arg-type]
    )


async def test_public_save_switches_only_after_the_index_activates() -> None:
    unit, index = _world()
    saved = await _save(unit, index)
    assert saved.visibility == "public"
    assert saved.title == "煎茶"
    assert saved.points == 12
    assert "skill_slug" not in CraftSkillView.__dataclass_fields__
    assert "pour-tea" not in str(saved)
    assert unit.store.skill.skill_slug == "pour-tea"
    assert unit.store.skill.points == 12
    assert index.active_at_stage == ["泡茶"]
    assert index.active[_CRAFT]["title"] == "煎茶"
    assert index.calls == ["stage", "activate"]
    assert unit.store.ask_snapshot == _ASK_SNAPSHOT
    again = await _save(unit, index)
    assert again.craft_id == saved.craft_id
    assert index.calls == ["stage", "activate"]


async def test_stage_failure_keeps_the_previous_public_copy() -> None:
    unit, index = _world()
    index.fail = "stage"
    with pytest.raises(ApiError) as caught:
        await _save(unit, index)
    assert caught.value.code == "DEPENDENCY_UNAVAILABLE"
    assert unit.store.skill.title == "泡茶"
    assert unit.store.skill.draft_title == "煎茶"
    assert index.active[_CRAFT]["title"] == "泡茶"
    index.fail = None
    saved = await _save(unit, index)
    assert saved.title == "煎茶"
    assert unit.store.skill.skill_slug == "pour-tea"
    assert unit.store.skill.points == 12


async def test_activate_failure_restores_the_previous_copy() -> None:
    unit, index = _world()
    index.fail = "activate"
    with pytest.raises(ApiError) as caught:
        await _save(unit, index)
    assert caught.value.code == "DEPENDENCY_UNAVAILABLE"
    assert unit.store.skill.title == "泡茶"
    assert unit.store.skill.draft_title == "煎茶"
    assert index.active[_CRAFT]["title"] == "泡茶"
    assert "withdraw" in index.calls
    index.fail = None
    saved = await _save(unit, index)
    assert saved.title == "煎茶"
    assert saved.craft_id == _CRAFT


async def test_rejected_content_does_not_write_a_draft() -> None:
    unit, index = _world()
    with pytest.raises(ApiError) as caught:
        await _save(unit, index, moderation="block")
    assert caught.value.code == "MODERATION_REJECTED"
    assert unit.store.locks == 0
    assert unit.store.skill.draft_title is None
    assert unit.store.skill.title == "泡茶"
    assert index.calls == []


async def test_unpublished_save_does_not_enter_matching() -> None:
    unit, index = _world(_row(visibility="unpublished"))
    saved = await _save(unit, index)
    assert saved.visibility == "unpublished"
    assert saved.title == "煎茶"
    assert unit.store.skill.skill_slug == "pour-tea"
    assert index.calls == []
    assert unit.store.ask_snapshot == _ASK_SNAPSHOT


async def test_private_removes_the_active_index_and_retry_does_not_put_it_back() -> None:
    unit, index = _world()
    unit.store.fail = "private"
    with pytest.raises(RuntimeError):
        await set_craft_visibility(
            unit,
            index,
            owner_id=_OWNER,
            client_id=_CLIENT,
            craft_id=_CRAFT,
            visibility="private",
        )
    assert unit.store.skill.visibility == "public"
    assert _CRAFT not in index.active
    assert "stage" not in index.calls
    hidden = await set_craft_visibility(
        unit,
        index,
        owner_id=_OWNER,
        client_id=_CLIENT,
        craft_id=_CRAFT,
        visibility="private",
    )
    assert hidden.visibility == "private"
    assert unit.store.skill.points == 12
    assert "stage" not in index.calls
    assert "activate" not in index.calls


async def test_republish_activates_only_after_the_skill_is_public() -> None:
    unit, index = _world(_row(visibility="private"))
    index.active.clear()
    index.fail = "activate"
    with pytest.raises(ApiError) as caught:
        await set_craft_visibility(
            unit,
            index,
            owner_id=_OWNER,
            client_id=_CLIENT,
            craft_id=_CRAFT,
            visibility="public",
        )
    assert caught.value.code == "DEPENDENCY_UNAVAILABLE"
    assert unit.store.skill.visibility == "private"
    assert _CRAFT not in index.active
    index.fail = None
    shown = await set_craft_visibility(
        unit,
        index,
        owner_id=_OWNER,
        client_id=_CLIENT,
        craft_id=_CRAFT,
        visibility="public",
    )
    assert shown.visibility == "public"
    assert index.active[_CRAFT]["title"] == "泡茶"
    assert shown.points == 12


async def test_delete_keeps_points_slug_and_ask_snapshot() -> None:
    unit, index = _world()
    unit.store.fail = "delete"
    with pytest.raises(RuntimeError):
        await delete_craft_skill(
            unit, index, owner_id=_OWNER, client_id=_CLIENT, craft_id=_CRAFT
        )
    assert unit.store.skill.deleted_at is None
    assert unit.store.skill.points == 12
    assert "stage" not in index.calls
    removed = await delete_craft_skill(
        unit, index, owner_id=_OWNER, client_id=_CLIENT, craft_id=_CRAFT
    )
    assert removed.deleted is True
    assert "skill_slug" not in removed.__dataclass_fields__
    assert unit.store.skill.points == 12
    assert unit.store.skill.skill_slug == "pour-tea"
    assert unit.store.skill.deleted_at is not None
    assert _CRAFT not in index.active
    assert "stage" not in index.calls
    assert "activate" not in index.calls
    assert unit.store.ask_snapshot == _ASK_SNAPSHOT
    await delete_craft_skill(unit, index, owner_id=_OWNER, client_id=_CLIENT, craft_id=_CRAFT)
    assert index.calls.count("remove") == 2


def test_edit_sql_does_not_touch_snapshots_points_or_bailian() -> None:
    service = (_SERVER / "app" / "services" / "craft" / "edit.py").read_text(encoding="utf-8")
    assert "ask_questions" not in service
    assert "craft_self_test_tickets" not in service
    assert "craft_ledgers" not in service
    assert "bailian" not in service
    assert "points" not in MARK_DELETED_SQL
    registered = {row.action: row.operation for row in V3_WRITE_IDEMPOTENCY}
    assert registered["保存修改"] == CRAFT_SKILL_SAVE_OPERATION
    assert registered["设为私密或再公开"] == CRAFT_SKILL_VISIBILITY_OPERATION
    assert registered["删除技能"] == CRAFT_SKILL_DELETE_OPERATION
