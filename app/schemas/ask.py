"""Ask, helped-row, and public spirit profile models.

Draft contract. Skill slugs, skill text, keywords, points, and gaps stay off these responses.
"""

from __future__ import annotations

from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.craft import RankName
from app.schemas.envelope import EnvelopeError

AskRating = Literal["不满意", "满意", "非常满意"]
AskView = Literal["all", "rated", "unrated"]
_RATING_POINTS = {"不满意": 0, "满意": 5, "非常满意": 10}


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


def points_for_rating(rating: AskRating) -> int:
    """Ledger points for one rating. Callers see the label, not this number."""
    return _RATING_POINTS[rating]


class AskQuestionSummary(_ForbidExtra):
    id: UUID
    question: str = Field(min_length=1, max_length=500)
    spirit_name: str = Field(min_length=1)
    spirit_id: UUID | None
    rank_name: RankName | None = None
    skill_title: str | None = Field(min_length=1)
    rated: bool
    rating: AskRating | None = None

    @field_validator("skill_title", "spirit_id", mode="before")
    @classmethod
    def blank_optional_is_null(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @model_validator(mode="after")
    def rating_matches_flag(self) -> Self:
        if self.rated and self.rating is None:
            raise ValueError("rated question needs a rating")
        if not self.rated and self.rating is not None:
            raise ValueError("unrated question has no rating")
        return self


class AskTurn(_ForbidExtra):
    role: Literal["user", "spirit"]
    text: str = Field(min_length=1)


class AskQuestionDetail(AskQuestionSummary):
    turns: list[AskTurn] = Field(min_length=1)

    @model_validator(mode="after")
    def history_has_a_reply(self) -> Self:
        if not any(turn.role == "spirit" for turn in self.turns):
            raise ValueError("history detail needs a reply")
        return self


class AskQuestionPage(_ForbidExtra):
    items: list[AskQuestionSummary]
    next_cursor: str | None
    has_more: bool


class AskRecent(_ForbidExtra):
    items: list[AskQuestionSummary] = Field(max_length=3)


class AskSubmitRequest(_ForbidExtra):
    client_id: UUID
    text: str = Field(min_length=1, max_length=500)


class AskSubmitResult(_ForbidExtra):
    matched: bool
    question: AskQuestionDetail | None = None

    @model_validator(mode="after")
    def dialogue_follows_match(self) -> Self:
        if self.matched and self.question is None:
            raise ValueError("matched submit includes the dialogue")
        if not self.matched and self.question is not None:
            raise ValueError("unmatched submit has no dialogue")
        return self


class AskTurnRequest(_ForbidExtra):
    client_id: UUID
    text: str = Field(min_length=1, max_length=500)


class AskTurnResult(_ForbidExtra):
    question_id: UUID
    reply: str = Field(min_length=1)


class AskRateRequest(_ForbidExtra):
    client_id: UUID
    rating: AskRating


class AskRateResult(_ForbidExtra):
    question_id: UUID
    rating: AskRating


class HelpedRow(_ForbidExtra):
    spirit_name: str = Field(min_length=1)
    help_count: int = Field(ge=1)


class HelpedResult(_ForbidExtra):
    item: HelpedRow | None


class PublicSkill(_ForbidExtra):
    title: str = Field(min_length=1)
    rank_name: RankName | None = None


class SpiritPublicProfile(_ForbidExtra):
    id: UUID
    name: str = Field(min_length=1)
    help_count: int = Field(ge=0)
    skills: list[PublicSkill]


class _SuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    error: None = None
    request_id: str
    server_time: str


class AskSubmitEnvelope(_SuccessEnvelope):
    data: AskSubmitResult


class AskTurnEnvelope(_SuccessEnvelope):
    data: AskTurnResult


class AskQuestionPageEnvelope(_SuccessEnvelope):
    data: AskQuestionPage


class AskRecentEnvelope(_SuccessEnvelope):
    data: AskRecent


class AskQuestionDetailEnvelope(_SuccessEnvelope):
    data: AskQuestionDetail


class AskRateEnvelope(_SuccessEnvelope):
    data: AskRateResult


class HelpedEnvelope(_SuccessEnvelope):
    data: HelpedResult


class SpiritPublicProfileEnvelope(_SuccessEnvelope):
    data: SpiritPublicProfile


class AskErrorEnvelope(_ForbidExtra):
    ok: Literal[False] = False
    data: None = None
    error: EnvelopeError
    request_id: str
    server_time: str
