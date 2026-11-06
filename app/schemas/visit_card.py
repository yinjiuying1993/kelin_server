"""Visit message card HTTP models."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VisitCardSaveRequest(_ForbidExtra):
    client_id: UUID
    body: str | None = None
    voice_key: str | None = None
    voice_seconds: int | None = Field(default=None, ge=1, le=30)
    voice_title: str | None = None
    image_key: str | None = None
    image_title: str | None = None
    image_caption: str | None = None


class VisitCardReplyRequest(_ForbidExtra):
    client_id: UUID
    body: str | None = None
    voice_key: str | None = None
    voice_seconds: int | None = Field(default=None, ge=1, le=30)
    voice_title: str | None = None
    image_key: str | None = None
    image_title: str | None = None
    image_caption: str | None = None
    reply_title: str | None = None


class VisitCardUploadRequest(_ForbidExtra):
    client_id: UUID
    kind: Literal["voice", "image"]
    mime: Literal["audio/mp4", "audio/mpeg", "image/jpeg", "image/png", "image/webp"]
    size_bytes: int = Field(ge=1, le=5 * 1024 * 1024)


class VisitCardDraft(_ForbidExtra):
    status: Literal["empty", "ready"]
    body: str | None = None
    voice_key: str | None = None
    voice_seconds: int | None = None
    voice_title: str | None = None
    image_key: str | None = None
    image_title: str | None = None
    image_caption: str | None = None
    client_id: UUID | None = None


class VisitCardPeer(_ForbidExtra):
    title: str
    stage: Literal["whelp", "formed", "awake"]


class VisitCardReply(_ForbidExtra):
    id: UUID
    status: Literal["open", "returned", "late"]
    body: str | None = None
    voice_seconds: int | None = None
    voice_title: str | None = None
    voice_url: str | None = None
    image_title: str | None = None
    image_caption: str | None = None
    image_url: str | None = None
    reply_title: str | None = None
    client_id: UUID


class VisitCardReplyResult(VisitCardReply):
    kind: Literal["visit", "late"]
    official: bool
    unread: bool
    occurred_at: str
    peer: VisitCardPeer
    is_default: bool
    reply_status: Literal["open", "returned", "late"]


class VisitCardSealed(_ForbidExtra):
    id: UUID
    kind: Literal["visit", "late"]
    status: Literal["sealed", "delivered"]
    is_default: bool
    official: bool
    unread: bool
    occurred_at: str
    peer: VisitCardPeer
    body: str | None = None
    voice_seconds: int | None = None
    voice_title: str | None = None
    voice_url: str | None = None
    image_title: str | None = None
    image_caption: str | None = None
    image_url: str | None = None
    reply_title: str | None = None
    reply_status: Literal["open", "returned", "late"] | None = None
    reply: VisitCardReply | None = None


class VisitFootprint(_ForbidExtra):
    id: UUID
    kind: Literal["visit", "late"]
    official: bool
    unread: bool
    occurred_at: str
    peer: VisitCardPeer
    is_default: bool
    body: str | None = None
    voice_seconds: int | None = None
    voice_title: str | None = None
    voice_url: str | None = None
    image_title: str | None = None
    image_caption: str | None = None
    image_url: str | None = None
    reply_title: str | None = None
    reply_status: Literal["open", "returned", "late"] | None = None


class VisitFootprintPage(_ForbidExtra):
    items: list[VisitFootprint]
    next_cursor: str | None
    has_more: bool


class VisitCardUploadResult(_ForbidExtra):
    object_key: str
    put_url: str
    expires_at: str


class VisitCardDraftEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: VisitCardDraft
    error: None = None
    request_id: str
    server_time: str


class VisitCardSealedEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: VisitCardSealed
    error: None = None
    request_id: str
    server_time: str


class VisitCardReplyEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: VisitCardReplyResult
    error: None = None
    request_id: str
    server_time: str


class VisitFootprintPageEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: VisitFootprintPage
    error: None = None
    request_id: str
    server_time: str


class VisitCardUploadEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: VisitCardUploadResult
    error: None = None
    request_id: str
    server_time: str


class VisitCardReadResult(_ForbidExtra):
    id: UUID
    unread: Literal[False] = False


class VisitCardReadEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: VisitCardReadResult
    error: None = None
    request_id: str
    server_time: str
