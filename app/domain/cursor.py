"""Opaque HMAC keyset cursor. Spec §§10.2, 15.1.

Payload is version, sort_time, id, snapshot_at, and filter_hash. Callers must
not treat the token as SQL.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

CURSOR_VERSION = 1
CURSOR_PREFIX = "kelin.msg.v1."
MEMORIES_CURSOR_PREFIX = "kelin.mem.v1."
FRIENDS_CURSOR_PREFIX = "kelin.friend.v1."
POSTCARDS_CURSOR_PREFIX = "kelin.card.v1."
_MESSAGES_FILTER = "messages:v1:exclude_system"
_MEMORIES_FILTER = "memories:v1"
_FRIENDS_FILTER = "friends:v1"
_POSTCARDS_FILTER = "postcards:v1"
_MAC_LENGTH = 32


class InvalidCursor(Exception):
    """Cursor is malformed, tampered, or not valid for this owner/filter."""


@dataclass(frozen=True, slots=True)
class MessageCursor:
    version: int
    sort_time: datetime
    id: UUID
    snapshot_at: datetime
    filter_hash: str


def messages_filter_hash(owner_id: UUID) -> str:
    material = f"{_MESSAGES_FILTER}:{owner_id}".encode("ascii")
    return hashlib.sha256(material).hexdigest()


def memories_filter_hash(owner_id: UUID, memory_filter: str) -> str:
    material = f"{_MEMORIES_FILTER}:{memory_filter}:{owner_id}".encode("ascii")
    return hashlib.sha256(material).hexdigest()


def friends_filter_hash(owner_id: UUID) -> str:
    material = f"{_FRIENDS_FILTER}:{owner_id}".encode("ascii")
    return hashlib.sha256(material).hexdigest()


def postcards_filter_hash(owner_id: UUID, *, unread_only: bool) -> str:
    flag = "unread" if unread_only else "all"
    material = f"{_POSTCARDS_FILTER}:{flag}:{owner_id}".encode("ascii")
    return hashlib.sha256(material).hexdigest()


def utc_iso(value: datetime) -> str:
    aware = value.astimezone(UTC)
    return aware.isoformat().replace("+00:00", "Z")


def encode_message_cursor(cursor: MessageCursor, *, secret: bytes) -> str:
    return _encode_keyset(cursor, secret=secret, prefix=CURSOR_PREFIX)


def encode_memory_cursor(cursor: MessageCursor, *, secret: bytes) -> str:
    return _encode_keyset(cursor, secret=secret, prefix=MEMORIES_CURSOR_PREFIX)


def decode_message_cursor(
    token: str,
    *,
    secret: bytes,
    expected_filter_hash: str,
) -> MessageCursor:
    return _decode_keyset(
        token,
        secret=secret,
        expected_filter_hash=expected_filter_hash,
        prefix=CURSOR_PREFIX,
    )


def decode_memory_cursor(
    token: str,
    *,
    secret: bytes,
    expected_filter_hash: str,
) -> MessageCursor:
    return _decode_keyset(
        token,
        secret=secret,
        expected_filter_hash=expected_filter_hash,
        prefix=MEMORIES_CURSOR_PREFIX,
    )


def encode_friend_cursor(cursor: MessageCursor, *, secret: bytes) -> str:
    return _encode_keyset(cursor, secret=secret, prefix=FRIENDS_CURSOR_PREFIX)


def decode_friend_cursor(
    token: str,
    *,
    secret: bytes,
    expected_filter_hash: str,
) -> MessageCursor:
    return _decode_keyset(
        token,
        secret=secret,
        expected_filter_hash=expected_filter_hash,
        prefix=FRIENDS_CURSOR_PREFIX,
    )


def encode_postcard_cursor(cursor: MessageCursor, *, secret: bytes) -> str:
    return _encode_keyset(cursor, secret=secret, prefix=POSTCARDS_CURSOR_PREFIX)


def decode_postcard_cursor(
    token: str,
    *,
    secret: bytes,
    expected_filter_hash: str,
) -> MessageCursor:
    return _decode_keyset(
        token,
        secret=secret,
        expected_filter_hash=expected_filter_hash,
        prefix=POSTCARDS_CURSOR_PREFIX,
    )


def _encode_keyset(cursor: MessageCursor, *, secret: bytes, prefix: str) -> str:
    if cursor.version != CURSOR_VERSION:
        raise InvalidCursor
    body = json.dumps(
        {
            "f": cursor.filter_hash,
            "i": str(cursor.id),
            "s": utc_iso(cursor.snapshot_at),
            "t": utc_iso(cursor.sort_time),
            "v": cursor.version,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    mac = hmac.new(secret, body, hashlib.sha256).digest()
    packed = urlsafe_b64encode(body + mac).rstrip(b"=").decode("ascii")
    return f"{prefix}{packed}"


def _decode_keyset(
    token: str,
    *,
    secret: bytes,
    expected_filter_hash: str,
    prefix: str,
) -> MessageCursor:
    if not token.startswith(prefix):
        raise InvalidCursor
    packed = token.removeprefix(prefix)
    try:
        raw = _b64url_decode(packed)
    except (ValueError, OSError) as exc:
        raise InvalidCursor from exc
    if len(raw) <= _MAC_LENGTH:
        raise InvalidCursor
    body = raw[:-_MAC_LENGTH]
    mac = raw[-_MAC_LENGTH:]
    expected = hmac.new(secret, body, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        raise InvalidCursor
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidCursor from exc
    if not isinstance(payload, dict):
        raise InvalidCursor
    if set(payload.keys()) != {"v", "t", "i", "s", "f"}:
        raise InvalidCursor
    if payload.get("v") != CURSOR_VERSION:
        raise InvalidCursor
    filter_hash = payload.get("f")
    if not isinstance(filter_hash, str) or filter_hash != expected_filter_hash:
        raise InvalidCursor
    try:
        message_id = UUID(str(payload.get("i")))
        sort_time = _parse_utc(payload.get("t"))
        snapshot_at = _parse_utc(payload.get("s"))
    except (TypeError, ValueError) as exc:
        raise InvalidCursor from exc
    if sort_time > snapshot_at:
        raise InvalidCursor
    return MessageCursor(
        version=CURSOR_VERSION,
        sort_time=sort_time,
        id=message_id,
        snapshot_at=snapshot_at,
        filter_hash=filter_hash,
    )


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise InvalidCursor
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise InvalidCursor
    return parsed.astimezone(UTC)


def _b64url_decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return urlsafe_b64decode(value + padding)
