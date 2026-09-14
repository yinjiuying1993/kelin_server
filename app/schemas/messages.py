"""GET /messages page contract. Spec §§8.3, 10.2, 15.1.

System messages are never listed. Pagination is keyset; cursor is opaque.
"""

from __future__ import annotations

from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.envelope import EnvelopeError
from app.schemas.jsonb import SourceRef

MessageRole = Literal["user", "spirit"]
MessageSource = Literal["text", "voice", "onboarding"]
MessageStatus = Literal["accepted", "generated", "failed"]


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MessagePublic(_ForbidExtra):
    id: UUID
    client_id: UUID | None = Field(
        default=None,
        description="Present on user messages; null on spirit messages.",
    )
    role: MessageRole = Field(description="user or spirit. System messages are never listed.")
    content: str = Field(min_length=1, max_length=4000)
    source: MessageSource
    status: MessageStatus
    reply_to_message_id: UUID | None = Field(
        default=None,
        description="Spirit replies point at the user message. Null on user messages.",
    )
    source_refs: list[SourceRef] = Field(default_factory=list)
    created_at: str


class MessagePage(_ForbidExtra):
    items: list[MessagePublic]
    next_cursor: str | None = Field(
        default=None,
        description="Opaque keyset cursor. Null on the last page. Do not treat as SQL.",
    )
    has_more: bool
    snapshot_at: str = Field(
        description="ISO 8601 UTC upper bound for this paging session.",
    )

    @model_validator(mode="after")
    def cursor_matches_has_more(self) -> Self:
        if self.has_more and self.next_cursor is None:
            raise ValueError("next_cursor is required when has_more is true")
        if not self.has_more and self.next_cursor is not None:
            raise ValueError("next_cursor must be null on the last page")
        return self


class MessagePageSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: MessagePage
    error: None = None
    request_id: str
    server_time: str


class MessagePageErrorEnvelope(_ForbidExtra):
    ok: Literal[False] = False
    data: None = None
    error: EnvelopeError
    request_id: str
    server_time: str
