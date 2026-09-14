"""Shared ReportSnapshot for bootstrap and GET /report. Spec §§8.10, 9.1, 14.7–14.8."""

from __future__ import annotations

from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.invite_code import INVITE_CODE_PATTERN
from app.schemas.envelope import EnvelopeError
from app.schemas.spirit import SpiritStage

ReportStatus = Literal["locked", "generating", "partial", "ready", "failed"]
CardStatus = Literal["generating", "partial", "ready", "failed"]
TraitDimension = Literal["closeness", "curiosity", "sharpness", "nocturnal", "stubborn"]
MemoryType = Literal["preference", "knowledge", "emotion", "relation", "speech", "sight"]
_LINE_NULL_STATUSES = frozenset({"generating", "partial", "failed"})


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportEligibility(_ForbidExtra):
    is_eligible: bool
    eligible_at: str
    days_remaining: int = Field(ge=0)
    required_dialogue_rounds: int = Field(ge=0)
    completed_dialogue_rounds: int = Field(ge=0)
    dialogue_rounds_remaining: int = Field(ge=0)


class ReportCardSpirit(_ForbidExtra):
    id: UUID
    name: str = Field(min_length=1, max_length=20)
    stage: SpiritStage
    public_layers: list[str]


class ReportTopTrait(_ForbidExtra):
    dimension: TraitDimension
    value: int = Field(ge=0, le=100)


class ReportCardMemory(_ForbidExtra):
    id: UUID
    type: MemoryType | None = None
    summary: str | None = None
    unavailable: bool

    @model_validator(mode="after")
    def unavailable_hides_body(self) -> Self:
        if self.unavailable:
            if self.type is not None or self.summary is not None:
                raise ValueError("unavailable memory must not echo type or summary")
        elif self.type is None or self.summary is None:
            raise ValueError("active memory requires type and summary")
        return self


class ReportCardModel(_ForbidExtra):
    id: UUID
    status: CardStatus
    rules_version: str = Field(min_length=1)
    title: str = Field(min_length=1)
    spirit: ReportCardSpirit
    room_weather: str = Field(min_length=1, max_length=32)
    top_traits: list[ReportTopTrait] = Field(min_length=2, max_length=2)
    top_memories: list[ReportCardMemory] = Field(max_length=3)
    scholar_marks: list[str]
    signature_line: str | None = None
    invite_code: str = Field(pattern=INVITE_CODE_PATTERN)
    generated_at: str
    version: int = Field(ge=1)

    @model_validator(mode="after")
    def line_matches_status(self) -> Self:
        if self.status == "ready":
            if self.signature_line is None or not self.signature_line.strip():
                raise ValueError("ready card requires signature_line")
        elif self.status in _LINE_NULL_STATUSES:
            if self.status == "partial" and self.signature_line is not None:
                raise ValueError("partial card signature_line must be null")
        return self


class ReportSnapshot(_ForbidExtra):
    status: ReportStatus
    report_id: UUID | None
    eligibility: ReportEligibility
    card: ReportCardModel | None = None

    @model_validator(mode="after")
    def report_id_matches_status(self) -> Self:
        if self.status == "locked":
            if self.report_id is not None:
                raise ValueError("locked report_id must be null")
            if self.card is not None:
                raise ValueError("locked card must be null")
        elif self.report_id is None:
            raise ValueError("non-locked report_id is required")
        if self.card is not None:
            if self.card.status != self.status:
                raise ValueError("card.status must match snapshot status")
            if self.card.id != self.report_id:
                raise ValueError("card.id must match report_id")
        elif self.status == "partial":
            raise ValueError("partial snapshot requires a stable card")
        elif self.status == "ready":
            raise ValueError("ready snapshot requires a stable card")
        return self


class ReportLineRequest(_ForbidExtra):
    client_id: UUID
    report_id: UUID
    expected_version: int = Field(ge=1)


class ReportLinePatch(_ForbidExtra):
    snapshot_version: int = Field(ge=1)
    report: ReportSnapshot


class ReportLineResult(_ForbidExtra):
    resource: ReportCardModel
    patch: ReportLinePatch


class ReportSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: ReportSnapshot
    error: None = None
    request_id: str
    server_time: str


class ReportErrorEnvelope(_ForbidExtra):
    ok: Literal[False] = False
    data: None = None
    error: EnvelopeError
    request_id: str
    server_time: str


class ReportLineSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: ReportLineResult
    error: None = None
    request_id: str
    server_time: str


class ReportLineErrorEnvelope(_ForbidExtra):
    ok: Literal[False] = False
    data: None = None
    error: EnvelopeError
    request_id: str
    server_time: str
