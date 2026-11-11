"""Send rules that do not touch the database."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

_TEXT_LIMIT = 500


class ChatTextRejected(ValueError):
    """Empty or longer than one ask turn. Nothing is stored."""


def message_text(value: str) -> str:
    text = value.strip()
    if not text or len(text) > _TEXT_LIMIT:
        raise ChatTextRejected
    return text


def ordered_spirit_pair(left: UUID, right: UUID) -> tuple[UUID, UUID]:
    if left < right:
        return left, right
    return right, left


def send_hash(peer_spirit_id: UUID, text: str) -> str:
    payload = f"{peer_spirit_id}\n{text}".encode()
    return hashlib.sha256(payload).hexdigest()


def clear_hash(thread_id: UUID) -> str:
    return hashlib.sha256(f"clear\n{thread_id}".encode()).hexdigest()


@dataclass(frozen=True)
class ReadMark:
    thread_id: UUID
    last_read_seq: int
    clear_seq: int
    unread: int


@dataclass(frozen=True)
class ClearMark:
    thread_id: UUID
    clear_seq: int


def visible_seq(seq: int, clear_seq: int) -> bool:
    return seq > clear_seq


@dataclass(frozen=True)
class ThreadListRow:
    thread_id: UUID
    spirit_id: UUID
    display_name: str
    avatar_url: str
    available: bool
    text: str
    created_at: datetime
    unread: int


@dataclass(frozen=True)
class ChatMessageRow:
    id: UUID
    seq: int
    mine: bool
    text: str
    created_at: datetime


@dataclass(frozen=True)
class OpenThreadRow:
    thread_id: UUID
    spirit_id: UUID
    display_name: str
    avatar_url: str
    available: bool
    own_display_name: str
    own_avatar_url: str


@dataclass(frozen=True)
class OwnFace:
    spirit_id: UUID
    display_name: str
    avatar_url: str


@dataclass(frozen=True)
class OutboxDelivery:
    id: UUID
    recipient_user_id: UUID
    thread_id: UUID
    event_type: str
    seq: int
    event_body: str


def unread_count(peer_seqs: tuple[int, ...], *, last_read_seq: int, clear_seq: int) -> int:
    return sum(1 for seq in peer_seqs if seq > last_read_seq and seq > clear_seq)
