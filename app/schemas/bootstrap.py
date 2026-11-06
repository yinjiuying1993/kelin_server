"""GET /bootstrap snapshot contract. Spec §§5.1, 9.1, 9.2."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.envelope import EnvelopeError
from app.schemas.room import RoomPublic
from app.schemas.spirit import QuotaUsage, SpiritPublic


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FeatureFlags(_ForbidExtra):
    remote_search: bool


class BootstrapSnapshot(_ForbidExtra):
    schema_version: Literal[2] = 2
    snapshot_version: int = Field(ge=0)
    api_version: Literal["v1"] = "v1"
    spirit: SpiritPublic | None
    room: RoomPublic | None
    latest_memories: list[Any] = Field(default_factory=list)
    unread_postcards: list[Any] = Field(default_factory=list)
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
