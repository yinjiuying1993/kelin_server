"""Pydantic schema boundary for JSONB columns used by chat and feed tables."""

from __future__ import annotations

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FoodPayload(_ForbidExtra):
    pass


class KnowledgePayload(_ForbidExtra):
    text: str = Field(min_length=1, max_length=10000)


class EmotionPayload(_ForbidExtra):
    emotion: Literal["calm", "happy", "tired", "anxious", "angry"]
    note: str | None = Field(default=None, max_length=200)


class PromisePayload(_ForbidExtra):
    text: str = Field(min_length=1, max_length=500)
    remind_at: AwareDatetime


class SightPhotoPayload(_ForbidExtra):
    source: Literal["photo"]


class SightLocationPayload(_ForbidExtra):
    source: Literal["location"]
    label: str = Field(min_length=1, max_length=80)
    city: str = Field(min_length=1, max_length=40)
    category: Literal["landmark", "park", "cafe", "school", "workplace", "other"] | None = None


SightPayload = Annotated[
    SightPhotoPayload | SightLocationPayload,
    Field(discriminator="source"),
]


class FeedFood(_ForbidExtra):
    kind: Literal["food"]
    payload: FoodPayload


class FeedKnowledge(_ForbidExtra):
    kind: Literal["knowledge"]
    payload: KnowledgePayload


class FeedEmotion(_ForbidExtra):
    kind: Literal["emotion"]
    payload: EmotionPayload


class FeedPromise(_ForbidExtra):
    kind: Literal["promise"]
    payload: PromisePayload


class FeedSight(_ForbidExtra):
    kind: Literal["sight"]
    payload: SightPayload


FeedDocument = Annotated[
    FeedFood | FeedKnowledge | FeedEmotion | FeedPromise | FeedSight,
    Field(discriminator="kind"),
]


class SourceRef(_ForbidExtra):
    """Verified citation summary only. Web refs are HTTPS URL + fetch time."""

    type: Literal["memory", "pact", "builtin_bank", "web"]
    id: UUID | None = None
    url: str | None = None
    fetched_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def shape_matches_type(self) -> Self:
        if self.type == "web":
            if self.id is not None or not self.url or not self.url.startswith("https://"):
                raise ValueError("web citation needs an https url and no id")
            return self
        if self.id is None or self.url is not None or self.fetched_at is not None:
            raise ValueError("local citation needs id only")
        return self


class GrowthEventPayload(_ForbidExtra):
    """Settlement scalars only; message body / prompt text are forbidden."""

    hunger_delta: int | None = None
    energy_delta: int | None = None
    mood_delta: int | None = None
    bond_delta: int | None = None
    closeness_delta: int | None = Field(default=None, ge=-2, le=2)
    curiosity_delta: int | None = Field(default=None, ge=-2, le=2)
    sharpness_delta: int | None = Field(default=None, ge=-2, le=2)
    nocturnal_delta: int | None = Field(default=None, ge=-2, le=2)
    stubborn_delta: int | None = Field(default=None, ge=-2, le=2)


_FEED_DOCUMENT_ADAPTER: TypeAdapter[FeedDocument] = TypeAdapter(FeedDocument)
_SOURCE_REFS_ADAPTER: TypeAdapter[list[SourceRef]] = TypeAdapter(list[SourceRef])


def parse_feed_payload(*, kind: str, payload: object) -> FeedDocument:
    return _FEED_DOCUMENT_ADAPTER.validate_python({"kind": kind, "payload": payload})


def parse_source_refs(value: object) -> list[SourceRef]:
    return _SOURCE_REFS_ADAPTER.validate_python(value)


def parse_growth_payload(value: object) -> GrowthEventPayload:
    return GrowthEventPayload.model_validate(value)
