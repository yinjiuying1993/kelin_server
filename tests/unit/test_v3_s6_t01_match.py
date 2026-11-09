"""V3-S6-T01 match reads the index once and does not substitute another spirit."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.domain.ask.match import (
    ConfirmableSkill,
    choose_skill,
    match_payload,
    payload_fields_are_allowed,
)
from app.domain.match_index.rows import QuestionMaterial
from app.integrations.bailian import BailianHttpAdapter
from app.repositories import match_confirm as confirm_sql
from app.services.ask.match import MatchCallFailed, match_published_skill

_ASKER = UUID("00000000-0000-4000-8000-000000000051")
_SPIRIT_A = UUID("00000000-0000-4000-8000-000000000052")
_SPIRIT_B = UUID("00000000-0000-4000-8000-000000000053")
_WHEN = datetime(2026, 11, 4, 4, 0, tzinfo=UTC)


def _material(slug: str, title: str, keywords: tuple[str, ...]) -> QuestionMaterial:
    return QuestionMaterial(skill_slug=slug, title=title, keywords=keywords)


def _skill(
    slug: str,
    spirit: UUID,
    title: str,
    keywords: tuple[str, ...],
    *,
    points: int,
    help_count: int = 0,
    updated: datetime = _WHEN,
    public: bool = True,
) -> ConfirmableSkill:
    return ConfirmableSkill(
        skill_slug=slug,
        spirit_id=spirit,
        title=title,
        keywords=keywords,
        public=public,
        points=points,
        help_count=help_count,
        updated_at=updated,
    )


class _Materials:
    def __init__(self, rows: list[QuestionMaterial]) -> None:
        self.rows = rows
        self.loads = 0

    async def load(self) -> list[QuestionMaterial]:
        self.loads += 1
        return list(self.rows)


class _Confirm:
    def __init__(self, rows: list[ConfirmableSkill]) -> None:
        self.rows = {row.skill_slug: row for row in rows}
        self.calls: list[tuple[str, ...]] = []

    async def confirm(self, skill_slugs: tuple[str, ...]) -> list[ConfirmableSkill]:
        self.calls.append(skill_slugs)
        return [self.rows[slug] for slug in skill_slugs if slug in self.rows]


class _Model:
    def __init__(self, returned: list[str] | None = None, *, fail: bool = False) -> None:
        self.returned = [] if returned is None else returned
        self.fail = fail
        self.payloads: list[dict[str, object]] = []

    async def think(self, payload: dict[str, object]) -> list[str]:
        self.payloads.append(payload)
        if self.fail:
            raise MatchCallFailed
        return list(self.returned)


@pytest.mark.asyncio
async def test_unknown_id_does_not_switch_to_another_skill() -> None:
    materials = _Materials(
        [_material("pour-tea", "倒茶", ("待客",)), _material("mend", "补衣", ("针线",))]
    )
    confirmer = _Confirm([])
    model = _Model(["not-in-list"])
    chosen = await match_published_skill(
        materials, confirmer, model, asker_spirit_id=_ASKER, question="谁会倒茶"
    )
    assert chosen is None
    assert materials.loads == 1
    assert model.payloads[0]["question"] == "谁会倒茶"
    assert confirmer.calls == []
    assert payload_fields_are_allowed(model.payloads[0])


@pytest.mark.asyncio
async def test_changed_skill_or_model_failure_has_no_match() -> None:
    sent = _material("pour-tea", "倒茶", ("待客",))
    materials = _Materials([sent, _material("mend", "补衣", ("针线",))])
    confirmer = _Confirm(
        [_skill("pour-tea", _SPIRIT_A, "倒茶", ("新词",), points=10)]
    )
    model = _Model(["pour-tea"])
    assert (
        await match_published_skill(
            materials, confirmer, model, asker_spirit_id=_ASKER, question="倒茶"
        )
        is None
    )
    assert confirmer.calls == [("pour-tea",)]
    failed = _Model(fail=True)
    with pytest.raises(MatchCallFailed):
        await match_published_skill(
            materials, confirmer, failed, asker_spirit_id=_ASKER, question="倒茶"
        )
    assert failed.payloads != []
    assert materials.loads == 2


@pytest.mark.asyncio
async def test_overflow_and_empty_materials_skip_the_model() -> None:
    materials = _Materials([_material("pour-tea", "倒茶", ("待客",))])
    model = _Model(["pour-tea"])
    assert (
        await match_published_skill(
            materials,
            _Confirm([]),
            model,
            asker_spirit_id=_ASKER,
            question="倒茶",
            request_limit=1,
        )
        is None
    )
    assert model.payloads == []
    empty = _Materials([])
    assert (
        await match_published_skill(
            empty, _Confirm([]), model, asker_spirit_id=_ASKER, question="倒茶"
        )
        is None
    )
    assert materials.loads == 1
    assert empty.loads == 1
    assert model.payloads == []


@pytest.mark.asyncio
async def test_tie_break_keeps_one_confirmed_skill() -> None:
    materials = _Materials(
        [
            _material("low", "低", ("甲",)),
            _material("high", "高", ("乙",)),
        ]
    )
    confirmer = _Confirm(
        [
            _skill("low", _SPIRIT_A, "低", ("甲",), points=50, help_count=9),
            _skill(
                "high",
                _SPIRIT_B,
                "高",
                ("乙",),
                points=100,
                help_count=0,
                updated=_WHEN - timedelta(days=1),
            ),
        ]
    )
    chosen = await match_published_skill(
        materials,
        confirmer,
        _Model(["low", "high", "low"]),
        asker_spirit_id=_ASKER,
        question="谁合适",
    )
    assert chosen is not None
    assert chosen.skill_slug == "high"
    assert chosen.keywords == ("乙",)
    assert materials.loads == 1
    assert confirmer.calls == [("low", "high")]


def test_rank_then_points_then_help_then_time() -> None:
    older = _skill("old", _SPIRIT_A, "甲", ("一",), points=100, help_count=2, updated=_WHEN)
    newer = _skill(
        "new",
        _SPIRIT_B,
        "乙",
        ("二",),
        points=100,
        help_count=2,
        updated=_WHEN + timedelta(minutes=1),
    )
    helped = _skill("helped", _SPIRIT_A, "丙", ("三",), points=100, help_count=3)
    apprentice = _skill("apprentice", _SPIRIT_B, "丁", ("四",), points=50, help_count=99)
    assert choose_skill([older, newer]).skill_slug == "new"
    assert choose_skill([older, helped]).skill_slug == "helped"
    assert choose_skill([helped, apprentice]).skill_slug == "helped"


def test_match_material_whitelist_and_prompt_boundary() -> None:
    payload = match_payload("倒茶", [_material("pour-tea", "倒茶", ("待客",))])
    assert payload_fields_are_allowed(payload)
    encoded = str(payload)
    for forbidden in ("detail", "reference_links", "snapshot_body", "memories", "rank_name"):
        assert forbidden not in encoded
    source = "\n".join(
        (
            inspect.getsource(match_payload),
            inspect.getsource(match_published_skill),
            inspect.getsource(confirm_sql),
        )
    )
    for forbidden in ("snapshot_body", "reference_links", "memories", "complete_self_test"):
        assert forbidden not in source
    match_call = inspect.getsource(BailianHttpAdapter.complete_match)
    assert "thinking=True" in match_call
    assert "_chat_instruction" not in match_call
