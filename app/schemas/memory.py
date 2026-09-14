"""GET/PATCH/DELETE /memories contract. Spec §§12.1–12.4, 14.11, 15.1."""

from __future__ import annotations

from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.providers.types import MemoryType
from app.schemas.envelope import EnvelopeError
from app.schemas.spirit import MutationEvent, MutationPatch, QuotaUsage

MemoryFilter = Literal["all", "relationship", "knowledge", "speech", "sight"]
MemoryStatus = Literal["active", "sealed", "deleted"]
MemoryPatchAction = Literal["correct", "seal"]
CLEAR_ALL_MEMORIES = "CLEAR_ALL_MEMORIES"


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemoryPublic(_ForbidExtra):
    id: UUID
    type: MemoryType
    summary: str = Field(min_length=1, max_length=500)
    tags: list[str] = Field(default_factory=list)
    salience: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    status: MemoryStatus
    version: int = Field(ge=1)
    created_at: str


class MemoryTombstone(_ForbidExtra):
    """Deleted memory sync record. Must not include summary or other body fields."""

    id: UUID
    deleted_at: str


class MemoryPage(_ForbidExtra):
    items: list[MemoryPublic]
    tombstones: list[MemoryTombstone] = Field(default_factory=list)
    next_cursor: str | None = Field(
        default=None,
        description="Opaque keyset cursor. Null on the last page. Do not treat as SQL.",
    )
    has_more: bool
    snapshot_at: str = Field(description="ISO 8601 UTC upper bound for this paging session.")

    @model_validator(mode="after")
    def cursor_matches_has_more(self) -> Self:
        if self.has_more and self.next_cursor is None:
            raise ValueError("next_cursor is required when has_more is true")
        if not self.has_more and self.next_cursor is not None:
            raise ValueError("next_cursor must be null on the last page")
        return self

    @model_validator(mode="after")
    def items_omit_deleted_body(self) -> Self:
        if any(item.status == "deleted" for item in self.items):
            raise ValueError("deleted memories must not appear in items")
        return self


class MemoryListQuery(_ForbidExtra):
    filter: MemoryFilter = Field(
        description=(
            "Page filter. relationship maps preference, relation, and emotion. "
            "Changing filter invalidates an old cursor."
        ),
    )
    cursor: str | None = Field(default=None, min_length=1)
    limit: int = Field(default=30, ge=1, le=50)


class MemoryPatchRequest(_ForbidExtra):
    client_id: UUID
    expected_version: int = Field(ge=1)
    action: MemoryPatchAction
    summary: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def summary_only_for_correct(self) -> Self:
        if self.action == "correct":
            if self.summary is None:
                raise ValueError("summary is required when action=correct")
            return self
        if self.summary is not None:
            raise ValueError("summary is only allowed when action=correct")
        return self


class MemoryDeleteRequest(_ForbidExtra):
    client_id: UUID
    expected_version: int = Field(ge=1)


class MemoryClearRequest(_ForbidExtra):
    client_id: UUID
    confirm: Literal["CLEAR_ALL_MEMORIES"] = Field(
        description="Must be exactly CLEAR_ALL_MEMORIES. Clear-all is online-only.",
    )


class MemoryResource(_ForbidExtra):
    type: Literal["memory"] = "memory"
    id: UUID
    version: int = Field(ge=1)
    status: MemoryStatus


class MemoryClearResource(_ForbidExtra):
    type: Literal["memory_clear"] = "memory_clear"
    id: UUID = Field(description="client_id of the clear-all request.")
    version: Literal[1] = 1


class MemoryMutationResult(_ForbidExtra):
    resource: MemoryResource | MemoryClearResource
    patch: MutationPatch
    quotas: list[QuotaUsage] = Field(default_factory=list)
    events: list[MutationEvent] = Field(default_factory=list)


class MemoryPageSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: MemoryPage
    error: None = None
    request_id: str
    server_time: str


class MemoryPageErrorEnvelope(_ForbidExtra):
    ok: Literal[False] = False
    data: None = None
    error: EnvelopeError
    request_id: str
    server_time: str


class MemoryMutationSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: MemoryMutationResult
    error: None = None
    request_id: str
    server_time: str


class MemoryMutationErrorEnvelope(_ForbidExtra):
    ok: Literal[False] = False
    data: None = None
    error: EnvelopeError
    request_id: str
    server_time: str
