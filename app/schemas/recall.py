"""POST /recall request/response contract. Spec §§8.7, 11.5, 14.11."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.bootstrap import RoomPublic
from app.schemas.envelope import EnvelopeError
from app.schemas.memory import MemoryPublic
from app.schemas.spirit import MutationEvent, OnboardingState, QuotaUsage, SpiritPublic

RecallMethod = Literal["food", "sight_memory"]


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecallFoodRequest(_ForbidExtra):
    client_id: UUID
    method: Literal["food"]


class RecallSightRequest(_ForbidExtra):
    client_id: UUID
    method: Literal["sight_memory"]
    memory_id: UUID


RecallRequest = Annotated[
    RecallFoodRequest | RecallSightRequest,
    Field(discriminator="method"),
]


class RecallResource(_ForbidExtra):
    type: Literal["recall"] = "recall"
    id: UUID
    version: int = Field(ge=1)
    method: RecallMethod


class RecallPatch(_ForbidExtra):
    snapshot_version: int = Field(ge=1)
    spirit: SpiritPublic | None = None
    preferences: None = None
    room: RoomPublic | None = None
    onboarding: OnboardingState | None = None
    report: None = None
    social: None = None
    memories_upsert: list[MemoryPublic] = Field(default_factory=list)
    memory_tombstones: list[Any] = Field(default_factory=list)
    postcards_upsert: list[Any] = Field(default_factory=list)
    pact: None = None


class RecallMutationResult(_ForbidExtra):
    resource: RecallResource
    patch: RecallPatch
    quotas: list[QuotaUsage] = Field(default_factory=list)
    events: list[MutationEvent] = Field(default_factory=list)


class RecallSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: RecallMutationResult
    error: None = None
    request_id: str
    server_time: str


class RecallErrorEnvelope(_ForbidExtra):
    ok: Literal[False] = False
    data: None = None
    error: EnvelopeError
    request_id: str
    server_time: str
