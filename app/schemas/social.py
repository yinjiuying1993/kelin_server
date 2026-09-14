"""Pydantic schema boundary for JSONB columns on social, report, and outbox tables."""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VisitPublicContext(_ForbidExtra):
    title: str | None = Field(default=None, max_length=40)
    stage: Literal["whelp", "formed", "awake"] | None = None
    weather: str | None = Field(default=None, max_length=32)
    public_marks: list[str] = Field(default_factory=list)


class PactQuestion(_ForbidExtra):
    question_id: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=500)


class PactAnswerEntry(_ForbidExtra):
    question_id: str = Field(min_length=1, max_length=128)
    client_id: UUID
    text: str = Field(min_length=1, max_length=4000)


class PactSessionFeedback(_ForbidExtra):
    summary: str | None = Field(default=None, max_length=500)


class ReportEligibilitySnapshot(_ForbidExtra):
    is_eligible: bool
    eligible_at: AwareDatetime
    days_remaining: int = Field(ge=0)
    required_dialogue_rounds: int = Field(ge=0)
    completed_dialogue_rounds: int = Field(ge=0)
    dialogue_rounds_remaining: int = Field(ge=0)


class ReportSpiritSnapshot(_ForbidExtra):
    id: UUID
    name: str = Field(min_length=1, max_length=20)
    stage: Literal["whelp", "formed", "awake"]
    public_layers: list[str]


class ReportTopTrait(_ForbidExtra):
    dimension: Literal["closeness", "curiosity", "sharpness", "nocturnal", "stubborn"]
    value: int = Field(ge=0, le=100)


class ReportMemorySnapshot(_ForbidExtra):
    id: UUID
    type: Literal["preference", "knowledge", "emotion", "relation", "speech", "sight"] | None = None
    summary: str | None = Field(default=None, max_length=500)


class OutboxPayload(_ForbidExtra):
    """IDs, enums, and non-sensitive scalars only; no message/memory/postcard body."""

    resource_id: UUID | None = None
    local_date: date | None = None


_PACT_QUESTIONS_ADAPTER: TypeAdapter[list[PactQuestion]] = TypeAdapter(list[PactQuestion])
_PACT_ANSWERS_ADAPTER: TypeAdapter[list[PactAnswerEntry]] = TypeAdapter(list[PactAnswerEntry])
_TOP_TRAITS_ADAPTER: TypeAdapter[list[ReportTopTrait]] = TypeAdapter(list[ReportTopTrait])
_MEMORY_SNAPSHOTS_ADAPTER: TypeAdapter[list[ReportMemorySnapshot]] = TypeAdapter(
    list[ReportMemorySnapshot]
)


def parse_visit_public_context(value: object) -> VisitPublicContext:
    return VisitPublicContext.model_validate(value)


def parse_pact_questions(value: object) -> list[PactQuestion]:
    return _PACT_QUESTIONS_ADAPTER.validate_python(value)


def parse_pact_answers(value: object) -> list[PactAnswerEntry]:
    return _PACT_ANSWERS_ADAPTER.validate_python(value)


def parse_pact_feedback(value: object) -> PactSessionFeedback:
    return PactSessionFeedback.model_validate(value)


def parse_report_eligibility(value: object) -> ReportEligibilitySnapshot:
    return ReportEligibilitySnapshot.model_validate(value)


def parse_report_spirit_snapshot(value: object) -> ReportSpiritSnapshot:
    return ReportSpiritSnapshot.model_validate(value)


def parse_report_top_traits(value: object) -> list[ReportTopTrait]:
    return _TOP_TRAITS_ADAPTER.validate_python(value)


def parse_report_memory_snapshots(value: object) -> list[ReportMemorySnapshot]:
    return _MEMORY_SNAPSHOTS_ADAPTER.validate_python(value)


def parse_outbox_payload(value: object) -> OutboxPayload:
    return OutboxPayload.model_validate(value)
