"""Settle one rating. This module calls the ledger and the skill, and does not write them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.repositories.craft_settle import RecordedSettlement

_POINTS = frozenset({0, 5, 10})


class SettlementAlreadyRecorded(Exception):
    def __init__(self) -> None:
        super().__init__("settlement already recorded")


@dataclass(frozen=True)
class SettlementReceipt:
    question_id: UUID
    points_added: int
    skill_points_applied: bool
    repeated: bool


class HelpBook(Protocol):
    async def credit(self, spirit_id: UUID, points: int) -> None: ...


class SkillBook(Protocol):
    async def apply_skill(self, spirit_id: UUID, skill_slug: str, points: int) -> bool: ...


class SettlementRecords(Protocol):
    async def find(self, question_id: UUID) -> RecordedSettlement | None: ...

    async def insert(
        self,
        *,
        question_id: UUID,
        spirit_id: UUID,
        points_added: int,
        skill_points_applied: bool,
    ) -> bool: ...


async def settle_dialogue(
    book: HelpBook,
    skills: SkillBook,
    records: SettlementRecords,
    *,
    question_id: UUID,
    spirit_id: UUID,
    skill_slug: str,
    points: int,
) -> SettlementReceipt:
    if points not in _POINTS:
        raise ValueError("rating points must be 0, 5, or 10")
    existing = await records.find(question_id)
    if existing is not None:
        return SettlementReceipt(
            question_id,
            existing.points_added,
            existing.skill_points_applied,
            True,
        )
    applied = False
    if points > 0:
        applied = await skills.apply_skill(spirit_id, skill_slug, points)
    await book.credit(spirit_id, points)
    inserted = await records.insert(
        question_id=question_id,
        spirit_id=spirit_id,
        points_added=points,
        skill_points_applied=applied,
    )
    if not inserted:
        raise SettlementAlreadyRecorded
    return SettlementReceipt(question_id, points, applied, False)
