"""V3-S4-T01 staged slots stay invisible until activate, and removal hides them."""

from __future__ import annotations

import inspect
from dataclasses import fields
from uuid import uuid4

import pytest
from app.domain.match_index.rows import MatchIndexError, MatchRow, QuestionMaterial
from app.repositories import match_index as match_index_sql
from app.services.match_index import MatchIndexService

_SLUG = "pour-tea"


class _MemoryStore:
    def __init__(self) -> None:
        self.rows: dict[object, MatchRow] = {}

    async def get(self, craft_id: object) -> MatchRow | None:
        return self.rows.get(craft_id)

    async def save(self, row: MatchRow) -> None:
        self.rows[row.craft_id] = row

    async def list_rows(self) -> list[MatchRow]:
        return list(self.rows.values())


def _service() -> tuple[MatchIndexService, _MemoryStore]:
    store = _MemoryStore()
    return MatchIndexService(store), store


@pytest.mark.asyncio
async def test_staged_slot_is_not_readable_by_a_question() -> None:
    service, _store = _service()
    craft_id = uuid4()
    await service.stage(
        craft_id=craft_id,
        spirit_id=uuid4(),
        skill_slug=_SLUG,
        title="倒茶",
        keywords=("待客",),
    )
    assert await service.materials_for_questions() == []


@pytest.mark.asyncio
async def test_activate_replaces_the_previous_projection() -> None:
    service, _store = _service()
    craft_id = uuid4()
    spirit_id = uuid4()
    await service.stage(
        craft_id=craft_id,
        spirit_id=spirit_id,
        skill_slug=_SLUG,
        title="倒茶",
        keywords=("待客",),
    )
    await service.activate(craft_id=craft_id)
    await service.stage(
        craft_id=craft_id,
        spirit_id=spirit_id,
        skill_slug=_SLUG,
        title="新倒茶",
        keywords=("新待客",),
    )
    visible = await service.materials_for_questions()
    assert visible == [
        QuestionMaterial(skill_slug=_SLUG, title="倒茶", keywords=("待客",))
    ]
    await service.activate(craft_id=craft_id)
    assert await service.materials_for_questions() == [
        QuestionMaterial(skill_slug=_SLUG, title="新倒茶", keywords=("新待客",))
    ]


@pytest.mark.asyncio
async def test_withdraw_keeps_the_previous_active_slot() -> None:
    service, _store = _service()
    craft_id = uuid4()
    spirit_id = uuid4()
    await service.stage(
        craft_id=craft_id,
        spirit_id=spirit_id,
        skill_slug=_SLUG,
        title="倒茶",
        keywords=("待客",),
    )
    await service.activate(craft_id=craft_id)
    await service.stage(
        craft_id=craft_id,
        spirit_id=spirit_id,
        skill_slug=_SLUG,
        title="新倒茶",
        keywords=("新待客",),
    )
    await service.withdraw(craft_id=craft_id)
    assert await service.materials_for_questions() == [
        QuestionMaterial(skill_slug=_SLUG, title="倒茶", keywords=("待客",))
    ]


@pytest.mark.asyncio
async def test_remove_active_hides_the_skill_immediately() -> None:
    service, store = _service()
    craft_id = uuid4()
    spirit_id = uuid4()
    await service.stage(
        craft_id=craft_id,
        spirit_id=spirit_id,
        skill_slug=_SLUG,
        title="倒茶",
        keywords=("待客",),
    )
    await service.activate(craft_id=craft_id)
    await service.stage(
        craft_id=craft_id,
        spirit_id=spirit_id,
        skill_slug=_SLUG,
        title="未生效",
        keywords=("草稿",),
    )
    await service.remove_active(craft_id=craft_id)
    assert await service.materials_for_questions() == []
    assert store.rows[craft_id].staged_title == "未生效"


@pytest.mark.asyncio
async def test_activate_without_a_staged_slot_fails() -> None:
    service, _store = _service()
    with pytest.raises(MatchIndexError):
        await service.activate(craft_id=uuid4())


def test_question_material_and_sql_omit_the_skill_body() -> None:
    assert {field.name for field in fields(QuestionMaterial)} == {
        "skill_slug",
        "title",
        "keywords",
    }
    parameters = inspect.signature(MatchIndexService.stage).parameters
    assert {"body", "detail", "reference_links"}.isdisjoint(parameters)
    source = inspect.getsource(match_index_sql)
    for forbidden in ("body", "detail", "reference_links", "points", "rank_name"):
        assert forbidden not in source
