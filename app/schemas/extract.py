"""POST /extract request/response contract. Spec §§8.3, 10.3, 16.2."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.envelope import EnvelopeError
from app.schemas.memory import MemoryPublic
from app.schemas.spirit import MutationEvent, MutationPatch, QuotaUsage

ExtractResourceStatus = Literal["processing", "extracted"]


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtractRequest(_ForbidExtra):
    client_id: UUID
    conversation_window_id: UUID = Field(
        description=(
            "Server-owned conversation window. Clients must not submit start/end message IDs "
            "as window bounds."
        )
    )


class ExtractResource(_ForbidExtra):
    type: Literal["extract"] = "extract"
    conversation_window_id: UUID
    status: ExtractResourceStatus
    memories: list[MemoryPublic] = Field(default_factory=list, max_length=2)


class ExtractResult(_ForbidExtra):
    resource: ExtractResource
    patch: MutationPatch
    quotas: list[QuotaUsage] = Field(default_factory=list)
    events: list[MutationEvent] = Field(default_factory=list)


class ExtractSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: ExtractResult
    error: None = None
    request_id: str
    server_time: str


class ExtractErrorEnvelope(_ForbidExtra):
    ok: Literal[False] = False
    data: None = None
    error: EnvelopeError
    request_id: str
    server_time: str
