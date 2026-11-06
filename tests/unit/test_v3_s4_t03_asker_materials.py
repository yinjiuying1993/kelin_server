"""V3-S4-T03 question materials exclude the asker's spirit and skip inactive slots."""

from __future__ import annotations

import inspect
from dataclasses import fields
from uuid import UUID, uuid4

import pytest
from app.domain.match_index import MatchIndexError
from app.domain.match_index.rows import MatchRow, QuestionMaterial
from app.repositories import match_index as match_index_sql
from app.services.match_index import MatchIndexService, read_asker_materials
from app.services.match_index import materials as materials_module

_ASKER = UUID("00000000-0000-4000-8000-000000000021")
_OWN = UUID("00000000-0000-4000-8000-000000000022")
_OTHER = UUID("00000000-0000-4000-8000-000000000023")


def _row(
    spirit_id: UUID,
    slug: str,
    *,
    active: tuple[str, tuple[str, ...]] | None = None,
    staged: tuple[str, tuple[str, ...]] | None = None,
) -> MatchRow:
    return MatchRow(
        craft_id=uuid4(),
        spirit_id=spirit_id,
        skill_slug=slug,
        active_title=None if active is None else active[0],
        active_keywords=None if active is None else active[1],
        staged_title=None if staged is None else staged[0],
        staged_keywords=None if staged is None else staged[1],
    )


class _Store:
    def __init__(self, rows: list[MatchRow]) -> None:
        self.rows = rows
        self.reads = 0

    async def list_for_asker(self, exclude_spirit_id: UUID | None) -> list[MatchRow]:
        del exclude_spirit_id
        self.reads += 1
        return list(self.rows)


class _Askers:
    def __init__(self, spirit_id: UUID | None) -> None:
        self.bound = spirit_id

    async def spirit_id(self, user_id: UUID) -> UUID | None:
        del user_id
        return self.bound


@pytest.mark.asyncio
async def test_asker_materials_are_other_spirits_active_slots_only() -> None:
    rows = [
        _row(_OWN, "own-tea", active=("自家茶", ("自关键字",))),
        _row(_OWN, "own-draft", staged=("自家暂存", ("看不到",))),
        _row(_OTHER, "their-tea", active=("别人茶", ("水温",)), staged=("新标题", ("新词",))),
        _row(_OTHER, "their-draft", staged=("别人暂存", ("草稿",))),
        _row(_OTHER, "their-hidden", active=None),
    ]
    store = _Store(rows)
    visible = await read_asker_materials(
        MatchIndexService(store),
        _Askers(_OWN),
        asker_user_id=_ASKER,
        asker_spirit_id=_OWN,
    )
    assert visible == [
        QuestionMaterial(skill_slug="their-tea", title="别人茶", keywords=("水温",))
    ]
    assert {field.name for field in fields(QuestionMaterial)} == {
        "skill_slug",
        "title",
        "keywords",
    }
    assert store.reads == 1


@pytest.mark.asyncio
async def test_asker_without_a_spirit_still_omits_inactive_slots() -> None:
    store = _Store(
        [
            _row(_OTHER, "their-tea", active=("别人茶", ("水温",))),
            _row(_OTHER, "their-draft", staged=("别人暂存", ("草稿",))),
        ]
    )
    visible = await read_asker_materials(
        MatchIndexService(store),
        _Askers(None),
        asker_user_id=_ASKER,
        asker_spirit_id=None,
    )
    assert visible == [
        QuestionMaterial(skill_slug="their-tea", title="别人茶", keywords=("水温",))
    ]


@pytest.mark.asyncio
async def test_spirit_that_is_not_the_users_returns_nothing() -> None:
    store = _Store([_row(_OWN, "own-tea", active=("自家茶", ("自关键字",)))])
    with pytest.raises(MatchIndexError):
        await read_asker_materials(
            MatchIndexService(store),
            _Askers(_OWN),
            asker_user_id=_ASKER,
            asker_spirit_id=_OTHER,
        )
    assert store.reads == 0


def test_question_read_does_not_touch_the_skill_body_or_a_model() -> None:
    source = "\n".join(
        (
            inspect.getsource(match_index_sql),
            inspect.getsource(materials_module),
            inspect.getsource(MatchIndexService),
        )
    )
    for forbidden in (
        "bailian",
        "craft_ledgers",
        "craft_skills",
        "reference_links",
        "rank_name",
        "snapshot_body",
    ):
        assert forbidden not in source
    assert "active_title IS NOT NULL" in inspect.getsource(match_index_sql)
    assert "spirit_id <>" in inspect.getsource(match_index_sql)
