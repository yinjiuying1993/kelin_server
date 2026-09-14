"""POST /feed request/response contract. Spec §§5.2, 8.4, 11.1."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.schemas.bootstrap import RoomPublic
from app.schemas.envelope import EnvelopeError
from app.schemas.jsonb import (
    EmotionPayload,
    FoodPayload,
    KnowledgePayload,
    PromisePayload,
    SightPayload,
)
from app.schemas.memory import MemoryPublic
from app.schemas.spirit import MutationEvent, OnboardingState, QuotaUsage, SpiritPublic

FeedKind = Literal["food", "knowledge", "emotion", "promise", "sight"]
FeedStatus = Literal["pending", "processing", "accepted", "rejected", "cancelled"]


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FeedCreateFood(_ForbidExtra):
    client_id: UUID
    kind: Literal["food"]
    payload: FoodPayload


class FeedCreateKnowledge(_ForbidExtra):
    client_id: UUID
    kind: Literal["knowledge"]
    payload: KnowledgePayload


class FeedCreateEmotion(_ForbidExtra):
    client_id: UUID
    kind: Literal["emotion"]
    payload: EmotionPayload


class FeedCreatePromise(_ForbidExtra):
    client_id: UUID
    kind: Literal["promise"]
    payload: PromisePayload


class FeedCreateSight(_ForbidExtra):
    client_id: UUID
    kind: Literal["sight"]
    payload: SightPayload


FeedCreateRequest = Annotated[
    FeedCreateFood | FeedCreateKnowledge | FeedCreateEmotion | FeedCreatePromise | FeedCreateSight,
    Field(discriminator="kind"),
]


class FeedResource(_ForbidExtra):
    type: Literal["feed"] = "feed"
    id: UUID
    version: int = Field(ge=1)
    kind: FeedKind
    status: FeedStatus
    promise_status: Literal["active", "completed", "cancelled"] | None = None


class PromisePatchRequest(_ForbidExtra):
    client_id: UUID
    expected_version: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=200)
    remind_at: AwareDatetime


class PromiseActionRequest(_ForbidExtra):
    client_id: UUID
    expected_version: int = Field(ge=1)


class FeedPatch(_ForbidExtra):
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


class FeedMutationResult(_ForbidExtra):
    resource: FeedResource
    patch: FeedPatch
    quotas: list[QuotaUsage] = Field(default_factory=list)
    events: list[MutationEvent] = Field(default_factory=list)


class FeedSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: FeedMutationResult
    error: None = None
    request_id: str
    server_time: str


class FeedErrorEnvelope(_ForbidExtra):
    ok: Literal[False] = False
    data: None = None
    error: EnvelopeError
    request_id: str
    server_time: str
