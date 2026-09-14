"""POST /spirits request/response contract. Spec §§5.2, 6.3, 9.3."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.invite_code import INVITE_CODE_PATTERN
from app.schemas.envelope import EnvelopeError

Egg = Literal["warm", "cold", "wild"]
SpiritStage = Literal["whelp", "formed", "awake"]
SpiritStatus = Literal["home", "away", "study", "lost"]


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AiDisclosureConsent(_ForbidExtra):
    """S02 explicit AI disclosure. Must not be pre-checked. Spec §6.3."""

    document_version: str = Field(min_length=1, max_length=32)
    explicitly_accepted: Literal[True]


class DisplayedDocumentConsent(_ForbidExtra):
    """Shown-document record. displayed is not an explicit checkbox. Spec §6.3."""

    document_version: str = Field(min_length=1, max_length=32)
    displayed: Literal[True]


class CreateSpiritConsents(_ForbidExtra):
    ai_disclosure: AiDisclosureConsent
    data_notice: DisplayedDocumentConsent
    user_terms: DisplayedDocumentConsent


class CreateSpiritRequest(_ForbidExtra):
    client_id: UUID
    egg: Egg
    name: str = Field(min_length=1, max_length=20)
    consents: CreateSpiritConsents


class SpiritPublic(_ForbidExtra):
    id: UUID
    name: str = Field(min_length=1, max_length=20)
    egg: Egg
    invite_code: str = Field(pattern=INVITE_CODE_PATTERN)
    closeness: int = Field(ge=0, le=100)
    curiosity: int = Field(ge=0, le=100)
    sharpness: int = Field(ge=0, le=100)
    nocturnal: int = Field(ge=0, le=100)
    stubborn: int = Field(ge=0, le=100)
    hunger: int = Field(ge=0, le=100)
    energy: int = Field(ge=0, le=100)
    mood: int = Field(ge=0, le=100)
    bond: int = Field(ge=0, le=100)
    stage: SpiritStage
    status: SpiritStatus
    scholar_marks: list[str]
    version: int = Field(ge=1)
    onboarding_step: int = Field(ge=0, le=5)
    onboarding_completed_at: str | None
    hatched_at: str | None
    created_at: str


class OnboardingState(_ForbidExtra):
    required: bool
    step: int = Field(ge=0, le=5)
    total_steps: Literal[5] = 5
    completed_at: str | None = None


class UserPreferencesPublic(_ForbidExtra):
    tts_on: bool
    push_on: bool
    visit_on: bool
    dnd_start: str
    dnd_end: str
    timezone: str
    default_city: str | None
    location_weather_on: bool
    remote_search_on: bool


class MutationResource(_ForbidExtra):
    type: Literal["spirit"]
    id: UUID
    version: int = Field(ge=1)


class MutationPatch(_ForbidExtra):
    snapshot_version: int = Field(ge=1)
    spirit: SpiritPublic | None = None
    preferences: UserPreferencesPublic | None = None
    room: None = None
    onboarding: OnboardingState | None = None
    report: None = None
    social: None = None
    memories_upsert: list[Any] = Field(default_factory=list)
    memory_tombstones: list[Any] = Field(default_factory=list)
    postcards_upsert: list[Any] = Field(default_factory=list)
    pact: None = None


class QuotaUsage(_ForbidExtra):
    capability: str
    used: int = Field(ge=0)
    limit: int = Field(ge=0)
    reset_at: str


class MutationEvent(_ForbidExtra):
    id: UUID
    type: str
    occurred_at: str


class MutationResult(_ForbidExtra):
    resource: MutationResource
    patch: MutationPatch
    quotas: list[QuotaUsage] = Field(default_factory=list)
    events: list[MutationEvent] = Field(default_factory=list)


class SpiritCreateSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: MutationResult
    error: None = None
    request_id: str
    server_time: str


class SpiritCreateErrorEnvelope(_ForbidExtra):
    ok: Literal[False] = False
    data: None = None
    error: EnvelopeError
    request_id: str
    server_time: str
