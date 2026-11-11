"""Read models for spirit chat. No user primary key."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.envelope import EnvelopeError


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SpiritChatListItem(_ForbidExtra):
    thread_id: UUID
    spirit_id: UUID
    display_name: str = Field(min_length=1, max_length=20)
    avatar_url: str = Field(min_length=1, max_length=300)
    peer_available: bool
    text: str = Field(min_length=1, max_length=500)
    created_at: str = Field(min_length=1)
    unread: int = Field(ge=0)


class SpiritChatList(_ForbidExtra):
    items: list[SpiritChatListItem]
    unread_total: int = Field(ge=0)
    has_more: bool
    next_cursor: str | None = None


class SpiritChatMessage(_ForbidExtra):
    id: UUID
    seq: int = Field(ge=1)
    mine: bool
    text: str = Field(min_length=1, max_length=500)
    created_at: str = Field(min_length=1)


class SpiritChatMessagePage(_ForbidExtra):
    items: list[SpiritChatMessage]
    has_more: bool
    next_cursor: str | None = None


class SpiritChatWindow(_ForbidExtra):
    thread_id: UUID | None = None
    spirit_id: UUID
    display_name: str = Field(min_length=1, max_length=20)
    avatar_url: str = Field(min_length=1, max_length=300)
    peer_available: bool
    own_display_name: str = Field(min_length=1, max_length=20)
    own_avatar_url: str = Field(min_length=1, max_length=300)
    messages: list[SpiritChatMessage]
    has_more: bool
    next_cursor: str | None = None


class SpiritChatListEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: SpiritChatList
    error: None = None
    request_id: str
    server_time: str


class SpiritChatWindowEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: SpiritChatWindow
    error: None = None
    request_id: str
    server_time: str


class SpiritChatMessagePageEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: SpiritChatMessagePage
    error: None = None
    request_id: str
    server_time: str


class SpiritChatErrorEnvelope(_ForbidExtra):
    ok: Literal[False] = False
    data: None = None
    error: EnvelopeError
    request_id: str
    server_time: str
