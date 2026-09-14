"""GET /bootstrap snapshot contract. Spec §§5.1, 9.1, 9.2."""

from __future__ import annotations

from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.envelope import EnvelopeError
from app.schemas.report import ReportSnapshot
from app.schemas.spirit import OnboardingState, QuotaUsage, SpiritPublic

SightSource = Literal["photo", "location"]
PendingSightStatus = Literal["pending", "processing"]


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoomLetter(_ForbidExtra):
    type: str = Field(min_length=1)
    resource_id: UUID
    title_key: str = Field(min_length=1)
    occurred_at: str


class PendingSight(_ForbidExtra):
    feed_id: UUID
    source: SightSource
    status: PendingSightStatus
    updated_at: str


class RoomPublic(_ForbidExtra):
    weather: str = Field(min_length=1, max_length=32)
    layers: list[str]
    letter: RoomLetter | None = None
    pending_sight: PendingSight | None = None
    unread_footprint_count: int = Field(ge=0)
    updated_at: str


class FeatureFlags(_ForbidExtra):
    remote_search: bool
    image_feed: bool


class BootstrapSnapshot(_ForbidExtra):
    schema_version: Literal[2] = 2
    snapshot_version: int = Field(ge=0)
    api_version: Literal["v1"] = "v1"
    spirit: SpiritPublic | None
    room: RoomPublic | None
    onboarding: OnboardingState
    latest_memories: list[Any] = Field(default_factory=list)
    unread_postcards: list[Any] = Field(default_factory=list)
    active_pact: Any | None = None
    report: ReportSnapshot
    quotas: list[QuotaUsage] = Field(default_factory=list)
    feature_flags: FeatureFlags

    @model_validator(mode="after")
    def room_follows_spirit(self) -> Self:
        if self.spirit is None:
            if self.room is not None:
                raise ValueError("room must be null when spirit is null")
        elif self.room is None:
            raise ValueError("room is required when spirit is present")
        return self


class BootstrapSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: BootstrapSnapshot
    error: None = None
    request_id: str
    server_time: str


class BootstrapErrorEnvelope(_ForbidExtra):
    ok: Literal[False] = False
    data: None = None
    error: EnvelopeError
    request_id: str
    server_time: str
