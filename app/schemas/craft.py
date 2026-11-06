"""Craft and self-test HTTP models.

Draft contract. The public address is the resource UUID. Skill slugs stay on the server.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.envelope import EnvelopeError

RankName = Literal["门边学徒", "搭手帮工", "上手匠人", "独当师傅", "掌作名匠", "镇场宗师"]
CraftVisibility = Literal["public", "private"]
_REDEEM_AT = 1000


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class RankProgress:
    name: RankName | None
    points_to_next: int | None
    at_max_rank: bool


def rank_for_points(points: int) -> RankProgress:
    """Rank from one skill's points. Zero has no name; 1500 and above stays at the top."""
    if points < 0:
        raise ValueError("points must be >= 0")
    if points == 0:
        return RankProgress(name=None, points_to_next=1, at_max_rank=False)
    if points < 100:
        return RankProgress(name="门边学徒", points_to_next=100 - points, at_max_rank=False)
    if points < 300:
        return RankProgress(name="搭手帮工", points_to_next=300 - points, at_max_rank=False)
    if points < 600:
        return RankProgress(name="上手匠人", points_to_next=600 - points, at_max_rank=False)
    if points < 1000:
        return RankProgress(name="独当师傅", points_to_next=1000 - points, at_max_rank=False)
    if points < 1500:
        return RankProgress(name="掌作名匠", points_to_next=1500 - points, at_max_rank=False)
    return RankProgress(name="镇场宗师", points_to_next=None, at_max_rank=True)


class CraftSkillSummary(_ForbidExtra):
    id: UUID
    title: str = Field(min_length=1)
    visibility: CraftVisibility
    points: int = Field(ge=0)
    rank_name: RankName | None = None
    points_to_next: int | None = Field(default=None, ge=1)
    at_max_rank: bool
    updated_at: datetime

    @model_validator(mode="after")
    def rank_matches_points(self) -> Self:
        expected = rank_for_points(self.points)
        if (
            self.rank_name != expected.name
            or self.points_to_next != expected.points_to_next
            or self.at_max_rank != expected.at_max_rank
        ):
            raise ValueError("rank does not match points")
        return self


class CraftSkillDetail(CraftSkillSummary):
    keywords: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1)
    description: str = Field(min_length=1)
    reference_links: list[Annotated[str, Field(min_length=1)]] = Field(default_factory=list)


class CraftSkillPage(_ForbidExtra):
    total_points: int = Field(ge=0)
    items: list[CraftSkillSummary]
    next_cursor: str | None
    has_more: bool


class CraftSkillWriteRequest(_ForbidExtra):
    client_id: UUID
    title: str = Field(min_length=1)
    keywords: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1)
    description: str = Field(min_length=1)
    reference_links: list[Annotated[str, Field(min_length=1)]] = Field(default_factory=list)


class CraftVisibilityRequest(_ForbidExtra):
    client_id: UUID
    visibility: CraftVisibility


class CraftDeleteRequest(_ForbidExtra):
    client_id: UUID


class CraftDeleted(_ForbidExtra):
    id: UUID
    deleted: Literal[True]


class CraftSelfTestTicket(_ForbidExtra):
    ticket: UUID
    expires_at: datetime


class CraftSelfTestTurnRequest(_ForbidExtra):
    ticket: UUID
    text: str = Field(min_length=1, max_length=500)


class CraftSelfTestTurn(_ForbidExtra):
    reply: str = Field(min_length=1)


class CraftRedeemMarkRequest(_ForbidExtra):
    client_id: UUID
    spirit_id: UUID


class CraftRedeemMark(_ForbidExtra):
    spirit_id: UUID
    redeemed: Literal[True]
    redeemed_at: datetime


class CraftRedeemEligibility(_ForbidExtra):
    eligible: bool
    total_points: int = Field(ge=0)
    redeemed: bool

    @model_validator(mode="after")
    def eligibility_matches_ledger(self) -> Self:
        expected = self.total_points >= _REDEEM_AT and not self.redeemed
        if self.eligible != expected:
            raise ValueError("eligibility does not match points and redemption")
        return self


class _SuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    error: None = None
    request_id: str
    server_time: str


class CraftSkillDetailEnvelope(_SuccessEnvelope):
    data: CraftSkillDetail


class CraftSkillPageEnvelope(_SuccessEnvelope):
    data: CraftSkillPage


class CraftDeletedEnvelope(_SuccessEnvelope):
    data: CraftDeleted


class CraftSelfTestTicketEnvelope(_SuccessEnvelope):
    data: CraftSelfTestTicket


class CraftSelfTestTurnEnvelope(_SuccessEnvelope):
    data: CraftSelfTestTurn


class CraftRedeemEligibilityEnvelope(_SuccessEnvelope):
    data: CraftRedeemEligibility


class CraftRedeemMarkEnvelope(_SuccessEnvelope):
    data: CraftRedeemMark


class CraftErrorEnvelope(_ForbidExtra):
    ok: Literal[False] = False
    data: None = None
    error: EnvelopeError
    request_id: str
    server_time: str
