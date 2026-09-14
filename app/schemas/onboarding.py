"""POST /onboarding/complete request/response contract. Spec §§8.2, 9.4."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.bootstrap import RoomPublic
from app.schemas.envelope import EnvelopeError
from app.schemas.report import ReportSnapshot
from app.schemas.spirit import (
    MutationEvent,
    MutationResource,
    OnboardingState,
    QuotaUsage,
    SpiritPublic,
    UserPreferencesPublic,
)


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CompleteOnboardingRequest(_ForbidExtra):
    client_id: UUID
    expected_spirit_version: int = Field(
        ge=1,
        description=(
            "Must match spirits.version. Completes only when onboarding_step=5 and five "
            "complete onboarding rounds exist. Repeat with the same client_id returns the "
            "same hatched_at."
        ),
    )


class OnboardingCompletePatch(_ForbidExtra):
    snapshot_version: int = Field(ge=1)
    spirit: SpiritPublic | None = None
    preferences: UserPreferencesPublic | None = None
    room: RoomPublic | None = None
    onboarding: OnboardingState | None = None
    report: ReportSnapshot | None = None
    social: None = None
    memories_upsert: list[Any] = Field(default_factory=list)
    memory_tombstones: list[Any] = Field(default_factory=list)
    postcards_upsert: list[Any] = Field(default_factory=list)
    pact: None = None


class OnboardingCompleteResult(_ForbidExtra):
    resource: MutationResource
    patch: OnboardingCompletePatch
    quotas: list[QuotaUsage] = Field(default_factory=list)
    events: list[MutationEvent] = Field(default_factory=list)


class OnboardingCompleteSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: OnboardingCompleteResult
    error: None = None
    request_id: str
    server_time: str


class OnboardingCompleteErrorEnvelope(_ForbidExtra):
    ok: Literal[False] = False
    data: None = None
    error: EnvelopeError
    request_id: str
    server_time: str
