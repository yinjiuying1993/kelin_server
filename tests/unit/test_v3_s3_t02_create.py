"""Idempotent craft create stays unpublished until the index stage and activate both succeed."""

from __future__ import annotations

import copy
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from app.core.errors import ApiError
from app.db.rls_matrix import V3_WRITE_IDEMPOTENCY
from app.domain.craft.create import CRAFT_SKILL_CREATE_OPERATION, SkillSlugTaken
from app.domain.match_index import MatchIndexError
from app.repositories.craft import StoredClaim, StoredSkill
from app.services.craft.create import CreatedCraftSkill, create_craft_skill

_OWNER = UUID("00000000-0000-4000-8000-000000000010")
_CLIENT = UUID("00000000-0000-4000-8000-000000000011")
_SPIRIT = UUID("00000000-0000-4000-8000-000000000012")
_SERVER = Path(__file__).parents[2]


@dataclass
class _Claim:
    request_hash: str
    status: str
    resource_id: UUID | None


class _Store:
    def __init__(self) -> None:
        self.spirit_id: UUID | None = _SPIRIT
        self.skills: dict[UUID, StoredSkill] = {}
        self.claims: dict[tuple[UUID, UUID], _Claim] = {}
        self.inserts = 0
        self.locks = 0
        self.reject_once: str | None = None

    def snapshot(self) -> object:
        return copy.deepcopy((self.skills, self.claims, self.inserts, self.locks))

    def restore(self, snapshot: object) -> None:
        self.skills, self.claims, self.inserts, self.locks = snapshot  # type: ignore[misc]

    async def lock_owner(self, owner_id: UUID) -> None:
        del owner_id
        self.locks += 1

    async def owner_spirit(self, owner_id: UUID) -> UUID | None:
        del owner_id
        return self.spirit_id

    async def claim(self, owner_id: UUID, client_id: UUID, request_hash: str) -> StoredClaim:
        key = (owner_id, client_id)
        row = self.claims.get(key)
        if row is None:
            self.claims[key] = _Claim(request_hash, "in_progress", None)
            return StoredClaim(True, request_hash, "in_progress", None)
        return StoredClaim(False, row.request_hash, row.status, row.resource_id)

    async def taken_slugs(self, spirit_id: UUID) -> set[str]:
        return {skill.skill_slug for skill in self.skills.values() if skill.spirit_id == spirit_id}

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
    ) -> StoredSkill:
        del body
        if skill_slug == self.reject_once:
            self.reject_once = None
            raise SkillSlugTaken
        self.inserts += 1
        skill = StoredSkill(
            craft_id=uuid4(),
            spirit_id=spirit_id,
            skill_slug=skill_slug,
            title=title,
            keywords=keywords,
            detail=detail,
            reference_links=reference_links,
            visibility="unpublished",
            points=0,
        )
        self.skills[skill.craft_id] = skill
        return skill

    async def get_skill(self, craft_id: UUID) -> StoredSkill | None:
        return self.skills.get(craft_id)

    async def set_visibility(self, craft_id: UUID, visibility: str) -> None:
        self.skills[craft_id] = replace(self.skills[craft_id], visibility=visibility)  # type: ignore[arg-type]

    async def bind_claim(self, owner_id: UUID, client_id: UUID, craft_id: UUID) -> None:
        self.claims[(owner_id, client_id)].resource_id = craft_id

    async def complete_claim(self, owner_id: UUID, client_id: UUID, craft_id: UUID) -> None:
        row = self.claims[(owner_id, client_id)]
        row.status = "completed"
        row.resource_id = craft_id


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
        self.active: dict[UUID, dict[str, object]] = {}

    def _outside(self) -> None:
        assert self.unit.depth == 0

    async def stage(self, **kwargs: object) -> None:
        self._outside()
        assert {"body", "detail", "reference_links", "description"}.isdisjoint(kwargs)
        self.calls.append("stage")
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
        self.active.pop(craft_id, None)


def _world() -> tuple[_Unit, _Index]:
    unit = _Unit(_Store())
    return unit, _Index(unit)


