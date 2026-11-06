"""V3-S8-T01 one dialogue settles once, apart from the rating request."""

from __future__ import annotations

import inspect
from uuid import UUID

import pytest
from app.repositories.craft_settle import RecordedSettlement
from app.schemas.craft import rank_for_points
from app.services.craft_ledger import settle as settle_module
from app.services.craft_ledger import worker as worker_module
from app.services.craft_ledger.settle import SettlementAlreadyRecorded, settle_dialogue

_SPIRIT = UUID("00000000-0000-4000-8000-0000000000e1")
_QUESTION = UUID("00000000-0000-4000-8000-0000000000e2")


class _Book:
    def __init__(self) -> None:
        self.help = 4
        self.total = 20
        self.credits = 0

    async def credit(self, spirit_id: UUID, points: int) -> None:
        del spirit_id
        self.credits += 1
        self.help += 1
        self.total += points


class _Skills:
    def __init__(self, *, live: bool, points: int = 95) -> None:
        self.live = live
        self.points = points
        self.rank = rank_for_points(points).name
        self.calls = 0

    async def apply_skill(self, spirit_id: UUID, skill_slug: str, points: int) -> bool:
        del spirit_id, skill_slug
        self.calls += 1
        if not self.live:
            return False
        self.points += points
        self.rank = rank_for_points(self.points).name
        return True


class _Records:
    def __init__(self, *, conflict: bool = False) -> None:
        self.row: RecordedSettlement | None = None
        self.conflict = conflict

    async def find(self, question_id: UUID) -> RecordedSettlement | None:
        del question_id
        return self.row

    async def insert(
        self,
        *,
        question_id: UUID,
        spirit_id: UUID,
        points_added: int,
        skill_points_applied: bool,
    ) -> bool:
        del question_id, spirit_id
        if self.row is not None or self.conflict:
            return False
        self.row = RecordedSettlement(points_added, skill_points_applied)
        return True


@pytest.mark.asyncio
async def test_points_raise_the_rank_once() -> None:
    book = _Book()
    skills = _Skills(live=True)
    records = _Records()
    rating = {"rating": "满意"}
    first = await settle_dialogue(
        book,
        skills,
        records,
        question_id=_QUESTION,
        spirit_id=_SPIRIT,
        skill_slug="pour-tea",
        points=10,
    )
    assert first.repeated is False
    assert first.skill_points_applied is True
    assert book.help == 5
    assert book.total == 30
    assert skills.points == 105
    assert skills.rank == "搭手帮工"
    assert rating == {"rating": "满意"}
    again = await settle_dialogue(
        book,
        skills,
        records,
        question_id=_QUESTION,
        spirit_id=_SPIRIT,
        skill_slug="pour-tea",
        points=10,
    )
    assert again.repeated is True
    assert book.credits == 1
    assert skills.calls == 1
    assert book.help == 5
    assert skills.points == 105


@pytest.mark.asyncio
async def test_zero_points_count_help_without_score() -> None:
    book = _Book()
    skills = _Skills(live=True, points=0)
    records = _Records()
    receipt = await settle_dialogue(
        book,
        skills,
        records,
        question_id=_QUESTION,
        spirit_id=_SPIRIT,
        skill_slug="pour-tea",
        points=0,
    )
    assert receipt.skill_points_applied is False
    assert book.help == 5
    assert book.total == 20
    assert skills.calls == 0
    assert skills.points == 0
    assert skills.rank is None


@pytest.mark.asyncio
async def test_deleted_skill_keeps_its_points() -> None:
    book = _Book()
    skills = _Skills(live=False, points=12)
    records = _Records()
    receipt = await settle_dialogue(
        book,
        skills,
        records,
        question_id=_QUESTION,
        spirit_id=_SPIRIT,
        skill_slug="pour-tea",
        points=5,
    )
    assert receipt.skill_points_applied is False
    assert skills.calls == 1
    assert skills.points == 12
    assert skills.rank == "门边学徒"
    assert book.help == 5
    assert book.total == 25


@pytest.mark.asyncio
async def test_conflicting_insert_does_not_pretend_it_settled() -> None:
    with pytest.raises(SettlementAlreadyRecorded):
        await settle_dialogue(
            _Book(),
            _Skills(live=True),
            _Records(conflict=True),
            question_id=_QUESTION,
            spirit_id=_SPIRIT,
            skill_slug="pour-tea",
            points=5,
        )


def test_settlement_does_not_call_a_model() -> None:
    source = inspect.getsource(settle_module) + inspect.getsource(worker_module)
    for forbidden in ("complete_match", "complete_ask", "complete_self_test", "bailian"):
        assert forbidden not in source
    assert "INSERT INTO" not in inspect.getsource(settle_module)
    assert "craft_ledgers" not in inspect.getsource(settle_module)
