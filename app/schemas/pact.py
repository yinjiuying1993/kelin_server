"""POST /pacts, /pact-session, /pact-answer, /pact-skip contract. Spec §§13.1–13.4.

Clients never send score, completeness, mark, or finalized. The last missing
answer finalizes inside POST /pact-answer. There is no public complete route.
"""

from __future__ import annotations

from datetime import date
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.envelope import EnvelopeError
from app.schemas.spirit import MutationEvent, QuotaUsage, SpiritPublic

PactTheme = Literal["interview", "notes"]
PactStatus = Literal["active", "completed", "abandoned"]
PactSessionStatus = Literal["ready", "answered", "skipped", "closed"]


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreatePactRequest(_ForbidExtra):
    client_id: UUID
    theme: PactTheme
    title: str = Field(min_length=1, max_length=80)
    notes_memory_id: UUID | None = None

    @model_validator(mode="after")
    def notes_theme_requires_memory(self) -> Self:
        if self.theme == "notes" and self.notes_memory_id is None:
            raise ValueError("notes_memory_id is required when theme is notes")
        return self


class PactSessionRequest(_ForbidExtra):
    client_id: UUID
    pact_id: UUID


class PactAnswerRequest(_ForbidExtra):
    client_id: UUID
    session_id: UUID
    expected_session_version: int = Field(ge=1)
    question_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=4000)


class PactSkipRequest(_ForbidExtra):
    client_id: UUID
    pact_id: UUID
    session_date: date


class PactPublic(_ForbidExtra):
    type: Literal["pact"] = "pact"
    id: UUID
    theme: PactTheme
    title: str = Field(min_length=1, max_length=80)
    notes_memory_id: UUID | None = None
    question_bank_version: str = Field(min_length=1, max_length=32)
    status: PactStatus
    completed_sessions: int = Field(ge=0)
    skipped_sessions: int = Field(ge=0)
    completeness: int = Field(ge=0, le=100)
    scholar_mark: str | None = None
    week_start: date
    starts_at: str
    ends_at: str
    version: int = Field(ge=1)
    created_at: str


class PactQuestionPublic(_ForbidExtra):
    question_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=500)


class PactSessionPublic(_ForbidExtra):
    type: Literal["pact_session"] = "pact_session"
    id: UUID
    pact_id: UUID
    date: date
    day_index: int = Field(ge=1, le=7)
    explain: str = Field(min_length=1, max_length=500)
    questions: list[PactQuestionPublic] = Field(min_length=3, max_length=3)
    question_bank_version: str = Field(min_length=1, max_length=32)
    status: PactSessionStatus
    row_version: int = Field(ge=1)


class PactAnswerFeedback(_ForbidExtra):
    summary: str = Field(min_length=1, max_length=500)
    improvements: list[str] = Field(default_factory=list, max_length=8)


class PactMistakePublic(_ForbidExtra):
    question_id: str = Field(min_length=1, max_length=128)
    category: str = Field(min_length=1, max_length=32)
    summary: str = Field(min_length=1, max_length=500)
    times_seen: int = Field(ge=1)


class PactAnswerResource(_ForbidExtra):
    type: Literal["pact_answer"] = "pact_answer"
    session_id: UUID
    question_id: str = Field(min_length=1, max_length=128)
    feedback: PactAnswerFeedback
    answered_count: int = Field(ge=1, le=3)
    row_version: int = Field(ge=1)
    finalized: bool
    score: int | None = Field(default=None, ge=0, le=100)
    pact_status: PactStatus | None = None
    mistakes: list[PactMistakePublic] = Field(default_factory=list)

    @model_validator(mode="after")
    def finalize_fields_are_complete(self) -> Self:
        if self.finalized:
            if self.score is None or self.pact_status is None:
                raise ValueError("finalized answers must include score and pact_status")
            if self.answered_count != 3:
                raise ValueError("finalized answers must have answered_count=3")
        elif self.score is not None or self.pact_status is not None:
            raise ValueError("non-finalized answers must omit score and pact_status")
        return self


class PactSkippedSession(_ForbidExtra):
    type: Literal["pact_session"] = "pact_session"
    id: UUID
    pact_id: UUID
    date: date
    day_index: int = Field(ge=1, le=7)
    status: Literal["skipped"] = "skipped"
    row_version: int = Field(ge=1)


class PactMutationPatch(_ForbidExtra):
    snapshot_version: int = Field(ge=1)
    spirit: SpiritPublic | None = None
    preferences: None = None
    room: None = None
    onboarding: None = None
    report: None = None
    social: None = None
    memories_upsert: list[object] = Field(default_factory=list)
    memory_tombstones: list[object] = Field(default_factory=list)
    postcards_upsert: list[object] = Field(default_factory=list)
    pact: PactPublic | None = None


class CreatePactResult(_ForbidExtra):
    resource: PactPublic
    patch: PactMutationPatch
    quotas: list[QuotaUsage] = Field(default_factory=list)
    events: list[MutationEvent] = Field(default_factory=list)


class PactSessionResult(_ForbidExtra):
    resource: PactSessionPublic
    patch: PactMutationPatch
    quotas: list[QuotaUsage] = Field(default_factory=list)
    events: list[MutationEvent] = Field(default_factory=list)


class PactAnswerResult(_ForbidExtra):
    resource: PactAnswerResource
    patch: PactMutationPatch
    quotas: list[QuotaUsage] = Field(default_factory=list)
    events: list[MutationEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def finalize_patch_includes_pact(self) -> Self:
        if self.resource.finalized:
            if self.patch.pact is None:
                raise ValueError("finalized answers must patch pact")
            if self.resource.pact_status == "completed" and self.patch.spirit is None:
                raise ValueError("completed pact must patch spirit scholar_marks")
        return self


class PactSkipResult(_ForbidExtra):
    resource: PactSkippedSession
    patch: PactMutationPatch
    quotas: list[QuotaUsage] = Field(default_factory=list)
    events: list[MutationEvent] = Field(default_factory=list)


class CreatePactSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: CreatePactResult
    error: None = None
    request_id: str
    server_time: str


class PactSessionSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: PactSessionResult
    error: None = None
    request_id: str
    server_time: str


class PactAnswerSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: PactAnswerResult
    error: None = None
    request_id: str
    server_time: str


class PactSkipSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: PactSkipResult
    error: None = None
    request_id: str
    server_time: str


class PactErrorEnvelope(_ForbidExtra):
    ok: Literal[False] = False
    data: None = None
    error: EnvelopeError
    request_id: str
    server_time: str