async def _create(unit: _Unit, index: _Index, **overrides: object):
    payload = {
        "title": "泡茶",
        "keywords": ["水温"],
        "detail": "先烧水，再闷三分钟。",
        "moderation": "allow",
        "rng": lambda: "pour-tea",
    }
    payload.update(overrides)
    return await create_craft_skill(
        unit,
        index,
        owner_id=_OWNER,
        client_id=_CLIENT,
        **payload,  # type: ignore[arg-type]
    )


async def test_both_steps_return_one_public_skill_without_the_slug() -> None:
    unit, index = _world()
    created = await _create(unit, index)
    assert created.visibility == "public"
    assert created.points == 0
    assert "skill_slug" not in CreatedCraftSkill.__dataclass_fields__
    assert "pour-tea" not in str(created)
    assert unit.store.inserts == 1
    assert index.calls == ["stage", "activate"]
    assert index.active[created.craft_id]["keywords"] == ("水温",)
    assert unit.store.skills[created.craft_id].visibility == "public"


async def test_same_key_does_not_create_a_second_skill() -> None:
    unit, index = _world()
    first = await _create(unit, index)
    second = await _create(unit, index)
    assert second.craft_id == first.craft_id
    assert unit.store.inserts == 1
    assert index.calls == ["stage", "activate"]


async def test_same_key_with_different_content_conflicts() -> None:
    unit, index = _world()
    await _create(unit, index)
    with pytest.raises(ApiError) as caught:
        await _create(unit, index, detail="换一种泡法，先温杯。")
    assert caught.value.code == "IDEMPOTENCY_CONFLICT"
    assert unit.store.inserts == 1


async def test_stage_failure_keeps_the_skill_unpublished() -> None:
    unit, index = _world()
    index.fail = "stage"
    with pytest.raises(ApiError) as caught:
        await _create(unit, index)
    assert caught.value.code == "DEPENDENCY_UNAVAILABLE"
    assert caught.value.retryable is True
    assert "pour-tea" not in caught.value.message
    skill = next(iter(unit.store.skills.values()))
    assert skill.visibility == "unpublished"
    assert index.calls == ["stage"]
    index.fail = None
    created = await _create(unit, index)
    assert created.visibility == "public"
    assert created.craft_id == skill.craft_id
    assert unit.store.inserts == 1


async def test_activate_failure_reverts_public_and_retry_keeps_the_same_skill() -> None:
    unit, index = _world()
    index.fail = "activate"
    with pytest.raises(ApiError) as caught:
        await _create(unit, index)
    assert caught.value.code == "DEPENDENCY_UNAVAILABLE"
    skill = next(iter(unit.store.skills.values()))
    assert skill.visibility == "unpublished"
    assert index.calls == ["stage", "activate", "withdraw"]
    assert created_ids(index) == set()
    index.fail = None
    created = await _create(unit, index)
    assert created.craft_id == skill.craft_id
    assert created.visibility == "public"
    assert unit.store.inserts == 1


async def test_moderation_block_does_not_open_a_transaction() -> None:
    unit, index = _world()
    with pytest.raises(ApiError) as caught:
        await _create(unit, index, moderation="block")
    assert caught.value.code == "MODERATION_REJECTED"
    assert unit.store.locks == 0
    assert unit.store.inserts == 0
    assert index.calls == []


async def test_slug_collision_retries_inside_the_create_transaction() -> None:
    unit, index = _world()
    pending = iter(("taken-id", "fresh-id"))
    unit.store.reject_once = "taken-id"
    created = await _create(unit, index, rng=lambda: next(pending))
    assert unit.store.inserts == 1
    assert unit.store.skills[created.craft_id].skill_slug == "fresh-id"
    assert "fresh-id" not in str(created)


def test_create_does_not_call_bailian_or_write_the_index_table() -> None:
    service = (_SERVER / "app" / "services" / "craft" / "create.py").read_text(encoding="utf-8")
    repository = (_SERVER / "app" / "repositories" / "craft.py").read_text(encoding="utf-8")
    assert "bailian" not in service
    assert "build_provider" not in service
    assert "craft_match_index" not in repository
    assert "'unpublished'" in repository
    registered = next(row.operation for row in V3_WRITE_IDEMPOTENCY if row.action == "创建技能")
    assert registered == CRAFT_SKILL_CREATE_OPERATION


def created_ids(index: _Index) -> set[UUID]:
    return set(index.active)
